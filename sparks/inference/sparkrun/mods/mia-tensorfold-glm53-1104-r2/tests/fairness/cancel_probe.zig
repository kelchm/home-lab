//! Actual donor Engine/Forward/Leader cancellation; CUDA calls record arguments without computing a model.
const std = @import("std");
const glm = @import("glm");
const cuda = @import("cuda");
const lanes = @import("lanes");
const t = std.testing;
const Engine = glm.engine.Engine;
const Seq = @typeInfo(@FieldType(Engine, "cur")).pointer.child;
const C = glm.engine.prompt_rows;

const Record = struct {
    const shared = 0x1000;
    const link = 0x2000;
    const conv_shift = 10;
    const kda_step = 11;
    var word: f64 = 0;
    var shifts: usize = 0;
    var inputs: [16]u64 = undefined;
    var outputs: [16]u64 = undefined;
    var steps: usize = 0;
    var syncs: usize = 0;
    var header: glm.follow.Header = undefined;
    var messages: usize = 0;
    var events: usize = 0;
    var live_events: usize = 0;
    var marked: usize = 0;
    var mark_error: ?anyerror = null;

    fn reset() void {
        word = 0;
        shifts = 0;
        steps = 0;
        syncs = 0;
        messages = 0;
        events = 0;
        live_events = 0;
        marked = 0;
        mark_error = null;
    }
    fn value(comptime T: type, args: [*]?*anyopaque, i: usize) T {
        return @as(*const T, @ptrCast(@alignCast(args[i].?))).*;
    }
    fn launch(f: cuda.abi.Function, _: c_uint, _: c_uint, _: c_uint, _: c_uint, _: c_uint, _: c_uint, _: c_uint, _: cuda.abi.Stream, args_: ?[*]?*anyopaque, _: ?[*]?*anyopaque) callconv(.c) c_int {
        const args = args_.?;
        if (@intFromPtr(f) == conv_shift) shifts += 1;
        if (@intFromPtr(f) == kda_step) {
            std.debug.assert(steps < inputs.len);
            inputs[steps] = value(u64, args, 1);
            outputs[steps] = value(u64, args, 9);
            steps += 1;
        }
        return 0;
    }
    fn upload(dst: u64, src_: ?*const anyopaque, n: usize, _: cuda.abi.Stream) callconv(.c) c_int {
        const src: [*]const u8 = @ptrCast(src_.?);
        if (dst == shared) @memcpy(std.mem.asBytes(&word), src[0..n]);
        if (dst == link and n == @sizeOf(glm.follow.Header)) @memcpy(std.mem.asBytes(&header), src[0..n]);
        return 0;
    }
    fn download(dst: ?*anyopaque, src: u64, n: usize) callconv(.c) c_int {
        std.debug.assert(src == shared and n == 8);
        @memcpy(@as([*]u8, @ptrCast(dst.?))[0..n], std.mem.asBytes(&word));
        return 0;
    }
    fn device(out: *cuda.abi.Device) callconv(.c) c_int {
        out.* = 0;
        return 0;
    }
    fn attr(out: *c_int, _: cuda.abi.DeviceAttribute, _: cuda.abi.Device) callconv(.c) c_int {
        out.* = 0;
        return 0;
    }
    fn sync(_: cuda.abi.Stream) callconv(.c) c_int {
        syncs += 1;
        return 0;
    }
    fn memset32(_: u64, _: c_uint, _: usize, _: cuda.abi.Stream) callconv(.c) c_int {
        return 0;
    }
    fn memset8(_: u64, _: u8, _: usize, _: cuda.abi.Stream) callconv(.c) c_int {
        return 0;
    }
    fn reduce(_: u64, _: u64, _: usize, _: cuda.nccl.DataType, _: cuda.nccl.RedOp, _: cuda.nccl.Comm, _: cuda.abi.Stream) callconv(.c) c_int {
        return 0;
    }
    fn gather(_: u64, _: u64, _: usize, _: cuda.nccl.DataType, _: cuda.nccl.Comm, _: cuda.abi.Stream) callconv(.c) c_int {
        return 0;
    }
    fn broadcast(_: u64, _: u64, _: usize, _: cuda.nccl.DataType, _: c_int, _: cuda.nccl.Comm, _: cuda.abi.Stream) callconv(.c) c_int {
        messages += 1;
        return 0;
    }
    fn hostAlloc(out: *?*anyopaque, n: usize, _: c_uint) callconv(.c) c_int {
        const bytes = t.allocator.alignedAlloc(u8, .@"16", n + 16) catch return 2;
        std.mem.writeInt(usize, bytes[0..@sizeOf(usize)], n + 16, .little);
        out.* = bytes.ptr + 16;
        return 0;
    }
    fn hostFree(ptr: ?*anyopaque) callconv(.c) c_int {
        const base: [*]align(16) u8 = @alignCast(@as([*]u8, @ptrCast(ptr.?)) - 16);
        t.allocator.free(base[0..std.mem.readInt(usize, base[0..@sizeOf(usize)], .little)]);
        return 0;
    }
    fn eventCreate(out: *cuda.abi.Event, _: c_uint) callconv(.c) c_int {
        events += 1;
        live_events += 1;
        out.* = @ptrFromInt(events);
        return 0;
    }
    fn eventRecord(_: cuda.abi.Event, _: cuda.abi.Stream) callconv(.c) c_int {
        return 0;
    }
    fn eventDone(_: cuda.abi.Event) callconv(.c) c_int {
        return 0;
    }
    fn eventDestroy(_: cuda.abi.Event) callconv(.c) c_int {
        live_events -= 1;
        return 0;
    }
    fn at(ctx: *anyopaque, _: u32) void {
        const e: *Engine = @ptrCast(@alignCast(ctx));
        marked += 1;
        _ = e.keepState() catch |err| {
            mark_error = err;
            return;
        };
    }
};

