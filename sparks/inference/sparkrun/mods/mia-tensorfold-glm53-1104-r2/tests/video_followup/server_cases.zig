test "video prepare real: SSE closes while warm work waits without another decode" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var e: h.Engine = .{ .warm = true };
    var srv: h.Harness = .{};
    try srv.start(&e, .{ .residents = 4 });
    defer srv.deinit();
    e.release.store(true, .release);
    // request() reads to socket EOF. The warm job deliberately remains outstanding.
    const response = try srv.request(a, "/v1/chat/completions", try body(a, try url(a, clip), 1, true));
    try h.expectStatus(response, "HTTP/1.1 200 ");
    try t.expect(std.mem.indexOf(u8, response, "[DONE]") != null);
    try h.waitCount(&e.warm_calls, 1);
    try srv.wait("residents", 1);
    try t.expectEqual(@as(u32, 1), e.decode_calls.load(.acquire));
    try t.expect(!e.release_warm.load(.acquire));
    e.release_warm.store(true, .release);
    try srv.quiet();
    try t.expectEqual(@as(u32, 1), e.decode_calls.load(.acquire));
}
