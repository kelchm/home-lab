const std = @import("std");
const json = @import("json");
const Cache = @import("reasoning_cache.zig").Cache;
const Delivered = @import("delivered_calls.zig").Delivered;
const t = std.testing;
const Value = json.Value;

fn history(a: std.mem.Allocator, ids: []const []const u8) !Value {
    const calls = try a.alloc(Value, ids.len);
    for (calls, ids) |*slot, id| {
        const call = try json.newObject(a);
        try call.put(a, "id", .{ .string = id });
        slot.* = .{ .object = call };
    }
    const m = try json.newObject(a);
    try m.put(a, "role", .{ .string = "assistant" });
    try m.put(a, "tool_calls", .{ .array = calls });
    return .{ .array = try a.dupe(Value, &.{.{ .object = m }}) };
}
fn text(value: Value) ?[]const u8 {
    return value.array[0].strField("reasoning_content");
}

test "native API parity: LRU eviction bounds replies bytes IDs and restores owned copies" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var cache: Cache = .{ .gpa = t.allocator, .limit = 2 };
    defer cache.deinit();
    try cache.remember(t.io, &.{"one"}, "Reason one");
    try cache.remember(t.io, &.{"two"}, "Reason two");
    const saved = try cache.restore(t.io, a, try history(a, &.{"one"}));
    try cache.remember(t.io, &.{"tri"}, "Reason tri");
    try t.expectEqual(@as(usize, 2), cache.count);
    try t.expect(!cache.by_id.contains("two"));
    try cache.remember(t.io, &.{"for"}, "Reason for");
    try t.expect(!cache.by_id.contains("one"));
    try t.expectEqualStrings("Reason one", text(saved).?);
    try t.expect(text(try cache.restore(t.io, a, try history(a, &.{"two"}))) == null);
    try t.expect(cache.bytes <= cache.max_bytes);

    var bytes: Cache = .{ .gpa = t.allocator };
    defer bytes.deinit();
    try bytes.remember(t.io, &.{"aaa"}, "思考");
    bytes.max_bytes = bytes.bytes * 2;
    try bytes.remember(t.io, &.{"bbb"}, "思考");
    try bytes.remember(t.io, &.{"ccc"}, "思考");
    try t.expectEqual(@as(usize, 2), bytes.count);
    try t.expect(!bytes.by_id.contains("aaa"));
    try t.expect(bytes.bytes <= bytes.max_bytes);
    const large = try a.alloc(u8, bytes.max_bytes + 1);
    @memset(large, 'x');
    try bytes.remember(t.io, &.{"huge"}, large);
    try t.expect(!bytes.by_id.contains("huge"));
    const too_many = try a.alloc([]const u8, @import("reasoning_cache.zig").max_calls + 1);
    @memset(too_many, "unused");
    try bytes.remember(t.io, too_many, "no");
    try t.expect(!bytes.by_id.contains("unused"));
}

test "native API parity: exact IDs only all calls must agree and caller reasoning is immutable" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var cache: Cache = .{ .gpa = t.allocator };
    defer cache.deinit();
    try cache.remember(t.io, &.{ "a", "b" }, "First");
    try cache.remember(t.io, &.{"c"}, "Second");
    for ([_][]const []const u8{ &.{"unknown"}, &.{ "a", "unknown" }, &.{ "a", "c" }, &.{""}, &.{} }) |ids| {
        const input = try history(a, ids);
        try t.expect(text(try cache.restore(t.io, a, input)) == null);
    }
    for ([_][]const []const u8{ &.{"a"}, &.{"b"}, &.{ "b", "a" } }) |ids| {
        const input = try history(a, ids);
        try t.expectEqualStrings("First", text(try cache.restore(t.io, a, input)).?);
        try t.expect(text(input) == null);
    }
    for ([_][]const u8{ "reasoning_content", "reasoning", "content" }, [_][]const u8{ "mine", "mine", "<think>mine</think>answer" }) |key, value| {
        const input = try history(a, &.{"a"});
        try input.array[0].object.put(a, key, .{ .string = value });
        try t.expect(json.equal(input, try cache.restore(t.io, a, input)));
    }
    const input = try history(a, &.{"a"});
    try input.array[0].object.put(a, "role", .{ .string = "user" });
    try t.expect(text(try cache.restore(t.io, a, input)) == null);
}

