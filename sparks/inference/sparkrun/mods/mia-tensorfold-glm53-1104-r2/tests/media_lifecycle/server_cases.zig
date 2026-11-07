// Appended to the existing native_media_test.zig fixture in a disposable copy only.

test "native media parity: lifecycle deadlines and checked callbacks survive reordered repeated tool images" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var f = try Fixture.init(a);
    defer f.deinit();
    var dummy: u8 = 0;
    const messages = try openai(a);
    const repeated = try a.alloc(json.Value, 2);
    repeated[0] = try part(a, a_png);
    repeated[1] = try part(a, a_png);
    try messages.array[3].object.put(a, "content", .{ .array = repeated });
    const out = try rendered(a, f.hf, messages, vision(&dummy));
    try t.expectEqual(@as(usize, 3), out.images.len);
    try t.expectEqual(out.images[0].hash, out.images[1].hash);
    try t.expect(out.images[1].hash != out.images[2].hash);
    for (out.images) |img| {
        try t.expect(img.deadline != null);
        try t.expect(img.deferred.?.checked != null);
        try t.expectEqual(out.images[0].deadline.?.at, img.deadline.?.at);
        var expired = img;
        expired.deadline.?.at = .{ .nanoseconds = 0 };
        try t.expectError(error.MediaDeadline, expired.materialize(a));
    }
    const Cancel = struct {
        calls: usize = 0,
        at: usize,
        fn check(ctx: ?*anyopaque) bool {
            const self: *@This() = @ptrCast(@alignCast(ctx.?));
            self.calls += 1;
            return self.calls >= self.at;
        }
    };
    for (1..6) |stage| {
        var cancel: Cancel = .{ .at = stage };
        var scratch: std.heap.ArenaAllocator = .init(t.allocator);
        defer scratch.deinit();
        try t.expectError(error.Cancelled, out.images[0].materializeChecked(scratch.allocator(), .{ .ptr = &cancel, .check = Cancel.check }));
        try t.expectEqual(stage, cancel.calls);
    }
    var scratch: std.heap.ArenaAllocator = .init(t.allocator);
    defer scratch.deinit();
    const eager = try glm.image.prepare(t.io, scratch.allocator(), a_png, config, 2048);
    try t.expectEqualSlices(f32, eager.pixels, try out.images[0].materialize(scratch.allocator()));
}