const Box = struct {
    d: cuda.Driver = undefined,
    lib: cuda.nccl.Library = undefined,
    e: Engine = undefined,
    q: Seq = undefined,
    parity: [2]u1 = .{ 0, 0 },
    rings: [2]u64 = .{ 0x500000, 0x600000 },
    layers: [2]glm.weights.Layer = undefined,
    link: glm.follow.Link = undefined,
    ids: [C + 1]u32 = @splat(7),
    wire_ids: [C + 1]u32 = undefined,

    fn init(b: *Box) void {
        Record.reset();
        b.d.api.cuCtxGetDevice = Record.device;
        b.d.api.cuDeviceGetAttribute = Record.attr;
        b.d.api.cuLaunchKernel = Record.launch;
        b.d.api.cuMemcpyHtoDAsync_v2 = Record.upload;
        b.d.api.cuMemcpyDtoH_v2 = Record.download;
        b.d.api.cuStreamSynchronize = Record.sync;
        b.d.api.cuMemsetD32Async = Record.memset32;
        b.d.api.cuMemsetD8Async = Record.memset8;
        b.d.api.cuMemHostAlloc = Record.hostAlloc;
        b.d.api.cuMemFreeHost = Record.hostFree;
        b.d.api.cuEventCreate = Record.eventCreate;
        b.d.api.cuEventRecord = Record.eventRecord;
        b.d.api.cuEventQuery = Record.eventDone;
        b.d.api.cuEventDestroy_v2 = Record.eventDestroy;
        b.lib.api.ncclAllReduce = Record.reduce;
        b.lib.api.ncclAllGather = Record.gather;
        b.lib.api.ncclBroadcast = Record.broadcast;
        const e = &b.e;
        e.gpa = t.allocator;
        e.io = t.io;
        e.d = &b.d;
        e.stream = .{ .d = &b.d, .handle = @ptrFromInt(1) };
        e.lib = &b.lib;
        e.comm = null;
        e.group = null;
        e.share.rank = 0;
        e.share.world = 1;
        e.images = null;
        e.pair = null;
        e.drafter = null;
        e.side = null;
        e.ce = null;
        e.moe_fuse = false;
        e.settings = .{};
        e.settings.split = false;
        e.pieces.count = 1;
        e.index_split = false;
        e.index_splits = 0;
        e.prompt_profs = @splat(null);
        e.profiling_prompt = null;
        e.heat_gate = .{};
        e.shared_buf = .{ .d = &b.d, .ptr = Record.shared, .len = 8, .borrowed = true };
        e.dropped = .empty;
        e.announce = 0;
        e.snaps = .empty;
        e.next_snap = 1;
        e.snap_pages = null;
        e.mtp = false;
        e.prompt_logprobs = null;
        e.partial = null;
        e.slice_costs = .{ .gpa = t.allocator };
        e.slice_cal = null;
        e.slot = 0;
        e.cur = &b.q;
        b.q.st.capacity = 4 * C;
        b.q.st.pos = C;
        b.q.st.base = 0;
        b.q.st.ring_rows = 4 * C;
        b.q.st.cur = &b.parity;
        b.q.st.kda_layers = 2;
        b.q.st.heads = 1;
        b.q.st.conv_c = 3 * 128;
        b.q.st.rec_layer = 128 * 128 * 4;
        b.q.st.conv = 0x100000;
        b.q.st.rec = 0x200000;
        b.q.st.pending = 0;
        b.q.st.mtp_len = 0;
        b.q.st.pos_dev = 0x3000;
        b.q.st.mtp_pos_dev = 0x4000;
        b.q.st.arena.buf = .{ .d = &b.d, .ptr = 0x100000, .len = 0x400000, .borrowed = true };
        b.q.st.caches = &.{};
        b.q.st.rings = &b.rings;
        b.q.st.ring_row = 64;
        e.c.hidden = 4096;
        e.c.hc = 4;
        e.c.eps = 1e-5;
        e.c.hc_eps = 1e-5;
        e.c.sinkhorn = 1;
        e.w.config = e.c;
        e.w.layers = &b.layers;
        e.w.share = e.share;
        e.w.embed = 0x8000;
        const hc: glm.weights.Hc = .{ .fn_w = 0x10000, .base = 0x20000, .scale = 0x30000 };
        for (&b.layers, 0..) |*l, i| l.* = .{
            .index = @intCast(i),
            .attn_hc = hc,
            .ffn_hc = hc,
            .in_norm = .{ .bf16 = 0x40000 },
            .post_norm = .{ .bf16 = 0x40000 },
            .mixer = .{ .kda = .{ .proj = .{ .bf16 = 0x50000 }, .fb = 0x60000, .gb = 0x70000, .o = .{ .bf16 = 0x80000 }, .conv_w = 0x90000, .a_log = 0xa0000, .dt_bias = 0xb0000, .norm_w = 0xc0000, .eps = 1e-5, .lower = -5, .heads = 1, .dims = 4096 } },
            .ffn = .{ .dense = .{ .w = .{ .bf16 = .{ .gu = 0xd0000, .down = 0xe0000 } }, .width = 512, .dims = 4096, .limit = 0 } },
        };
        const f: cuda.Function = .{ .d = &b.d, .handle = @ptrFromInt(1) };
        inline for (comptime std.meta.fieldNames(glm.glue.Kernels)) |name| @field(e.m.glue, name) = f;
        inline for (comptime std.meta.fieldNames(glm.kda.Kernels)) |name| {
            if (@FieldType(glm.kda.Kernels, name) == cuda.Function) @field(e.m.kda, name) = f;
        }
        e.m.kda.step_warps = 0;
        e.m.kda.step = .{ .d = &b.d, .handle = @ptrFromInt(Record.kda_step) };
        e.m.glue.conv_shift = .{ .d = &b.d, .handle = @ptrFromInt(Record.conv_shift) };
        e.m.index = null;
        e.m.dscore = null;
        e.m.packs = null;
        e.m.tiled = false;
        e.m.dsel = false;
        e.m.mm.chain = .{};
        e.m.hc_split = false;
        e.m.hc_part = .{ .n = 0 };
        e.m.copy_rows = f;
        e.bp.prefill = true;
        e.bp.rows = C;
        inline for (.{ "ids", "x", "normed", "xs", "post", "comb", "hcpart", "kproj", "gathered" }) |name|
            @field(e.bp, name) = 0x1000000;
        e.bp.nch = 1;
        e.bp.kda = .{ .p = 0x1000000, .ka = 0x1100000, .kg = 0x1200000, .saves = .{ .out = 0x1300000, .state_out = 0, .k = 0x1400000, .v = 0x1500000, .g = 0x1600000, .b = 0x1700000 }, .wide = .{ .q = 0x1800000, .y = 0x1900000 }, .mm_part = 0x2000000 };
        e.bp.dense = .{ .tmp = 0x2100000, .gu = 0x2200000, .act = 0x2300000, .xs_act = 0x2400000, .mm_part = 0x2500000 };
        b.link = .{ .d = &b.d, .lib = &b.lib, .comm = null, .stream = e.stream, .buf = .{ .d = &b.d, .ptr = Record.link, .len = 64 + b.wire_ids.len * 4, .borrowed = true }, .ids = &b.wire_ids, .gpa = t.allocator, .wait = true };
    }
    fn deinit(b: *Box) void {
        _ = b.e.dropPartialOf(b.e.slot);
        b.e.slice_costs.deinit();
        if (b.e.slice_cal) |ev| {
            for (ev) |*event| event.deinit();
            t.allocator.free(ev);
        }
        b.e.stream.synchronize() catch unreachable;
    }
    fn start(b: *Box) !void {
        try t.expectEqual(@as(?u32, null), try b.e.prefillChunkSlice(&b.ids, C, 3 * C, null, null, null, 1));
    }
    fn leader(b: *Box) glm.follow.Leader {
        return .{ .e = &b.e, .link = &b.link };
    }
    fn cancel(b: *Box) !void {
        try b.leader().cancelSlice(C, .{ .at = &.{}, .ctx = &b.e, .call = Record.at, .interrupted = Record.at });
    }
};