fn allocationFailures(gpa: std.mem.Allocator) !void {
    var cache: Cache = .{ .gpa = gpa };
    defer cache.deinit();
    try cache.remember(t.io, &.{ "id-a", "id-b" }, "owned reasoning");
    var arena: std.heap.ArenaAllocator = .init(gpa);
    defer arena.deinit();
    const input = try history(arena.allocator(), &.{"id-a"});
    const restored = try cache.restore(t.io, arena.allocator(), input);
    try t.expectEqualStrings("owned reasoning", text(restored).?);
}

test "native API parity: allocation failures release cache and request ownership" {
    try t.checkAllAllocationFailures(t.allocator, allocationFailures, .{});
}

test "native API parity: concurrent remember restore eviction keeps copies valid" {
    var cache: Cache = .{ .gpa = t.allocator, .limit = 8, .max_bytes = 2048 };
    defer cache.deinit();
    const Worker = struct {
        cache: *Cache,
        number: usize,
        failed: *std.atomic.Value(bool),
        fn run(w: @This()) void {
            w.work() catch {
                w.failed.store(true, .seq_cst);
            };
        }
        fn work(w: @This()) !void {
            for (0..50) |i| {
                var arena: std.heap.ArenaAllocator = .init(std.heap.page_allocator);
                defer arena.deinit();
                const a = arena.allocator();
                const id = try std.fmt.allocPrint(a, "id-{d}-{d}", .{ w.number, i });
                const reason = try std.fmt.allocPrint(a, "reason-{d}-{d}", .{ w.number, i });
                try w.cache.remember(t.io, &.{id}, reason);
                const restored = try w.cache.restore(t.io, a, try history(a, &.{id}));
                if (text(restored)) |found| try t.expectEqualStrings(reason, found);
            }
        }
    };
    var failed: std.atomic.Value(bool) = .init(false);
    var threads: [4]std.Thread = undefined;
    for (&threads, 0..) |*thread, i| thread.* = try std.Thread.spawn(.{}, Worker.run, .{Worker{ .cache = &cache, .number = i, .failed = &failed }});
    for (threads) |thread| thread.join();
    try t.expect(!failed.load(.seq_cst));
    try t.expect(cache.count <= cache.limit);
    try t.expect(cache.bytes <= cache.max_bytes);
}

test "native API parity: streamed identity capture refuses mismatch incomplete and conflicting deltas" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const delta = (try json.parse(a, "{\"tool_calls\":[{\"index\":0,\"id\":\"exposed\",\"function\":{\"name\":\"f\",\"arguments\":\"{\\\"q\\\":1}\"}}]}")).ok;
    const parsed = (try json.parse(a, "[{\"id\":\"reparsed\",\"function\":{\"name\":\"f\",\"arguments\":\"{\\\"q\\\":1}\"}}]")).ok.array;
    var capture: Delivered = .{};
    capture.note(a, delta);
    try t.expectEqualStrings("exposed", (try capture.ids(a, parsed)).?[0]);
    try parsed[0].get("function").?.object.put(a, "arguments", .{ .string = "{\"q\":2}" });
    try t.expect(try capture.ids(a, parsed) == null);
    capture.note(a, delta);
    try t.expect(!capture.valid);
    var incomplete: Delivered = .{};
    try delta.get("tool_calls").?.array[0].get("function").?.object.put(a, "arguments", .{ .string = "{" });
    incomplete.note(a, delta);
    try t.expect(try incomplete.ids(a, parsed) == null);
}

test "native API parity: default cache never exceeds 1024 completed replies" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var cache: Cache = .{ .gpa = t.allocator };
    defer cache.deinit();
    try t.expectEqual(@as(usize, 1024), cache.limit);
    try t.expectEqual(@as(usize, 32 << 20), cache.max_bytes);
    for (0..1025) |i| {
        const id = try std.fmt.allocPrint(a, "original-{d}", .{i});
        try cache.remember(t.io, &.{id}, "reason");
    }
    try t.expectEqual(@as(usize, 1024), cache.count);
    try t.expectEqual(@as(u32, 1024), cache.by_id.count());
    try t.expect(!cache.by_id.contains("original-0"));
    try t.expect(cache.by_id.contains("original-1024"));
}
