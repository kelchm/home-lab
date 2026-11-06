//! Native HTTP, HfText, GLM image planning/materialization; only engine consumption is delayed and device-free.
const std = @import("std");
const api = @import("engine_api");
const json = @import("json");
const server = @import("server.zig");
const media = @import("native_media_test.zig");
const glm = @import("glm_media");
pub const memory = @import("prepare_memory.zig");
pub const gate = @import("prepare_gate.zig");
const listener = @import("listener.zig");
pub const posix = @import("sys").posix;
const t = std.testing;
const Allocator = std.mem.Allocator;

pub var available: std.atomic.Value(u64) = .init(16 << 30);
pub var missing: std.atomic.Value(bool) = .init(false);
fn telemetry(_: std.Io) ?u64 {
    return if (missing.load(.acquire)) null else available.load(.acquire);
}
fn pause() void {
    std.Io.sleep(t.io, .fromMilliseconds(2), .awake) catch {};
}

pub const Engine = struct {
    calls: std.atomic.Value(u32) = .init(0),
    last_input: std.atomic.Value(u64) = .init(0),
    active: std.atomic.Value(u32) = .init(0),
    workers: std.atomic.Value(u32) = .init(0),
    peak: std.atomic.Value(u32) = .init(0),
    warm_calls: std.atomic.Value(u32) = .init(0),
    checked_images: std.atomic.Value(u32) = .init(0),
    failures: std.atomic.Value(u32) = .init(0),
    release: std.atomic.Value(bool) = .init(false),
    release_warm: std.atomic.Value(bool) = .init(false),
    reject: std.atomic.Value(bool) = .init(false),
    fail: std.atomic.Value(bool) = .init(false),
    cancelled: [256]std.atomic.Value(bool) = @splat(.init(false)),
    warm: bool = false,
    offer: api.Vision = undefined,
    reply_token: u32 = 120,
    consumer: memory.Tracked = .{ .child = t.allocator },

    fn engine(e: *Engine) api.Engine {
        return .{ .ctx = e, .vtable = &.{ .info = info, .submit = submit, .cancel = cancel, .status = status, .memory = deviceMemory } };
    }
    fn cast(ptr: *anyopaque) *Engine {
        return @ptrCast(@alignCast(ptr));
    }
    fn info(ptr: *anyopaque) api.Info {
        return .{ .context_window = 202752, .lanes = 4, .vision = cast(ptr).offer, .warm_turns = cast(ptr).warm };
    }
    const Work = struct {
        e: *Engine,
        id: api.Id,
        request: api.Request,
        sink: api.Sink,
        identity: u64,
    };
    fn submit(ptr: *anyopaque, id: api.Id, r: *const api.Request, sink: api.Sink) api.SubmitError!void {
        const e = cast(ptr);
        if (e.reject.load(.acquire)) return error.Busy;
        const work = t.allocator.create(Work) catch return error.Busy;
        errdefer t.allocator.destroy(work);
        work.* = .{ .e = e, .id = id, .request = r.*, .sink = sink, .identity = fingerprint(r) };
        e.last_input.store(std.hash.Wyhash.hash(0, std.mem.sliceAsBytes(r.prompt)), .release);
        _ = e.calls.fetchAdd(1, .release);
        _ = e.workers.fetchAdd(1, .release);
        const n = e.active.fetchAdd(1, .acq_rel) + 1;
        _ = e.peak.fetchMax(n, .monotonic);
        if (r.max_tokens == 0) _ = e.warm_calls.fetchAdd(1, .release);
        const thread = std.Thread.spawn(.{}, consume, .{work}) catch {
            _ = e.active.fetchSub(1, .release);
            _ = e.workers.fetchSub(1, .release);
            return error.Busy;
        };
        thread.detach();
    }
    fn fingerprint(r: *const api.Request) u64 {
        var h = std.hash.Wyhash.init(0);
        h.update(std.mem.sliceAsBytes(r.prompt));
        h.update(std.mem.sliceAsBytes(r.cache_key));
        for (r.images) |im| {
            h.update(std.mem.asBytes(&im.hash));
            h.update(std.mem.asBytes(&im.at));
            h.update(std.mem.asBytes(&im.tokens));
        }
        return h.final();
    }
    fn validate(w: *Work) !void {
        try t.expectEqual(w.identity, fingerprint(&w.request));
        for (w.request.images) |im| {
            try t.expect(im.deferred != null and im.preparation_budget != null);
            var scratch: std.heap.ArenaAllocator = .init(w.e.consumer.allocator());
            defer scratch.deinit();
            const pixels = try im.materialize(scratch.allocator());
            try t.expect(pixels.len > 0 and std.math.isFinite(pixels[0]));
            _ = w.e.checked_images.fetchAdd(1, .monotonic);
        }
    }
    fn consume(w: *Work) void {
        const e = w.e;
        defer _ = e.workers.fetchSub(1, .release);
        defer t.allocator.destroy(w);
        var rounds: usize = 0;
        while (!(if (w.request.max_tokens == 0) e.release_warm.load(.acquire) else e.release.load(.acquire)) and
            !e.cancelled[w.id % 256].load(.acquire)) : (rounds += 1)
        {
            if (rounds == 15000) @panic("delayed fixture consumer did not receive release");
            pause();
        }
        // Even cancellation must retain all borrows until completion, so validation runs on every submitted request.
        validate(w) catch |err| {
            std.debug.print("delayed consumer: {s}\n", .{@errorName(err)});
            _ = e.failures.fetchAdd(1, .monotonic);
        };
        const reason: api.Reason = if (e.cancelled[w.id % 256].load(.acquire)) .cancelled else if (e.fail.load(.acquire)) .failed else .stop;
        if (reason == .stop and w.request.max_tokens > 0) {
            w.sink.event(w.sink.ctx, w.id, &.{ .prefilled = 0 });
            w.sink.event(w.sink.ctx, w.id, &.{ .tokens = &.{e.reply_token} });
        }
        _ = e.active.fetchSub(1, .release);
        w.sink.event(w.sink.ctx, w.id, &.{ .finished = .{ .reason = reason } });
    }
    fn cancel(ptr: *anyopaque, id: api.Id) void {
        cast(ptr).cancelled[id % 256].store(true, .release);
    }
    fn status(ptr: *anyopaque, out: *api.Status, _: []u32) void {
        out.* = .{ .running = cast(ptr).active.load(.acquire) };
    }
    fn deviceMemory(_: *anyopaque, _: bool) ?api.Memory {
        return null;
    }
};