test "fairness witness: partial KDA changes precede chunk commit and survive actual cancellation" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    try b.start();
    try t.expectEqualSlices(u1, &.{ 1, 0 }, &b.parity);
    try t.expectEqual(C, b.q.st.pos);
    try t.expectEqual(C, b.q.st.pending);
    try t.expectEqual(@as(usize, 1), Record.shifts);
    try t.expectEqual(@as(usize, 1), Record.steps);
    try b.cancel();
    try t.expect(b.e.partial == null);
    try t.expectEqualSlices(u1, &.{ 1, 0 }, &b.parity);
    try t.expectEqual(C, b.q.st.pending);
    try t.expectEqual(@as(?anyerror, error.NotAtMark), Record.mark_error);
    try t.expectEqual(@as(usize, 1), Record.marked);
    try t.expectEqual(@backingInt(glm.follow.Kind.chunkpart), Record.header.kind);
    try t.expectEqual(@as(u64, 0), Record.header.a);
    try t.expectEqual(@as(usize, 1), Record.messages);
    std.debug.print("WITNESS cancellation: parity=1,0 pending={d} pos={d} snapshot=NotAtMark\n", .{ b.q.st.pending, b.q.st.pos });
}

test "fairness safety: cancellation restores the preceding committed prefix" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    try b.start();
    try b.cancel();
    try t.expectEqual(C, b.q.st.pos);
    try t.expectEqualSlices(u1, &.{ 0, 0 }, &b.parity);
    try t.expectEqual(@as(usize, 0), b.q.st.pending);
}

