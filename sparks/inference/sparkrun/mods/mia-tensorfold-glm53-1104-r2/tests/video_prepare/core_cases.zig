//! Recording real video sources, actual retained/scratch allocators, and the production pair materializer.
const std = @import("std");
const api = @import("engine_api");
const glm = @import("glm_media");
const video = glm.video;
const memory = @import("prepare_memory.zig");
const t = std.testing;
const config: glm.Config = .{ .image_token = 1, .image_start = 2, .image_end = 3, .mean = .{ 0.48, 0.45, 0.40 }, .std = .{ 0.26, 0.27, 0.28 }, .min_tokens = 16, .max_tokens = 16384 };

fn image(pair: video.Pair) api.Image {
    const d = pair.deferred.?;
    return .{ .pixels = pair.pixels, .deferred = .{ .ctx = d.ctx, .prepare = d.prepare, .checked = d.checked }, .gh = pair.gh, .gw = pair.gw, .at = 0, .tokens = pair.tokens, .hash = pair.hash };
}

const Recording = struct {
    clip: api.tfvideo.Video,
    calls: usize = 0,
    bytes: usize = 0,
    count: usize = 0,
    rss_peak: u64 = 0,
    legacy_calls: usize = 0,
    legacy_bytes: usize = 0,
    fn source(s: *Recording) video.Source {
        const v = s.clip.info;
        return .{ .frames = @intCast(v.frames), .rate_num = @intCast(v.rate_num), .rate_den = @intCast(v.rate_den), .width = @intCast(v.width), .height = @intCast(v.height), .ctx = s, .decode = decode };
    }
    fn decode(ctx: *anyopaque, indices: []const u32, sink: *video.Sink) anyerror!void {
        const r: *Recording = @ptrCast(@alignCast(ctx));
        r.calls += 1;
        const Into = struct {
            r: *Recording,
            sink: *video.Sink,
            pub fn take(x: @This(), k: usize, rgb: []const u8, w: u32, h: u32, stride: usize) !void {
                x.r.bytes += rgb.len;
                x.r.count += 1;
                if (@import("process_resident.zig").read(t.io)) |rss| x.r.rss_peak = @max(x.r.rss_peak, rss.peak_bytes orelse 0);
                try x.sink.take(k, rgb, w, h, stride);
            }
        };
        try r.clip.framesWithScratch(sink.scratch, indices, Into{ .r = r, .sink = sink });
    }
};

test "video prepare real: candidate library decodes each clip once and every patch equals frozen eager" {
    try t.expectEqualStrings("9.0.2", api.tfvideo.version().?);
    inline for (.{ "h264.mp4", "hevc.mp4", "vp8.webm", "vp9.webm", "mpeg4.mp4" }) |name| {
        var held: memory.Tracked = .{ .child = t.allocator };
        var scratch: memory.Tracked = .{ .child = t.allocator };
        var arena: std.heap.ArenaAllocator = .init(held.allocator());
        var rec: Recording = .{ .clip = try api.tfvideo.Video.open(@embedFile("video-fixtures/" ++ name)) };
        const out = try video.prepareLazyWithScratch(arena.allocator(), scratch.allocator(), rec.source(), config, 16384, 128);
        const decoded = rec.bytes;
        try t.expectEqual(@as(usize, 1), rec.calls);
        try t.expect(decoded > 0);
        try t.expectEqual(@as(usize, 0), scratch.count.live.load(.monotonic));
        var eager_arena: std.heap.ArenaAllocator = .init(t.allocator);
        defer eager_arena.deinit();
        const eager = try glm.eager.prepare(eager_arena.allocator(), .{ .frames = rec.source().frames, .rate_num = rec.source().rate_num, .rate_den = rec.source().rate_den, .width = rec.source().width, .height = rec.source().height, .ctx = &rec, .decode = eagerDecode }, config, 16384, 128);
        rec.clip.close(); // the prepared callbacks must survive the source and its open container
        rec.calls = 1;
        rec.bytes = decoded;
        const retained = held.count.live.load(.monotonic);
        for (out.groups, eager.groups) |g, ref| {
            try t.expectEqual(@as(usize, 0), g.pixels.len);
            try t.expectEqual(ref.tokens, g.tokens);
            try t.expectEqual(ref.gh, g.gh);
            try t.expectEqual(ref.gw, g.gw);
            // Repeat and straddling windows may independently materialize the same pair.
            for (0..3) |_| {
                const px = try image(g).materialize(scratch.allocator());
                try t.expectEqualSlices(f32, ref.pixels, px);
                scratch.allocator().free(px);
            }
        }
        try t.expectEqualSlices(f64, eager.times, out.times);
        try t.expectEqual(@as(usize, 1), rec.calls);
        try t.expectEqual(decoded, rec.bytes);
        try t.expectEqual(retained, held.count.live.load(.monotonic));
        try t.expectEqual(@as(usize, 0), scratch.count.live.load(.monotonic));
        std.debug.print("VIDEO_REAL codec={s} groups={d} calls={d} decoded_bytes={d} repeat_decoded_bytes=0 retained_peak={d} scratch_peak={d} process_rss_peak={d}\n", .{ name, out.groups.len, rec.calls, rec.bytes, held.count.peak.load(.monotonic), scratch.count.peak.load(.monotonic), rec.rss_peak });
        arena.deinit();
        try t.expectEqual(@as(usize, 0), held.count.live.load(.monotonic));
    }
}

