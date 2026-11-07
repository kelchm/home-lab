test "video prepare real: valid edit-list-trimmed MP4 matches sampled eager frames" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var rec: Recording = .{ .clip = try api.tfvideo.Video.open(@embedFile("video-fixtures/trimmed-edit-list.mp4")) };
    defer rec.clip.close();
    const src = rec.source();
    const eager = try glm.eager.prepare(a, .{ .frames = src.frames, .rate_num = src.rate_num, .rate_den = src.rate_den, .width = src.width, .height = src.height, .ctx = &rec, .decode = eagerDecode }, config, 16384, 128);
    const prepared = try video.prepareLazy(a, src, config, 16384, 128);
    try t.expectEqual(@as(usize, 1), rec.calls);
    try t.expectEqual(@as(usize, 12), rec.count);
    try t.expectEqual(eager.groups.len, prepared.groups.len);
    try t.expectEqualSlices(f64, eager.times, prepared.times);
    for (prepared.groups, eager.groups) |group, ref| {
        const pixels = try image(group).materialize(t.allocator);
        defer t.allocator.free(pixels);
        try t.expectEqualSlices(f32, ref.pixels, pixels);
    }
    std.debug.print("VIDEO_TRIM declared_frames={d} sampled_frames={d} sampled_eager_pixels_equal=true\n", .{ src.frames, rec.count });
}