fn prepareScratch(_: *anyopaque, a: Allocator, scratch: Allocator, bytes: []const u8, cap: u32) !api.PreparedImage {
    const p = try glm.image.planWithScratch(t.io, a, scratch, bytes, media.config, cap);
    return .{ .pixels = p.pixels, .gh = p.gh, .gw = p.gw, .tokens = p.tokens, .hash = p.hash, .source_pixels = p.source_pixels, .deferred = if (p.deferred) |d|
        .{ .ctx = d.ctx, .prepare = d.prepare, .checked = d.checked }
    else
        null };
}

pub const Harness = struct {
    srv: *server.Server = undefined,
    e: *Engine = undefined,
    files: media.Fixture = undefined,
    socket: listener.Listener = undefined,
    accept: std.Thread = undefined,
    stop: std.atomic.Value(bool) = .init(false),

    pub fn start(h: *Harness, e: *Engine, policy: gate.Policy) !void {
        missing.store(false, .release);
        available.store(16 << 30, .release);
        h.e = e;
        h.files = try media.Fixture.init(t.allocator);
        errdefer h.files.deinit();
        e.offer = media.vision(e);
        e.offer.image_token = h.files.hf.text().tokenId("<|image|>").?;
        e.offer.markers = &.{};
        e.offer.prepare_scratch = prepareScratch;
        const ids = try h.files.hf.text().encode(t.allocator, "x", false);
        defer t.allocator.free(ids);
        e.reply_token = ids[0];
        h.srv = try server.Server.init(t.allocator, t.io, e.engine(), h.files.hf.text(), .{
            .served_name = "m",
            .model_ids = &.{"m"},
            .enable_thinking = false,
            .keep_warm_s = 0,
            .request_body_bytes = 96 << 20,
            .prepare_policy = policy,
        }, null);
        errdefer h.srv.deinit();
        h.srv.prepare_gate.available = telemetry;
        h.socket = try listener.Listener.open(.{ .ip4 = .loopback(0) });
        errdefer h.socket.close();
        h.accept = try std.Thread.spawn(.{}, server.Server.serve, .{ h.srv, h.socket, &h.stop });
    }
    pub fn deinit(h: *Harness) void {
        h.e.release.store(true, .release);
        h.e.release_warm.store(true, .release);
        h.srv.draining.store(true, .seq_cst);
        h.stop.store(true, .release);
        h.accept.join();
        var waited: usize = 0;
        while (h.srv.open_connections.load(.acquire) > 0 or h.e.workers.load(.acquire) > 0) : (waited += 1) {
            if (waited == 5000) @panic("prepare fixture did not quiesce");
            pause();
        }
        h.socket.close();
        h.srv.deinit();
        h.files.deinit();
    }
    pub fn begin(h: *Harness, raw: []const u8) !posix.socket_t {
        const addr: std.Io.net.IpAddress = .{ .ip4 = .loopback(h.socket.port()) };
        var storage: std.Io.Threaded.PosixAddress = undefined;
        const len = std.Io.Threaded.addressToPosix(&addr, &storage);
        const fd = posix.system.socket(posix.AF.INET, posix.SOCK.STREAM, 0);
        if (posix.errno(fd) != .SUCCESS) return error.Socket;
        errdefer close(fd);
        if (posix.errno(posix.system.connect(fd, &storage.any, len)) != .SUCCESS) return error.Connect;
        try send(fd, raw);
        return fd;
    }
    pub fn request(h: *Harness, a: Allocator, path: []const u8, body: []const u8) ![]const u8 {
        const fd = try h.begin(try wire(a, path, body));
        defer close(fd);
        return receive(a, fd);
    }
    pub fn wait(h: *Harness, comptime field: []const u8, n: u32) !void {
        for (0..2500) |_| {
            if (@field(h.srv.prepare_gate.snapshot(t.io), field) == n) return;
            pause();
        }
        std.debug.print("gate {s} expected {d}, actual {d}\n", .{ field, n, @field(h.srv.prepare_gate.snapshot(t.io), field) });
        return error.Timeout;
    }
    pub fn quiet(h: *Harness) !void {
        for (0..2500) |_| {
            if (h.srv.generating.load(.seq_cst) == 0 and h.e.workers.load(.acquire) == 0) break;
            pause();
        }
        try t.expectEqual(@as(u32, 0), h.srv.generating.load(.seq_cst));
        try t.expectEqual(@as(u32, 0), h.e.failures.load(.acquire));
        for (&h.srv.prepare_memory.categories) |*category| try t.expectEqual(@as(usize, 0), category.count.live.load(.acquire));
        try t.expectEqual(@as(usize, 0), h.e.consumer.count.live.load(.acquire));
        try h.wait("residents", 0);
        try h.wait("waiting", 0);
    }
};
pub fn close(fd: posix.socket_t) void {
    _ = posix.system.close(fd);
}
pub fn send(fd: posix.socket_t, raw: []const u8) !void {
    var sent: usize = 0;
    while (sent < raw.len) {
        const n = posix.system.write(fd, raw[sent..].ptr, raw.len - sent);
        if (posix.errno(n) != .SUCCESS or n == 0) return error.Write;
        sent += @intCast(n);
    }
}
pub fn receive(a: Allocator, fd: posix.socket_t) ![]const u8 {
    var out: std.ArrayList(u8) = .empty;
    var buf: [4096]u8 = undefined;
    while (true) {
        var fds = [_]posix.pollfd{.{ .fd = fd, .events = posix.POLL.IN, .revents = 0 }};
        if (try posix.poll(&fds, 5000) == 0) return error.Timeout;
        const n = posix.system.read(fd, &buf, buf.len);
        if (posix.errno(n) != .SUCCESS) return error.Read;
        if (n == 0) return out.items;
        try out.appendSlice(a, buf[0..@intCast(n)]);
    }
}
pub fn wire(a: Allocator, path: []const u8, body: []const u8) ![]const u8 {
    return std.fmt.allocPrint(a, "POST {s} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {d}\r\nConnection: close\r\n\r\n{s}", .{ path, body.len, body });
}
pub fn expectStatus(reply: []const u8, status: []const u8) !void {
    if (!std.mem.startsWith(u8, reply, status)) {
        std.debug.print("expected {s}, got: {s}\n", .{ status, reply[0..@min(reply.len, 400)] });
        return error.UnexpectedStatus;
    }
}
pub fn waitCount(count: *std.atomic.Value(u32), n: u32) !void {
    for (0..2500) |_| {
        if (count.load(.acquire) == n) return;
        pause();
    }
    return error.Timeout;
}
pub fn requestBody(a: Allocator, route: []const u8, streaming: bool) ![]const u8 {
    const data = try media.url(a, media.a_png);
    const v = if (std.mem.indexOf(u8, route, "messages") != null)
        try std.fmt.allocPrint(a, "{{\"model\":\"m\",\"max_tokens\":8,\"stream\":{s},\"messages\":[{{\"role\":\"user\",\"content\":[{{\"type\":\"image\",\"source\":{{\"type\":\"url\",\"url\":\"{s}\"}}}},{{\"type\":\"image\",\"source\":{{\"type\":\"url\",\"url\":\"{s}\"}}}},{{\"type\":\"text\",\"text\":\"describe\"}}]}}]}}", .{ if (streaming) "true" else "false", data, data })
    else if (std.mem.indexOf(u8, route, "responses") != null)
        try std.fmt.allocPrint(a, "{{\"model\":\"m\",\"max_output_tokens\":8,\"store\":false,\"stream\":{s},\"input\":[{{\"role\":\"user\",\"content\":[{{\"type\":\"input_image\",\"image_url\":\"{s}\"}},{{\"type\":\"input_image\",\"image_url\":\"{s}\"}},{{\"type\":\"input_text\",\"text\":\"describe\"}}]}}]}}", .{ if (streaming) "true" else "false", data, data })
    else
        try std.fmt.allocPrint(a, "{{\"model\":\"m\",\"max_tokens\":8,\"stream\":{s},\"messages\":[{{\"role\":\"user\",\"content\":[{{\"type\":\"image_url\",\"image_url\":{{\"url\":\"{s}\"}}}},{{\"type\":\"image_url\",\"image_url\":{{\"url\":\"{s}\"}}}},{{\"type\":\"text\",\"text\":\"describe\"}}]}}]}}", .{ if (streaming) "true" else "false", data, data });
    return v;
}