fn eagerDecode(ctx: *anyopaque, indices: []const u32, sink: *glm.eager.Sink) anyerror!void {
    const r: *Recording = @ptrCast(@alignCast(ctx));
    r.legacy_calls += 1;
    const Into = struct {
        r: *Recording,
        sink: *glm.eager.Sink,
        pub fn take(x: @This(), k: usize, rgb: []const u8, w: u32, h: u32, stride: usize) !void {
            x.r.legacy_bytes += rgb.len;
            try x.sink.take(k, rgb, w, h, stride);
        }
    };
    try r.clip.frames(indices, Into{ .r = r, .sink = sink });
}

const Frames = struct {
    calls: usize = 0,
    count: usize = 128,
    width: u32 = 448,
    height: u32 = 224,
    mode: enum { valid, short, stride, bytes, dimensions, order, fail, cancel } = .valid,
    salt: u8 = 0,
    fn source(s: *Frames) video.Source {
        return .{ .identity = 99, .frames = s.count, .rate_num = 2, .rate_den = 1, .width = s.width, .height = s.height, .ctx = s, .decode = decode };
    }
    fn decode(ctx: *anyopaque, indices: []const u32, sink: *video.Sink) anyerror!void {
        const s: *Frames = @ptrCast(@alignCast(ctx));
        s.calls += 1;
        const pixels = try sink.scratch.alloc(u8, @as(usize, s.width) * s.height * 3);
        defer sink.scratch.free(pixels);
        for (indices, 0..) |idx, k| {
            @memset(pixels, @truncate(idx + s.salt));
            if (k + 1 == indices.len) switch (s.mode) {
                .short => return,
                .fail => return error.Decode,
                .cancel => return error.Cancelled,
                .stride => return sink.take(k, pixels, s.width, s.height, s.width * 3 - 1),
                .bytes => return sink.take(k, pixels[0 .. pixels.len - 1], s.width, s.height, s.width * 3),
                .dimensions => return sink.take(k, pixels, s.width + 1, s.height, s.width * 3),
                .order => return sink.take(k + 1, pixels, s.width, s.height, s.width * 3),
                .valid => {},
            };
            try sink.take(k, pixels, s.width, s.height, s.width * 3);
        }
    }
};

test "video prepare: four clips hit the checked 147 MiB payload bound with actual allocator cleanup" {
    var ledger = memory.Ledger.init(t.allocator);
    const request = try memory.Request.create(t.allocator, t.io, &ledger, null, 0);
    var frames: Frames = .{};
    var bytes: usize = 0;
    var tokens: usize = 0;
    for (0..4) |_| {
        const out = try video.prepareLazyWithScratch(request.arena.allocator(), ledger.of(.media).allocator(), frames.source(), config, 8192, 128);
        try t.expectEqual(@as(usize, 64), out.groups.len);
        for (out.groups) |g| {
            try t.expectEqual(@as(usize, 0), g.pixels.len);
            tokens += g.tokens;
        }
        var plans: std.heap.ArenaAllocator = .init(t.allocator);
        defer plans.deinit();
        const plan = try video.plan(plans.allocator(), frames.source(), config, 8192, 128);
        bytes += try plan.retainedBytes(8192);
    }
    try t.expectEqual(@as(usize, 32768), tokens);
    try t.expectEqual(@as(usize, 147 << 20), bytes);
    try t.expectEqual(@as(usize, 4), frames.calls);
    try t.expectEqual(@as(usize, 0), ledger.of(.media).count.live.load(.monotonic));
    const retained = ledger.of(.retained).count.live.load(.monotonic);
    try t.expect(retained >= bytes);
    // Arena capacity includes its growth slack and small plans; measure it rather than reporting payload as memory.
    try t.expect(retained < bytes * 2);
    std.debug.print("VIDEO_BOUND tokens={d} payload={d} retained_actual={d} retained_peak={d} scratch_peak={d}\n", .{ tokens, bytes, retained, ledger.of(.retained).count.peak.load(.monotonic), ledger.of(.media).count.peak.load(.monotonic) });
    request.release();
    for (&ledger.categories) |*category| try t.expectEqual(@as(usize, 0), category.count.live.load(.monotonic));
}

