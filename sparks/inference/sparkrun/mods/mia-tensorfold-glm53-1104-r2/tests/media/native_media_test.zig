//! Production prompt rendering, API translators and GLM preprocessing; only the GPU tower is outside this CPU gate.
const std = @import("std");
const json = @import("json");
const api = @import("engine_api");
const model = @import("model_text.zig");
const images = @import("images.zig");
const prompt = @import("prompt.zig");
const order = @import("media_order.zig");
const HfText = @import("hf_text.zig").HfText;
const Server = @import("server.zig").Server;
const Cx = @import("errors.zig").Cx;
const glm = @import("glm_media");
const t = std.testing;
const a_png = @embedFile("media-fixtures/a.png");
const b_png = @embedFile("media-fixtures/b.png");
const config: glm.Config = .{ .image_token = 1012, .image_start = 2, .image_end = 3, .mean = @splat(0.5), .std = @splat(0.5), .min_tokens = 16, .max_tokens = 2048 };

fn value(a: std.mem.Allocator, text: []const u8) !json.Value {
    return (try json.parse(a, text)).ok;
}
fn url(a: std.mem.Allocator, bytes: []const u8) ![]const u8 {
    const enc = std.base64.standard.Encoder;
    const buf = try a.alloc(u8, enc.calcSize(bytes.len));
    return std.fmt.allocPrint(a, "data:image/png;base64,{s}", .{enc.encode(buf, bytes)});
}
fn part(a: std.mem.Allocator, bytes: []const u8) !json.Value {
    const p = try value(a, "{\"type\":\"image_url\",\"image_url\":{}}");
    try p.get("image_url").?.object.put(a, "url", .{ .string = try url(a, bytes) });
    return p;
}
fn prepare(_: *anyopaque, a: std.mem.Allocator, bytes: []const u8, cap: u32) !api.PreparedImage {
    const p = try glm.image.plan(t.io, a, bytes, config, cap);
    return .{ .pixels = p.pixels, .gh = p.gh, .gw = p.gw, .tokens = p.tokens, .hash = p.hash, .source_pixels = p.source_pixels, .deferred = if (p.deferred) |d| .{ .ctx = d.ctx, .prepare = d.prepare } else null };
}
fn vision(ctx: *anyopaque) api.Vision {
    return .{ .ctx = ctx, .prepare = prepare, .image_token = 1012, .markers = &.{1012}, .image_tokens = 102400, .max_images = 50, .image_limits = .{ .io = t.io, .per_image_tokens = 2048, .max_request_bytes = 64 << 20, .max_request_pixels = 400 << 20, .prepare_seconds = 100, .render_order = true } };
}

const Fixture = struct {
    files: @import("control_tokens_fixture.zig").Fixture,
    hf: *HfText,
    fn init(a: std.mem.Allocator) !Fixture {
        var f = try @import("control_tokens_fixture.zig").Fixture.init(a, 1);
        errdefer f.deinit();
        try f.tmp.dir.writeFile(t.io, .{ .sub_path = "chat_template.jinja", .data = @embedFile("native_media_template.jinja") });
        try f.tmp.dir.writeFile(t.io, .{ .sub_path = "config.json", .data = "{\"model_type\":\"glm5_next\"}" });
        var problem: []const u8 = "";
        const hf = HfText.load(t.allocator, t.io, f.path, null, a, &problem) catch |e| {
            std.debug.print("HfText: {s}\n", .{problem});
            return e;
        };
        return .{ .files = f, .hf = hf };
    }
    fn deinit(f: *Fixture) void {
        f.hf.deinit();
        f.files.deinit();
    }
};