test "fairness witness: retry after cancellation consumes the cancelled layer output" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    try b.start();
    try b.cancel();
    try b.start();
    try t.expectEqual(@as(usize, 2), Record.steps);
    try t.expectEqual(Record.outputs[0], Record.inputs[1]);
    try t.expect(Record.inputs[0] != Record.inputs[1]);
    std.debug.print("WITNESS retry: original state=0x{x} cancelled output=0x{x} retry input=0x{x}\n", .{ Record.inputs[0], Record.outputs[0], Record.inputs[1] });
}

test "fairness safety: retry after cancellation consumes the original layer input" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    try b.start();
    try b.cancel();
    try b.start();
    try t.expectEqual(Record.inputs[0], Record.inputs[1]);
}

test "fairness control: explicit new begin resets recurrence and permits a fresh request" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    try b.start();
    try b.cancel();
    try b.e.begin(null);
    b.q.st.pos = C; // This control checks only the recurrent reset, not a claimed prefix restore.
    try b.start();
    try t.expectEqual(Record.inputs[0], Record.inputs[1]);
}

test "fairness witness: a cold sliceHi schedules all layers and calibration survives drop" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    try t.expectEqual(@as(usize, 2), try b.e.sliceHi(C, 0, 0.1));
    try t.expectEqual(@as(usize, 3), Record.live_events);
    b.e.partial = .{ .slot = 0, .start = C, .len = 3 * C, .j = 1, .rows = C, .part = try t.allocator.dupe(u32, &b.ids), .split = false };
    try t.expect(b.e.dropPartialOf(0));
    try t.expectEqual(@as(usize, 3), Record.live_events);
}