test "video prepare: bad final frames and cancellation fail during preparation with all scratch released" {
    for ([_]@FieldType(Frames, "mode"){ .short, .stride, .bytes, .dimensions, .order, .fail, .cancel }) |mode| {
        var held: memory.Tracked = .{ .child = t.allocator };
        var scratch: memory.Tracked = .{ .child = t.allocator };
        var arena: std.heap.ArenaAllocator = .init(held.allocator());
        var src: Frames = .{ .count = 5, .width = 53, .height = 21, .mode = mode };
        try t.expectError(if (mode == .fail) error.Decode else if (mode == .cancel) error.Cancelled else error.BadVideo, video.prepareLazyWithScratch(arena.allocator(), scratch.allocator(), src.source(), config, 16384, 128));
        try t.expectEqual(@as(usize, 1), src.calls);
        try t.expectEqual(@as(usize, 0), scratch.count.live.load(.monotonic));
        arena.deinit();
        try t.expectEqual(@as(usize, 0), held.count.live.load(.monotonic));
    }
}

fn allocationFailure(a: std.mem.Allocator) !void {
    var arena: std.heap.ArenaAllocator = .init(a);
    defer arena.deinit();
    var src: Frames = .{ .count = 5, .width = 53, .height = 21 };
    const out = try video.prepareLazyWithScratch(arena.allocator(), a, src.source(), config, 16384, 128);
    const pixels = try image(out.groups[0]).materialize(a);
    defer a.free(pixels);
    try t.expectEqual(@as(usize, 1), src.calls);
}

test "video prepare: every retained resampler and patch allocation failure unwinds" {
    try t.checkAllAllocationFailures(t.allocator, allocationFailure, .{});
}

test "video prepare: immutable pixels and full geometry govern identity even for a repeated source identity" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    var src: Frames = .{ .count = 5, .width = 53, .height = 21 };
    const a = arena.allocator();
    const first = try video.prepareLazy(a, src.source(), config, 16384, 128);
    const repeat = try video.prepareLazy(a, src.source(), config, 16384, 128);
    try t.expectEqual(first.groups[0].hash, repeat.groups[0].hash);
    src.salt = 1;
    const other = try video.prepareLazy(a, src.source(), config, 16384, 128);
    try t.expect(first.groups[0].hash != other.groups[0].hash);
    var changed = config;
    changed.mean[0] += 0.1;
    try t.expect(other.groups[0].hash != (try video.prepareLazy(a, src.source(), changed, 16384, 128)).groups[0].hash);
    changed = config;
    changed.std[2] += 0.1;
    try t.expect(other.groups[0].hash != (try video.prepareLazy(a, src.source(), changed, 16384, 128)).groups[0].hash);
}

test "video prepare real: truncated unsampled tail passes old eager sampling but fails full preparation" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    var rec: Recording = .{ .clip = try api.tfvideo.Video.open(@embedFile("video-fixtures/truncated-tail.mp4")) };
    defer rec.clip.close();
    const src = rec.source();
    const old = try glm.eager.prepare(arena.allocator(), .{ .frames = src.frames, .rate_num = src.rate_num, .rate_den = src.rate_den, .width = src.width, .height = src.height, .ctx = &rec, .decode = eagerDecode }, config, 16384, 128);
    try t.expectEqual(@as(usize, 2), old.groups.len);
    try t.expectError(error.Short, video.prepareLazy(arena.allocator(), src, config, 16384, 128));
    try t.expectEqual(@as(usize, 1), rec.calls);
    std.debug.print("VIDEO_TRUNCATED old_sampled_pairs=2 preparation=Short engine_admissions=0\n", .{});
}

test "video prepare real: frozen 070 control redecodes every pair and each repeat" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    var rec: Recording = .{ .clip = try api.tfvideo.Video.open(@embedFile("video-fixtures/h264.mp4")) };
    defer rec.clip.close();
    const src = rec.source();
    const old = try glm.eager.prepareLazy(arena.allocator(), .{ .identity = 99, .frames = src.frames, .rate_num = src.rate_num, .rate_den = src.rate_den, .width = src.width, .height = src.height, .ctx = &rec, .decode = eagerDecode }, config, 16384, 128);
    try t.expectEqual(@as(usize, 0), rec.legacy_calls);
    for (0..2) |_| for (old.groups) |g| {
        var scratch: std.heap.ArenaAllocator = .init(t.allocator);
        defer scratch.deinit();
        const d = g.deferred.?;
        _ = try d.prepare(d.ctx, scratch.allocator());
    };
    try t.expectEqual(old.groups.len * 2, rec.legacy_calls);
    try t.expect(rec.legacy_bytes > 0);
    std.debug.print("VIDEO_070_CONTROL prepared_decode_calls=0 materialize_decode_calls={d} materialize_decoded_bytes={d}\n", .{ rec.legacy_calls, rec.legacy_bytes });
}