fn rendered(a: std.mem.Allocator, hf: *HfText, messages: json.Value, v: api.Vision) !prompt.Rendered {
    var srv: Server = undefined;
    srv.text = hf.text();
    var visual = v;
    visual.image_token = hf.text().tokenId("<|image|>").?;
    visual.markers = &.{};
    srv.info = .{ .vision = visual };
    srv.late_system = "system";
    srv.needs_user_after_tool = false;
    srv.config = .{ .served_name = "fixture", .model_ids = &.{"fixture"} };
    srv.io = t.io;
    var cx: Cx = .{ .a = a };
    return prompt.prepare(&srv, &cx, .{ .messages = messages, .fields = .null }, true, null) catch |e| {
        std.debug.print("prompt refusal: {s}\n", .{cx.message});
        return e;
    };
}

const assistant = "{\"role\":\"assistant\",\"content\":\"\",\"tool_calls\":[{\"id\":\"A\",\"type\":\"function\",\"function\":{\"name\":\"lookup\",\"arguments\":\"{}\"}},{\"id\":\"B\",\"type\":\"function\",\"function\":{\"name\":\"lookup\",\"arguments\":\"{}\"}}]}";

fn openai(a: std.mem.Allocator) !json.Value {
    const msgs = try value(a, "[{\"role\":\"user\",\"content\":\"look\"}," ++ assistant ++ ",{\"role\":\"tool\",\"tool_call_id\":\"B\",\"content\":[]},{\"role\":\"tool\",\"tool_call_id\":\"A\",\"content\":[]}]");
    const b = try a.alloc(json.Value, 1);
    b[0] = try part(a, b_png);
    const aa = try a.alloc(json.Value, 1);
    aa[0] = try part(a, a_png);
    try msgs.array[2].object.put(a, "content", .{ .array = b });
    try msgs.array[3].object.put(a, "content", .{ .array = aa });
    return msgs;
}
fn anthropic(a: std.mem.Allocator, cx: *Cx) !json.Value {
    const body = try value(a,
        \\{"model":"m","max_tokens":32,"messages":[{"role":"user","content":"look"},{"role":"assistant","content":[{"type":"tool_use","id":"A","name":"lookup","input":{}},{"type":"tool_use","id":"B","name":"lookup","input":{}}]},{"role":"user","content":[{"type":"tool_result","tool_use_id":"B","content":[]},{"type":"tool_result","tool_use_id":"A","content":[]}]}]}
    );
    for ([_][]const u8{ b_png, a_png }, 0..) |bytes, i| {
        const p = try value(a, "{\"type\":\"image\",\"source\":{\"type\":\"url\"}}");
        try p.get("source").?.object.put(a, "url", .{ .string = try url(a, bytes) });
        const content = try a.alloc(json.Value, 1);
        content[0] = p;
        try body.get("messages").?.array[2].get("content").?.array[i].object.put(a, "content", .{ .array = content });
    }
    return (try @import("anthropic_translate.zig").translate(cx, body, false)).get("messages").?;
}
fn responses(a: std.mem.Allocator, cx: *Cx) !json.Value {
    const items = try value(a,
        \\[{"type":"message","role":"user","content":"look"},{"type":"function_call","call_id":"A","name":"lookup","arguments":"{}"},{"type":"function_call","call_id":"B","name":"lookup","arguments":"{}"},{"type":"function_call_output","call_id":"B","output":[]},{"type":"function_call_output","call_id":"A","output":[]}]
    );
    for ([_][]const u8{ b_png, a_png }, 0..) |bytes, i| {
        const p = try value(a, "{\"type\":\"input_image\"}");
        try p.object.put(a, "image_url", .{ .string = try url(a, bytes) });
        const content = try a.alloc(json.Value, 1);
        content[0] = p;
        try items.array[i + 3].object.put(a, "output", .{ .array = content });
    }
    return .{ .array = try @import("responses_translate.zig").messages(cx, items.array) };
}