test "fairness safety: repeated twice-baseline observations converge to two" {
    var costs: glm.engine.LayerCosts = .{ .gpa = t.allocator };
    defer costs.deinit();
    try costs.calibrate(C, &.{ 1, 1, 0 });
    for (0..100) |_| costs.observe(C, 0, 2, 4);
    std.debug.print("WITNESS EWMA scale={d}\n", .{costs.scale});
    try t.expectApproxEqAbs(@as(f64, 2), costs.scale, 0.001);
}

fn cancelled(_: ?*anyopaque) bool {
    return true;
}

test "fairness control: whole-chunk cancellation keeps all layers at the completed boundary" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    try t.expectError(error.Cancelled, b.e.prefillChunk(&b.ids, C, 3 * C, null, .{ .ptr = null, .check = cancelled }, null));
    try t.expectEqual(C, b.q.st.pos);
    try t.expectEqualSlices(u1, &.{ 0, 0 }, &b.parity);
    try t.expectEqual(@as(usize, 0), b.q.st.pending);
    try t.expectEqual(@as(usize, 0), Record.shifts);
}

test "fairness safety: the lane cancellation at cached zero drops its suspended chunk" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    b.q.st.pos = 0;
    _ = try b.e.prefillChunkSlice(&b.ids, 0, 3 * C, null, null, null, 1);
    var self: glm.lanes.Lanes = .{ .x = b.leader(), .fill_slice_ms = 1 };
    defer self.deinit();
    var s: lanes.Stream = undefined;
    s.cached = 0;
    s.reuse = .{};
    (try self.claim(&s)).fill = 0;
    const backend = self.backend();
    try backend.vtable.cancel_prefill.?(backend.ptr, &s);
    try t.expect(b.e.partial == null);
}

test "fairness safety: the first calibration call schedules one layer" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    try t.expectEqual(@as(usize, 1), try b.e.sliceHi(C, 0, 0.1));
}

test "fairness safety: nonfinite calibration is refused" {
    var costs: glm.engine.LayerCosts = .{ .gpa = t.allocator };
    defer costs.deinit();
    try t.expectError(error.BadCosts, costs.calibrate(C, &.{ 1, std.math.inf(f64), 0 }));
}

test "fairness safety: allocation failure followed by calibration retry retains no orphan scratch" {
    var fail = std.testing.FailingAllocator.init(t.allocator, .{ .fail_index = 1 });
    var costs: glm.engine.LayerCosts = .{ .gpa = fail.allocator() };
    try t.expectError(error.OutOfMemory, costs.calibrate(C, &.{ 1, 1, 0 }));
    const abandoned = costs.cut;
    fail.fail_index = std.math.maxInt(usize);
    try costs.calibrate(C, &.{ 1, 1, 0 });
    const replaced = abandoned.len > 0 and costs.cut.ptr != abandoned.ptr;
    costs.deinit();
    const leaked = fail.allocated_bytes - fail.freed_bytes;
    // Clean the donor's orphan after measuring it, so a known defect does not contaminate the test allocator.
    if (replaced) fail.allocator().free(abandoned);
    std.debug.print("WITNESS allocation retry: orphan bytes={d}\n", .{leaked});
    try t.expectEqual(@as(usize, 0), leaked);
}

test "fairness witness: failed partial allocation follows the forward header with no recovery header" {
    var b: Box = .{};
    b.init();
    defer b.deinit();
    try b.e.slice_costs.calibrate(C, &.{ 1, 1, 0 });
    var fail = std.testing.FailingAllocator.init(t.allocator, .{ .fail_index = 0 });
    b.e.gpa = fail.allocator();
    const prompt = try t.allocator.alloc(u32, 3 * C);
    defer t.allocator.free(prompt);
    @memset(prompt, 7);
    try t.expectError(error.OutOfMemory, b.leader().chunkSliced(prompt, C, false, null, 0.1, null, null));
    const published_on_failure = b.e.partial != null;
    b.e.partial = null; // The failed struct assignment can publish an incomplete payload; do not inspect or free it.
    try t.expect(published_on_failure);
    try t.expectEqual(@as(usize, 2), Record.messages); // header and full IDs, no cancellation or release.
    try t.expectEqual(@backingInt(glm.follow.Kind.chunkpart), Record.header.kind);
    try t.expectEqual(@as(u64, 1), Record.header.a);
    try t.expectEqual(@as(usize, 0), Record.steps);
    std.debug.print("WITNESS allocation: chunkpart announced, incomplete partial published, no KDA dispatch and no recovery header\n", .{});
}
