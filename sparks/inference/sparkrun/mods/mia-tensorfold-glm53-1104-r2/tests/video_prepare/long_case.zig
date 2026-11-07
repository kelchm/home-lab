test "video prepare real: 33-second 1080p clip keeps one decode and exact eager floats" {
    const encoded = @embedFile("video-fixtures/long.mp4");
    try t.expect(encoded.len <= 16 << 20);
    var held: memory.Tracked = .{ .child = t.allocator };
    var scratch: memory.Tracked = .{ .child = t.allocator };
    var arena: std.heap.ArenaAllocator = .init(held.allocator());
    var rec: Recording = .{ .clip = try api.tfvideo.Video.open(encoded) };
    defer rec.clip.close();
    const src = rec.source();
    try t.expectEqual(@as(u32, 1920), src.width);
    try t.expectEqual(@as(u32, 1080), src.height);
    const begin = std.Io.Clock.awake.now(t.io);
    const out = try video.prepareLazyWithScratch(arena.allocator(), scratch.allocator(), src, config, 16384, 128);
    const prepare_ns = std.Io.Clock.awake.now(t.io).nanoseconds - begin.nanoseconds;
    try t.expectEqual(@as(usize, 33), out.groups.len);
    try t.expectEqual(@as(usize, 1), rec.calls);
    const decoded = rec.bytes;
    try t.expectEqual(@as(usize, 0), scratch.count.live.load(.monotonic));
    const prep_scratch_peak = scratch.count.peak.load(.monotonic);
    const prep_rss = @import("process_resident.zig").read(t.io).?;
    var eager_arena: std.heap.ArenaAllocator = .init(t.allocator);
    const ref = try glm.eager.prepare(eager_arena.allocator(), .{ .frames = src.frames, .rate_num = src.rate_num, .rate_den = src.rate_den, .width = src.width, .height = src.height, .ctx = &rec, .decode = eagerDecode }, config, 16384, 128);
    var compared: usize = 0;
    for (out.groups, ref.groups) |g, old| {
        try t.expectEqual(@as(usize, 0), g.pixels.len);
        try t.expectEqual(old.tokens, g.tokens);
        try t.expectEqual(old.gh, g.gh);
        try t.expectEqual(old.gw, g.gw);
        for (0..2) |_| {
            const pixels = try image(g).materialize(scratch.allocator());
            try t.expectEqualSlices(f32, old.pixels, pixels);
            compared += pixels.len;
            scratch.allocator().free(pixels);
        }
    }
    try t.expectEqualSlices(f64, ref.times, out.times);
    eager_arena.deinit();
    try t.expectEqual(@as(usize, 1), rec.calls);
    try t.expectEqual(decoded, rec.bytes);
    try t.expectEqual(@as(usize, 0), scratch.count.live.load(.monotonic));
    std.debug.print("VIDEO_1080P encoded={d} groups={d} calls=1 decoded_bytes={d} repeat_decoded_bytes=0 compared_floats={d} prepare_ms={d} retained_peak={d} prepare_scratch_peak={d} materialize_scratch_peak={d} prep_rss_peak={d}\n", .{ encoded.len, out.groups.len, decoded, compared, @divTrunc(prepare_ns, std.time.ns_per_ms), held.count.peak.load(.monotonic), prep_scratch_peak, scratch.count.peak.load(.monotonic), prep_rss.peak_bytes.? });
    arena.deinit();
    try t.expectEqual(@as(usize, 0), held.count.live.load(.monotonic));
}