test "native media parity: equal-sized distinct reversed tool images retain A B occurrence order on all APIs" {
    var arena = std.heap.ArenaAllocator.init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var f = try Fixture.init(a);
    defer f.deinit();
    var cx: Cx = .{ .a = a };
    var dummy: u8 = 0;
    const pa = try glm.image.plan(t.io, a, a_png, config, 2048);
    const pb = try glm.image.plan(t.io, a, b_png, config, 2048);
    try t.expect(pa.hash != pb.hash);
    try t.expectEqual(pa.tokens, pb.tokens);
    for ([_]json.Value{ try openai(a), try anthropic(a, &cx), try responses(a, &cx) }) |msgs| {
        const x = try rendered(a, f.hf, msgs, vision(&dummy));
        try t.expectEqual(@as(usize, 2), x.images.len);
        try t.expectEqual(pa.hash, x.images[0].hash);
        try t.expectEqual(pb.hash, x.images[1].hash);
        try t.expect(x.images[0].at < x.images[1].at);
        const pixels_a = try x.images[0].materialize(a);
        const pixels_b = try x.images[1].materialize(a);
        try t.expect(pixels_a[0] > pixels_b[0]);
        try t.expectEqual(@as(usize, 0), x.images[0].pixels.len);
    }
}

const BudgetProbe = struct {
    n: usize = 0,
    pixels: u64 = 1,
    fn run(ctx: *anyopaque, _: std.mem.Allocator, _: []const u8, cap: u32) !api.PreparedImage {
        const self: *BudgetProbe = @ptrCast(@alignCast(ctx));
        try t.expectEqual(@as(u32, 2048), cap);
        self.n += 1;
        return .{ .pixels = &.{}, .gh = 64, .gw = 128, .tokens = 2048, .hash = self.n, .source_pixels = self.pixels };
    }
};
fn fifty(a: std.mem.Allocator, n: usize) !json.Value {
    const ps = try a.alloc(json.Value, n);
    for (ps) |*p| p.* = try part(a, a_png);
    const m = try value(a, "[{\"role\":\"user\"}]");
    try m.array[0].object.put(a, "content", .{ .array = ps });
    return m;
}

test "native media parity: 50 occurrences keep 2048 each and an unchanged history image budget" {
    var arena = std.heap.ArenaAllocator.init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var probe: BudgetProbe = .{};
    var v = vision(&probe);
    v.prepare = BudgetProbe.run;
    var cx: Cx = .{ .a = a };
    const ids = try a.alloc(u32, 50);
    @memset(ids, 1012);
    const one = (try images.expand(v, null, images.MarkText.text(), &cx, try fifty(a, 1), ids[0..1], 1)).?;
    probe.n = 0;
    const all = (try images.expand(v, null, images.MarkText.text(), &cx, try fifty(a, 50), ids, 1)).?;
    try t.expectEqual(@as(usize, 102400), all.ids.len);
    try t.expectEqual(one.images[0].tokens, all.images[0].tokens);
    try t.expectEqual(@as(usize, 2048), all.history_len);
    try t.expectEqual(@as(usize, 50), probe.n);
    v.image_tokens = 102399;
    try t.expectError(error.Refused, images.expand(v, null, images.MarkText.text(), &cx, try fifty(a, 50), ids, 1));
    try t.expectEqualStrings("the request's images exceed the configured total visual-token budget", cx.message);
    try t.expectError(error.Refused, images.expand(v, null, images.MarkText.text(), &cx, try fifty(a, 51), &@as([51]u32, @splat(1012)), 1));
}

test "native media parity: aggregate pixels count every occurrence including repeated images" {
    var arena = std.heap.ArenaAllocator.init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var probe: BudgetProbe = .{ .pixels = 16 << 20 };
    var v = vision(&probe);
    v.prepare = BudgetProbe.run;
    var cx: Cx = .{ .a = a };
    const ids: [26]u32 = @splat(1012);
    _ = try images.expand(v, null, images.MarkText.text(), &cx, try fifty(a, 25), ids[0..25], 0);
    try t.expectError(error.Refused, images.expand(v, null, images.MarkText.text(), &cx, try fifty(a, 26), &ids, 0));
    try t.expectEqualStrings("the request's images exceed the configured decoded pixel limit", cx.message);
}

