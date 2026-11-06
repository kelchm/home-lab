const std = @import("std");
const t = std.testing;
const h = @import("video_prepare_harness.zig");
const api = @import("engine_api");
const videos = @import("videos.zig");
const Cx = @import("errors.zig").Cx;
const clip = @embedFile("video-fixtures/h264.mp4");

fn url(a: std.mem.Allocator, bytes: []const u8) ![]const u8 {
    const encoder = std.base64.standard.Encoder;
    const out = try a.alloc(u8, encoder.calcSize(bytes.len));
    return std.fmt.allocPrint(a, "data:video/mp4;base64,{s}", .{encoder.encode(out, bytes)});
}
fn body(a: std.mem.Allocator, data: []const u8, count: usize, stream: bool) ![]const u8 {
    var parts: std.ArrayList(u8) = .empty;
    for (0..count) |i| {
        if (i > 0) try parts.appendSlice(a, ",");
        try parts.appendSlice(a, try std.fmt.allocPrint(a, "{{\"type\":\"video_url\",\"video_url\":{{\"url\":\"{s}\"}}}}", .{data}));
    }
    return std.fmt.allocPrint(a, "{{\"model\":\"m\",\"max_tokens\":2,\"stream\":{s},\"messages\":[{{\"role\":\"user\",\"content\":[{s}]}}]}}", .{ if (stream) "true" else "false", parts.items });
}

test "video prepare real: native HTTP delays engine admission until four clips validate and releases all owners" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var e: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&e, .{ .residents = 4, .waiting = 32 });
    defer srv.deinit();
    const data = try url(a, clip);
    const fd = try srv.begin(try h.wire(a, "/v1/chat/completions", try body(a, data, 4, false)));
    defer h.close(fd);
    try h.waitCount(&e.active, 1);
    try t.expectEqual(@as(u32, 4), e.decode_calls.load(.acquire));
    try t.expectEqual(@as(usize, 0), srv.srv.prepare_memory.of(.media).count.live.load(.acquire));
    try t.expect(srv.srv.prepare_memory.of(.media).count.peak.load(.acquire) > clip.len);
    const bytes = e.decoded_bytes.load(.acquire);
    e.release.store(true, .release);
    try h.expectStatus(try h.receive(a, fd), "HTTP/1.1 200 ");
    try srv.quiet();
    try t.expectEqual(bytes, e.decoded_bytes.load(.acquire));
    try t.expectEqual(@as(u32, 4), e.decode_calls.load(.acquire));
    try t.expect(e.checked_images.load(.acquire) >= 4);
    std.debug.print("VIDEO_HTTP clips=4 decode_calls=4 decoded_bytes={d} engine_decoded_bytes=0 retained_peak={d} media_peak={d}\n", .{ bytes, srv.srv.prepare_memory.of(.retained).count.peak.load(.acquire), srv.srv.prepare_memory.of(.media).count.peak.load(.acquire) });
    try h.expectStatus(try srv.request(a, "/v1/chat/completions", try body(a, data, 5, false)), "HTTP/1.1 400 ");
    try t.expectEqual(@as(u32, 1), e.calls.load(.acquire));
    try srv.quiet();
}

test "video prepare real: bad short truncated frames and exhausted budgets return HTTP 400 before submit" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var e: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&e, .{ .residents = 4 });
    defer srv.deinit();
    e.release.store(true, .release);
    for ([_][]const u8{ "invalid", clip[0 .. clip.len / 2], @embedFile("video-fixtures/truncated-tail.mp4"), @embedFile("video-fixtures/short.mp4") }) |bad| {
        const response = try srv.request(a, "/v1/chat/completions", try body(a, try url(a, bad), 1, false));
        try h.expectStatus(response, "HTTP/1.1 400 ");
        try t.expectEqual(@as(u32, 0), e.calls.load(.acquire));
        try srv.quiet();
    }
    for (0..2) |mode| {
        e.bad_frame = mode == 0;
        e.budget_failure = mode == 1;
        try h.expectStatus(try srv.request(a, "/v1/chat/completions", try body(a, try url(a, clip), 1, true)), "HTTP/1.1 400 ");
        try t.expectEqual(@as(u32, 0), e.calls.load(.acquire));
        try srv.quiet();
    }
    e.bad_frame = false;
    e.budget_failure = false;
    try h.expectStatus(try srv.request(a, "/v1/chat/completions", try body(a, try url(a, clip), 1, true)), "HTTP/1.1 200 ");
    try srv.quiet();
    try t.expectEqual(@as(u32, 1), e.calls.load(.acquire));
}

test "video prepare real: data URL warm passes decode once on the parent thread and retain its admission" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var e: h.Engine = .{ .warm = true };
    var srv: h.Harness = .{};
    try srv.start(&e, .{ .residents = 4 });
    defer srv.deinit();
    e.release.store(true, .release);
    try h.expectStatus(try srv.request(a, "/v1/chat/completions", try body(a, try url(a, clip), 1, false)), "HTTP/1.1 200 ");
    try h.waitCount(&e.warm_calls, 1);
    try srv.wait("residents", 1);
    try t.expectEqual(@as(u32, 2), e.decode_calls.load(.acquire));
    try t.expectEqual(@as(usize, 0), srv.srv.prepare_memory.of(.media).count.live.load(.acquire));
    const bytes = e.decoded_bytes.load(.acquire);
    try t.expect(srv.srv.prepare_memory.of(.retained).count.live.load(.acquire) > 0);
    e.release_warm.store(true, .release);
    try srv.quiet();
    try t.expectEqual(bytes, e.decoded_bytes.load(.acquire));
    try t.expectEqual(@as(u32, 2), e.decode_calls.load(.acquire));
    std.debug.print("VIDEO_WARM parent_decode=1 warm_decode=1 engine_decode=0 retained_final=0 residents_final=0\n", .{});
}

