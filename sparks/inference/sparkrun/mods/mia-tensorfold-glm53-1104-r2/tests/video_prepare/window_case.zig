test "media lifecycle: prepared video straddles and repeats actual windows without a live decoder" {
    var retained: std.heap.ArenaAllocator = .init(t.allocator);
    defer retained.deinit();
    var frames: Frames = .{};
    var src = frames.source(5);
    src.rate_num = 1;
    const prepared = try video.prepareLazy(retained.allocator(), src, config, 16384, 128);
    try t.expectEqual(@as(usize, 1), frames.calls);
    const expected = try pairImage(prepared.groups[0]).materialize(retained.allocator());
    const Recorder = struct {
        expected: []const f32,
        calls: usize = 0,
        fn encode(r: *@This(), pixels: []const f32, img: Image, out: u64) !void {
            try t.expectEqualSlices(f32, r.expected, pixels);
            r.calls += 1;
            Fake.queued = true;
            const base = (out - Fake.feature_base) / media.row_bytes;
            for (Fake.values[base..][0..img.tokens], 0..) |*value, i| value.* = @intCast(i + 700);
        }
    };
    var recorder: Recorder = .{ .expected = expected };
    var d: cuda.Driver = undefined;
    var v: Images = undefined;
    var map: [media.step_rows / 2 + 1]i32 = undefined;
    var cpu: Counted = .{};
    try setup(&v, &d, cpu.allocator(), &map);
    defer cleanup(&v);
    var img = pairImage(prepared.groups[0]);
    try t.expect(img.tokens > 1);
    img.at = 2047;
    const imgs = [_]Image{img};
    frames.fail_after = 0; // any reopening from a tower/window callback now fails the test
    try v.begin(7, &imgs, 0);
    try v.windowWith(0, 2048, null, &recorder, Recorder.encode);
    try t.expectEqual(@as(u32, 700), Fake.values[0]);
    v.finishWindow();
    try v.windowWith(2048, 4096, null, &recorder, Recorder.encode);
    try t.expectEqual(@as(u32, 701), Fake.values[0]);
    v.finishWindow();
    try v.windowWith(2048, 4096, null, &recorder, Recorder.encode);
    try t.expectEqual(@as(u32, 701), Fake.values[0]);
    v.finishWindow();
    try t.expectEqual(@as(usize, 3), recorder.calls);
    try t.expectEqual(@as(usize, 1), frames.calls);
    try t.expectEqual(@as(usize, 0), frames.active);
    v.release(7);
    std.debug.print("VIDEO_WINDOWS materializations={d} decoder_calls={d} repeat_decode_calls=0 cpu_peak={d}\n", .{ recorder.calls, frames.calls, cpu.peak });
}