test "native media parity: GLM uses Torch resize fixtures and lazy plans match eager preparation" {
    const source = @embedFile("media-fixtures/resize-source.rgb");
    const up = try glm.picture.resample.resize(t.allocator, .torch, .of(source, 7, 5), 39, 28);
    defer t.allocator.free(up);
    try t.expectEqualSlices(u8, @embedFile("media-fixtures/resize-up.rgb"), up);
    const down = try glm.picture.resample.resize(t.allocator, .torch, .of(source, 7, 5), 3, 2);
    defer t.allocator.free(down);
    try t.expectEqualSlices(u8, @embedFile("media-fixtures/resize-down.rgb"), down);
    var arena = std.heap.ArenaAllocator.init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const lazy = try glm.image.plan(t.io, a, a_png, config, 2048);
    const eager = try glm.image.prepare(t.io, a, a_png, config, 2048);
    try t.expectEqual(@as(usize, 0), lazy.pixels.len);
    try t.expectEqual(@as(u64, 35), lazy.source_pixels);
    try t.expectEqual(eager.hash, lazy.hash);
    try t.expectEqual(eager.tokens, lazy.tokens);
    const d = lazy.deferred.?;
    const actual = try d.prepare(d.ctx, a);
    try t.expectEqualSlices(f32, eager.pixels, actual);
    try t.expectError(error.Oversized, glm.picture.fits(8193, 1));
    try t.expectError(error.Oversized, glm.picture.fits(4097, 4096));
    try glm.picture.fits(4096, 4096);
    try t.expectError(error.Oversized, glm.image.plan(t.io, a, try a.alloc(u8, (10 << 20) + 1), config, 2048));
}

test "native media parity: GLM video clips have independent 16384 ceilings and actual rows share 32768" {
    const Fake = struct {
        fn prepare(_: *anyopaque, _: std.mem.Allocator, _: api.VideoSource, _: u32, _: u32) !api.PreparedVideo {
            return error.Unused;
        }
        fn decode(_: *anyopaque, _: []const u32, _: *glm.video.Sink) !void {
            return error.Unused;
        }
    };
    const o: api.VideoOffer = .{ .prepare = Fake.prepare, .video_token = 8, .group_open = 6, .group_close = 7, .video_tokens = 32768, .per_video_tokens = 16384, .max_videos = 4, .max_frames = 128 };
    for (1..5) |n| try t.expectEqual(@as(u32, 16384), images.videoTokenBudget(o, n));
    var arena = std.heap.ArenaAllocator.init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var cx: Cx = .{ .a = a };
    var total: u64 = 0;
    var groups = [_]api.PreparedImage{.{ .pixels = &.{}, .gh = 64, .gw = 512, .tokens = 8192, .hash = 1 }};
    const clip: api.PreparedVideo = .{ .groups = &groups, .times = &.{} };
    for (0..4) |_| try images.accountVideo(&cx, o, clip, 16384, &total);
    try t.expectEqual(@as(u64, 32768), total);
    try t.expectError(error.Refused, images.accountVideo(&cx, o, clip, 16384, &total));
    groups[0].tokens = 16385;
    total = 0;
    try t.expectError(error.Refused, images.accountVideo(&cx, o, clip, 16384, &total));
    var dummy: u8 = 0;
    const plan = try glm.video.plan(a, .{ .frames = 7200, .rate_num = 30, .rate_den = 1, .width = 1920, .height = 1080, .ctx = &dummy, .decode = Fake.decode }, config, 16384, 128);
    try t.expect(plan.indices.len <= 128);
    try t.expect(plan.pairs() * plan.tokensPerPair() <= 16384);
}