test "video prepare real: Messages and Responses keep explicit unsupported video refusals without engine admission" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    var e: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&e, .{ .residents = 4 });
    defer srv.deinit();
    const a = arena.allocator();
    const data = try url(a, clip);
    try h.expectStatus(try srv.request(a, "/v1/messages", try body(a, data, 1, false)), "HTTP/1.1 400 ");
    const response = try std.fmt.allocPrint(a, "{{\"model\":\"m\",\"store\":false,\"input\":[{{\"role\":\"user\",\"content\":[{{\"type\":\"input_video\",\"video_url\":\"{s}\"}}]}}]}}", .{data});
    try h.expectStatus(try srv.request(a, "/v1/responses", response), "HTTP/1.1 400 ");
    try t.expectEqual(@as(u32, 0), e.calls.load(.acquire));
    try t.expectEqual(@as(u32, 0), e.decode_calls.load(.acquire));
    try srv.quiet();
}

test "video prepare real: exact 16 MiB encoded boundary and short shared deadline release temporary bytes" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var e: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&e, .{ .residents = 4 });
    defer srv.deinit();
    e.release.store(true, .release);
    const bytes = try a.alloc(u8, videos.max_bytes + 1);
    @memset(bytes, 0);
    @memcpy(bytes[0..clip.len], clip);
    try h.expectStatus(try srv.request(a, "/v1/chat/completions", try body(a, try url(a, bytes[0..videos.max_bytes]), 1, false)), "HTTP/1.1 200 ");
    try srv.quiet();
    try h.expectStatus(try srv.request(a, "/v1/chat/completions", try body(a, try url(a, bytes), 1, false)), "HTTP/1.1 400 ");
    try t.expectEqual(@as(u32, 1), e.calls.load(.acquire));
    try srv.quiet();
    var cx: Cx = .{ .a = a };
    var budget: videos.Budget = .{ .io = t.io, .deadline = .{ .nanoseconds = 0 } };
    try t.expectError(error.Refused, videos.prepare(&cx, e.offer.video.?, &e, null, try url(a, clip), 0, 16384, &budget));
    try t.expectEqual(@as(u16, 400), cx.status());
    try t.expectEqual(@as(usize, 0), budget.used);
    try t.expectEqual(@as(u32, 1), e.decode_calls.load(.acquire));
}

test "video prepare real: disconnect and engine refusal release validated canvases without redecoding" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var e: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&e, .{ .residents = 4 });
    defer srv.deinit();
    const request = try body(a, try url(a, clip), 1, true);
    const fd = try srv.begin(try h.wire(a, "/v1/chat/completions", request));
    try h.waitCount(&e.active, 1);
    h.close(fd);
    e.release.store(true, .release);
    try srv.quiet();
    try t.expectEqual(@as(u32, 1), e.decode_calls.load(.acquire));
    e.reject.store(true, .release);
    try h.expectStatus(try srv.request(a, "/v1/chat/completions", request), "HTTP/1.1 503 ");
    try srv.quiet();
    try t.expectEqual(@as(u32, 2), e.decode_calls.load(.acquire));
    try t.expectEqual(@as(u32, 1), e.calls.load(.acquire));
    e.reject.store(false, .release);
    try h.expectStatus(try srv.request(a, "/v1/chat/completions", request), "HTTP/1.1 200 ");
    try srv.quiet();
    try t.expectEqual(@as(u32, 3), e.decode_calls.load(.acquire));
}

test "video prepare real: deadline expiring inside a real frame callback refuses before admission" {
    var data_arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer data_arena.deinit();
    const data = try url(data_arena.allocator(), clip);
    var ledger = h.memory.Ledger.init(t.allocator);
    const owner = try h.memory.Request.create(t.allocator, t.io, &ledger, null, 0);
    const previous = h.memory.current;
    h.memory.current = owner;
    defer h.memory.current = previous;
    var budget: videos.Budget = .{ .io = t.io, .deadline = std.Io.Clock.awake.now(t.io).addDuration(.fromSeconds(120)), .scratch = ledger.of(.media).allocator() };
    var e: h.Engine = .{ .expire_budget = &budget };
    var cx: Cx = .{ .a = owner.arena.allocator() };
    const offer: api.VideoOffer = .{ .lazy = true, .prepare = h.prepareVideo, .prepare_scratch = h.prepareVideoScratch, .video_token = 1013, .group_open = 1000, .group_close = 1001, .video_tokens = 32768, .per_video_tokens = 16384, .max_videos = 4, .max_frames = 128 };
    try t.expectError(error.Refused, videos.prepare(&cx, offer, &e, null, data, 0, 16384, &budget));
    try t.expectEqual(@as(u16, 400), cx.status());
    try t.expectEqualStrings("video preparation exceeded the configured time limit", cx.message);
    try t.expectEqual(@as(u32, 1), e.decode_calls.load(.acquire));
    try t.expect(e.decoded_bytes.load(.acquire) > 0);
    try t.expectEqual(@as(u32, 0), e.calls.load(.acquire));
    try t.expectEqual(@as(usize, 0), ledger.of(.media).count.live.load(.acquire));
    owner.release();
    for (&ledger.categories) |*category| try t.expectEqual(@as(usize, 0), category.count.live.load(.acquire));
}
