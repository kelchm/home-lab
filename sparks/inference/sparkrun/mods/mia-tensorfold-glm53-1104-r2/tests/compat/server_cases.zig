// Appended to the media fixture for the final composed source, after 071.
test "native media parity: shared active preparation budgets survive reordered repeated history" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var f = try Fixture.init(a);
    defer f.deinit();
    var dummy: u8 = 0;
    const out = try rendered(a, f.hf, try openai(a), vision(&dummy));
    try t.expectEqual(@as(usize, 2), out.images.len);
    const shared = out.images[0].preparation_budget.?;
    try t.expect(shared == out.images[1].preparation_budget.?);
    try t.expect(shared.remaining_ns > 0 and shared.remaining_ns <= 100 * std.time.ns_per_s);
    for (out.images) |img| {
        try t.expect(img.deadline == null);
        try t.expect(img.deferred.?.checked != null);
        var scratch: std.heap.ArenaAllocator = .init(t.allocator);
        defer scratch.deinit();
        _ = try img.materialize(scratch.allocator());
    }
    shared.remaining_ns = 0;
    try t.expectError(error.MediaDeadline, out.images[1].materialize(a));
}

test "native media parity: all four large clips receive their production share before planning" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const Fake = struct {
        fn prepare(_: *anyopaque, _: std.mem.Allocator, _: api.VideoSource, _: u32, _: u32) anyerror!api.PreparedVideo {
            return error.Unused;
        }
        fn decode(_: *anyopaque, _: []const u32, _: *glm.video.Sink) anyerror!void {
            return error.Unused;
        }
    };
    var dummy: u8 = 0;
    const offer: api.VideoOffer = .{ .prepare = Fake.prepare, .video_token = 8, .group_open = 6, .group_close = 7,
        .video_tokens = 32768, .per_video_tokens = 16384, .max_videos = 4, .max_frames = 128 };
    for ([_]u32{ 16384, 16384, 10922, 8192 }, 1..) |expected, count| {
        const cap = images.videoTokenBudget(offer, count);
        try t.expectEqual(expected, cap);
        const plan = try glm.video.plan(a, .{ .frames = 7200, .rate_num = 30, .rate_den = 1,
            .width = 1920, .height = 1080, .ctx = &dummy, .decode = Fake.decode }, config, cap, 128);
        const actual = plan.pairs() * plan.tokensPerPair();
        try t.expect(actual <= cap);
        try t.expect(actual * count <= 32768);
    }
}