test "native media parity: 64 MiB aggregate encoded bytes are enforced even when the fetch exceeds its bound" {
    var arena = std.heap.ArenaAllocator.init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const Source = struct {
        data: []const u8,
        last_limit: usize = 0,
        fn get(ctx: ?*anyopaque, _: std.mem.Allocator, _: std.Io, _: []const u8, max: usize, _: std.Io.Timestamp, _: []const []const u8, _: *@import("media_fetch.zig").Failure) @import("media_fetch.zig").Error![]const u8 {
            const self: *@This() = @ptrCast(@alignCast(ctx.?));
            self.last_limit = max;
            return self.data;
        }
    };
    const data = try a.alloc(u8, 10 << 20);
    @memset(data, 0);
    @memcpy(data[0..8], "\x89PNG\r\n\x1a\n");
    var source: Source = .{ .data = data };
    var probe: BudgetProbe = .{};
    var v = vision(&probe);
    v.prepare = BudgetProbe.run;
    var cx: Cx = .{ .a = a };
    const msgs = try fifty(a, 7);
    for (msgs.array[0].get("content").?.array) |p| try p.get("image_url").?.object.put(a, "url", .{ .string = "https://example.com/a.png" });
    const fetch: images.Fetch = .{ .ctx = &source, .io = t.io, .get = Source.get };
    try t.expectError(error.Refused, images.expand(v, fetch, images.MarkText.text(), &cx, msgs, &@as([7]u32, @splat(1012)), 0));
    try t.expectEqual(@as(usize, 6), probe.n);
    try t.expectEqual(@as(usize, 4 << 20), source.last_limit);
}

test "native media parity: preparation time is bounded after processing and request labels cannot impersonate occurrences" {
    var arena = std.heap.ArenaAllocator.init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const Clock = struct {
        calls: usize = 0,
        fn now(ctx: ?*anyopaque, _: std.Io.Clock) std.Io.Timestamp {
            const self: *@This() = @ptrCast(@alignCast(ctx.?));
            self.calls += 1;
            return .{ .nanoseconds = if (self.calls > 3) 101 * std.time.ns_per_s else 0 };
        }
    };
    var clock: Clock = .{};
    var vtable = t.io.vtable.*;
    vtable.now = Clock.now;
    var probe: BudgetProbe = .{};
    var v = vision(&probe);
    v.prepare = BudgetProbe.run;
    v.image_limits.?.io = .{ .userdata = &clock, .vtable = &vtable };
    var cx: Cx = .{ .a = a };
    try t.expectError(error.Refused, images.expand(v, null, images.MarkText.text(), &cx, try fifty(a, 1), &.{1012}, 0));
    try t.expectEqual(@as(usize, 1), probe.n);
    try t.expectEqualStrings("image preparation exceeded the configured time limit", cx.message);
    const msgs = try openai(a);
    try msgs.array[0].object.put(a, "content", .{ .string = "\x00tf-media-0:0\x00" });
    const tagged = try order.tag(&cx, msgs, "image_url");
    try t.expect(std.mem.indexOf(u8, tagged.labels[0], "tf-media-1:") != null);
    try t.expect(!std.mem.eql(u8, tagged.labels[0], tagged.labels[1]));
}

test "native media parity: real PNG decoder pixels and GLM Torch patchification match independent fixtures" {
    var arena = std.heap.ArenaAllocator.init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const decoded = try glm.picture.decode(t.io, a, a_png);
    try t.expectEqual(@as(usize, 7), decoded.width);
    try t.expectEqual(@as(usize, 5), decoded.height);
    for (0..35) |i| try t.expectEqualSlices(u8, &.{ 231, 17, 43 }, decoded.pixels[i * 3 ..][0..3]);
    var c = config;
    c.min_tokens = 1;
    const prepared = try glm.image.prepare(t.io, a, @embedFile("media-fixtures/pattern.png"), c, 2048);
    try t.expectEqual(@as(u32, 2), prepared.gh);
    try t.expectEqual(@as(u32, 2), prepared.gw);
    const reference = @embedFile("media-fixtures/glm-pattern-patches.f32le");
    try t.expectEqual(prepared.pixels.len * 4, reference.len);
    for (prepared.pixels, 0..) |actual, i| {
        const expected: f32 = @bitCast(std.mem.readInt(u32, reference[i * 4 ..][0..4], .little));
        try t.expectApproxEqAbs(expected, actual, 1e-6);
    }
}
