//! Same native prompt and real arena allocator before/after 090, with no policy or model dependency.
const std = @import("std");
const media = @import("native_media_test.zig");
const json = @import("json");
const prompt = @import("prompt.zig");
const Server = @import("server.zig").Server;
const Cx = @import("errors.zig").Cx;
const t = std.testing;
const Allocator = std.mem.Allocator;

const Count = struct {
    live: usize = 0,
    peak: usize = 0,
    fn allocator(c: *Count) Allocator {
        return .{ .ptr = c, .vtable = &.{ .alloc = alloc, .resize = resize, .remap = remap, .free = free } };
    }
    fn change(ctx: *anyopaque, old: usize, new: usize) void {
        const c: *Count = @ptrCast(@alignCast(ctx));
        c.live = c.live + new - old;
        c.peak = @max(c.peak, c.live);
    }
    fn alloc(ctx: *anyopaque, len: usize, alignment: std.mem.Alignment, ra: usize) ?[*]u8 {
        const out = t.allocator.rawAlloc(len, alignment, ra) orelse return null;
        change(ctx, 0, len);
        return out;
    }
    fn resize(ctx: *anyopaque, mem: []u8, alignment: std.mem.Alignment, len: usize, ra: usize) bool {
        if (!t.allocator.rawResize(mem, alignment, len, ra)) return false;
        change(ctx, mem.len, len);
        return true;
    }
    fn remap(ctx: *anyopaque, mem: []u8, alignment: std.mem.Alignment, len: usize, ra: usize) ?[*]u8 {
        const out = t.allocator.rawRemap(mem, alignment, len, ra) orelse return null;
        change(ctx, mem.len, len);
        return out;
    }
    fn free(ctx: *anyopaque, mem: []u8, alignment: std.mem.Alignment, ra: usize) void {
        t.allocator.rawFree(mem, alignment, ra);
        change(ctx, mem.len, 0);
    }
};

test "prepare arena receipt: native prompt retained capacity and exact output identities" {
    var outside: std.heap.ArenaAllocator = .init(t.allocator);
    defer outside.deinit();
    const a = outside.allocator();
    var f = try media.Fixture.init(a);
    defer f.deinit();
    const text = try a.alloc(u8, 32 << 10);
    @memset(text, 'x');
    const first = try media.value(a, try std.fmt.allocPrint(a, "[{{\"role\":\"user\",\"content\":\"{s}\"}}]", .{text}));
    const second = try media.openai(a);
    for ([_]json.Value{ first, second }, 0..) |messages, i| {
        var count: Count = .{};
        var arena: std.heap.ArenaAllocator = .init(count.allocator());
        var srv: Server = undefined;
        srv.text = f.hf.text();
        srv.gpa = t.allocator;
        srv.io = t.io;
        srv.info = .{ .vision = media.vision(&count) };
        srv.info.vision.?.image_token = srv.text.tokenId("<|image|>").?;
        srv.info.vision.?.markers = &.{};
        srv.late_system = "system";
        srv.needs_user_after_tool = false;
        srv.config = .{ .served_name = "receipt", .model_ids = &.{"receipt"} };
        var cx: Cx = .{ .a = arena.allocator() };
        const out = try prompt.prepare(&srv, &cx, .{ .messages = messages, .fields = .null }, false, null);
        const hash = @import("tokens.zig").tokenSha256(out.ids);
        const key = @import("tokens.zig").tokenSha256(out.cache_key);
        std.debug.print("ARENA_RECEIPT case={d} retained={d} peak={d} ids={d} sha={s} key={s} images={d}\n", .{
            i, count.live, count.peak, out.ids.len, hash, key, out.images.len,
        });
        for (out.images) |image| {
            var scratch: std.heap.ArenaAllocator = .init(t.allocator);
            defer scratch.deinit();
            try t.expect((try image.materialize(scratch.allocator())).len > 0);
        }
        arena.deinit();
        try t.expectEqual(@as(usize, 0), count.live);
    }
}
