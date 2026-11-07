const std = @import("std");
const h = @import("prepare_harness.zig");
const t = std.testing;
const simple = "{\"model\":\"m\",\"prompt\":\"hello\",\"max_tokens\":2}";

fn noScratch(s: *h.Harness) !void {
    for ([_]h.memory.Kind{ .render, .tokenize, .media, .prompt }) |kind| {
        const c = &s.srv.prepare_memory.of(kind).count;
        try t.expectEqual(@as(usize, 0), c.live.load(.acquire));
        try t.expect(c.peak.load(.acquire) > 0);
    }
}

test "prepare memory: four delayed consumers keep media alive while eight useful arrivals wait before allocation" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&engine, .{ .residents = 4, .waiting = 32 });
    defer srv.deinit();
    var sockets: [12]h.posix.socket_t = undefined;
    const raw = try h.wire(a, "/v1/chat/completions", try h.requestBody(a, "/v1/chat/completions", false));
    for (sockets[0..4]) |*fd| fd.* = try srv.begin(raw);
    defer for (sockets) |fd| h.close(fd);
    try h.waitCount(&engine.active, 4);
    try noScratch(&srv);
    const retained = srv.srv.prepare_memory.of(.retained).count.live.load(.acquire);
    try t.expect(retained > 0);
    for (sockets[4..]) |*fd| fd.* = try srv.begin(raw);
    try srv.wait("waiting", 8);
    try t.expectEqual(retained, srv.srv.prepare_memory.of(.retained).count.live.load(.acquire));
    try t.expectEqual(@as(u32, 4), engine.calls.load(.acquire));
    engine.release.store(true, .release);
    for (sockets) |fd| try h.expectStatus(try h.receive(a, fd), "HTTP/1.1 200 ");
    try srv.quiet();
    try t.expectEqual(@as(u32, 12), engine.calls.load(.acquire));
    try t.expectEqual(@as(u32, 4), engine.peak.load(.acquire));
    try t.expectEqual(@as(u32, 24), engine.checked_images.load(.acquire));
    const snap = srv.srv.prepare_gate.snapshot(t.io);
    try t.expectEqual(@as(u64, 0), snap.full);
    try t.expectEqual(@as(u32, 8), snap.peak_waiting);
    std.debug.print("PREPARE_BURST residents_peak={d} queued_peak={d} retained_at_four={d} consumed_images={d} consumer_peak={d}\n", .{
        snap.peak_residents, snap.peak_waiting, retained, engine.checked_images.load(.acquire), engine.consumer.count.peak.load(.acquire),
    });
}

test "prepare memory: production queue 32 waits before 96 MiB bodies and 100 Continue; cancellation and drain reclaim it" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&engine, .{ .residents = 4, .waiting = 32 });
    defer srv.deinit();
    var running: [4]h.posix.socket_t = undefined;
    for (&running) |*fd| fd.* = try srv.begin(try h.wire(a, "/v1/completions", simple));
    defer for (running) |fd| h.close(fd);
    try h.waitCount(&engine.active, 4);
    const retained = srv.srv.prepare_memory.of(.retained).count.live.load(.acquire);
    const head = "POST /v1/messages HTTP/1.1\r\nHost: localhost\r\nExpect: 100-continue\r\nContent-Length: 100663296\r\nConnection: close\r\n\r\n";
    var waiting: [32]h.posix.socket_t = undefined;
    for (&waiting) |*fd| fd.* = try srv.begin(head);
    defer for (waiting[1..]) |fd| h.close(fd);
    try srv.wait("waiting", 32);
    try t.expectEqual(retained, srv.srv.prepare_memory.of(.retained).count.live.load(.acquire));
    var poll = [_]h.posix.pollfd{.{ .fd = waiting[0], .events = h.posix.POLL.IN, .revents = 0 }};
    try t.expectEqual(@as(usize, 0), try h.posix.poll(&poll, 30));
    const excess = try srv.begin(head);
    defer h.close(excess);
    const refused = try h.receive(a, excess);
    try h.expectStatus(refused, "HTTP/1.1 429 ");
    try t.expect(std.mem.indexOf(u8, refused, "100 Continue") == null);
    try t.expect(std.mem.indexOf(u8, refused, "overloaded_error") != null);
    h.close(waiting[0]);
    try srv.wait("waiting", 31);
    srv.srv.draining.store(true, .seq_cst);
    for (waiting[1..]) |fd| {
        const reply = try h.receive(a, fd);
        try h.expectStatus(reply, "HTTP/1.1 503 ");
        try t.expect(std.mem.indexOf(u8, reply, "100 Continue") == null);
    }
    try srv.wait("waiting", 0);
    try t.expectEqual(@as(u32, 4), engine.active.load(.acquire));
    engine.release.store(true, .release);
    for (running) |fd| try h.expectStatus(try h.receive(a, fd), "HTTP/1.1 200 ");
    try srv.quiet();
    const snap = srv.srv.prepare_gate.snapshot(t.io);
    try t.expectEqual(@as(u64, 1), snap.cancelled);
    try t.expectEqual(@as(u64, 31), snap.drained);
    try t.expectEqual(@as(u64, 1), snap.full);
    std.debug.print("PREPARE_HEADERS queue_peak={d} declared_body=100663296 body_allocated_while_queued=0 cancelled={d} drained={d}\n", .{ snap.peak_waiting, snap.cancelled, snap.drained });
}

test "prepare memory: injected floor waits even with zero residents, recovers, times out and fails closed without telemetry" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&engine, .{ .residents = 4, .floor = 3 << 30, .reserve = 1 << 30, .wait_ms = 120 });
    defer srv.deinit();
    engine.release.store(true, .release);
    h.available.store(1 << 30, .release);
    const fd = try srv.begin(try h.wire(a, "/v1/completions", simple));
    defer h.close(fd);
    try srv.wait("waiting", 1);
    try t.expectEqual(@as(usize, 0), srv.srv.prepare_memory.of(.retained).count.live.load(.acquire));
    h.available.store(16 << 30, .release);
    try h.expectStatus(try h.receive(a, fd), "HTTP/1.1 200 ");
    try srv.quiet();
    h.available.store(1 << 30, .release);
    try h.expectStatus(try srv.request(a, "/v1/messages/count_tokens", "{}"), "HTTP/1.1 503 ");
    h.missing.store(true, .release);
    try h.expectStatus(try srv.request(a, "/v1/tokenize", "{}"), "HTTP/1.1 503 ");
    try srv.quiet();
    const snap = srv.srv.prepare_gate.snapshot(t.io);
    try t.expectEqual(@as(u64, 2), snap.timed_out);
    try t.expectEqual(@as(u64, 1), snap.telemetry_missing);
    try t.expectEqual(@as(u64, 0), snap.reserved_bytes);
}

test "prepare memory: native OpenAI Messages Responses streaming whole counting and tokenize release all scratch and media" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&engine, .{ .residents = 4 });
    defer srv.deinit();
    engine.release.store(true, .release);
    for (0..3) |_| {
        for ([_][]const u8{ "/v1/chat/completions", "/v1/messages", "/v1/responses" }) |path| {
            for ([_]bool{ false, true }) |stream| {
                try h.expectStatus(try srv.request(a, path, try h.requestBody(a, path, stream)), "HTTP/1.1 200 ");
                try srv.quiet();
            }
        }
        for ([_][]const u8{ "/v1/messages/count_tokens", "/v1/tokenize" }) |path| {
            try h.expectStatus(try srv.request(a, path, try h.requestBody(a, path, false)), "HTTP/1.1 200 ");
            try srv.quiet();
        }
    }
    try noScratch(&srv);
    try t.expectEqual(@as(u32, 36), engine.checked_images.load(.acquire));
    var metrics: std.Io.Writer.Allocating = .init(a);
    try srv.srv.prepare_memory.render(&metrics.writer);
    std.debug.print("PREPARE_LEDGER\n{s}", .{metrics.written()});
    const fd = try srv.begin("GET /metrics HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");
    defer h.close(fd);
    const reply = try h.receive(a, fd);
    try t.expect(std.mem.indexOf(u8, reply, "tensorfold_prepare_memory_peak_bytes{kind=\"media\"}") != null);
}

test "prepare memory: warm turns retain the parent admission and arena until delayed native completion" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{ .warm = true };
    var srv: h.Harness = .{};
    try srv.start(&engine, .{ .residents = 4 });
    defer srv.deinit();
    engine.release.store(true, .release);
    for (0..4) |_| try h.expectStatus(try srv.request(a, "/v1/chat/completions", try h.requestBody(a, "/v1/chat/completions", false)), "HTTP/1.1 200 ");
    try h.waitCount(&engine.warm_calls, 4);
    try srv.wait("residents", 4);
    try t.expectEqual(@as(u32, 4), srv.srv.generating.load(.seq_cst));
    try noScratch(&srv);
    const retained = srv.srv.prepare_memory.of(.retained).count.live.load(.acquire);
    try t.expect(retained > 0);
    const later = try srv.begin(try h.wire(a, "/v1/tokenize", "{\"prompt\":\"warm borrows finish first\"}"));
    defer h.close(later);
    try srv.wait("waiting", 1);
    try t.expectEqual(retained, srv.srv.prepare_memory.of(.retained).count.live.load(.acquire));
    engine.release_warm.store(true, .release);
    try h.expectStatus(try h.receive(a, later), "HTTP/1.1 200 ");
    try srv.quiet();
    try t.expectEqual(@as(u32, 16), engine.checked_images.load(.acquire));
}

test "prepare memory: client disconnect engine rejection failure malformed chunked bodies and recovery release tickets" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&engine, .{ .residents = 4 });
    defer srv.deinit();
    const fd = try srv.begin(try h.wire(a, "/v1/chat/completions", try h.requestBody(a, "/v1/chat/completions", true)));
    try h.waitCount(&engine.active, 1);
    h.close(fd);
    try srv.quiet();
    engine.reject.store(true, .release);
    try h.expectStatus(try srv.request(a, "/v1/completions", simple), "HTTP/1.1 503 ");
    engine.reject.store(false, .release);
    engine.release.store(true, .release);
    engine.fail.store(true, .release);
    try h.expectStatus(try srv.request(a, "/v1/completions", simple), "HTTP/1.1 500 ");
    engine.fail.store(false, .release);
    try h.expectStatus(try srv.request(a, "/v1/completions", "{"), "HTTP/1.1 400 ");
    const chunked = try srv.begin("POST /v1/tokenize HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n2\r\n{}\r\n0\r\n\r\n");
    defer h.close(chunked);
    try h.expectStatus(try h.receive(a, chunked), "HTTP/1.1 400 ");
    try h.expectStatus(try srv.request(a, "/v1/completions", simple), "HTTP/1.1 200 ");
    try srv.quiet();
}

test "prepare memory: independent prompt scratch is released before the delayed engine and repeated arenas do not accumulate" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&engine, .{ .residents = 4 });
    defer srv.deinit();
    const text = try a.alloc(u8, 32 << 10);
    @memset(text, 'x');
    const body = try std.fmt.allocPrint(a, "{{\"messages\":[{{\"role\":\"user\",\"content\":\"{s}\"}}],\"max_tokens\":2}}", .{text});
    for (0..3) |_| {
        engine.release.store(false, .release);
        const fd = try srv.begin(try h.wire(a, "/v1/chat/completions", body));
        defer h.close(fd);
        try h.waitCount(&engine.active, 1);
        for ([_]h.memory.Kind{ .render, .tokenize, .prompt }) |kind| {
            const c = &srv.srv.prepare_memory.of(kind).count;
            try t.expectEqual(@as(usize, 0), c.live.load(.acquire));
            try t.expect(c.peak.load(.acquire) > 0);
        }
        const retained = srv.srv.prepare_memory.of(.retained).count.live.load(.acquire);
        try t.expect(retained < text.len * 40);
        std.debug.print("PREPARE_LONG text={d} retained={d} render_peak={d} tokenizer_peak={d} prompt_peak={d}\n", .{
            text.len,                                                       retained,                                                     srv.srv.prepare_memory.of(.render).count.peak.load(.acquire),
            srv.srv.prepare_memory.of(.tokenize).count.peak.load(.acquire), srv.srv.prepare_memory.of(.prompt).count.peak.load(.acquire),
        });
        engine.release.store(true, .release);
        try h.expectStatus(try h.receive(a, fd), "HTTP/1.1 200 ");
        try srv.quiet();
    }
}

test "prepare memory: conservative defaults strict policy parsing telemetry and real allocator failure cleanup" {
    try t.expectEqual(@as(u32, 0), (try h.gate.Policy.fromEnv(null)).residents);
    try t.expectEqual(@as(?u64, 4096), h.gate.parseAvailable("MemAvailable: 4 kB\n"));
    try t.expectEqual(@as(?u64, null), h.gate.parseAvailable("MemAvailable: 4 MB\n"));
    var env: std.process.Environ.Map = .init(t.allocator);
    defer env.deinit();
    for ([_][]const u8{ "-1", "nan", "129", "1.5" }) |bad| {
        try env.put("TF_PREPARE_RESIDENTS", bad);
        try t.expectError(error.BadPreparePolicy, h.gate.Policy.fromEnv(&env));
    }
    try env.put("TF_PREPARE_RESIDENTS", "4");
    try env.put("TF_PREPARE_FLOOR_MIB", "3072");
    try t.expectError(error.BadPreparePolicy, h.gate.Policy.fromEnv(&env));
    try env.put("TF_PREPARE_RESERVE_MIB", "1024");
    try t.expectEqual(@as(u64, 3 << 30), (try h.gate.Policy.fromEnv(&env)).floor);
    var failing = std.testing.FailingAllocator.init(t.allocator, .{ .fail_index = 1 });
    var ledger: h.memory.Ledger = .init(failing.allocator());
    const request = try h.memory.Request.create(failing.allocator(), t.io, &ledger, null, 0);
    try t.expectError(error.OutOfMemory, request.arena.allocator().alloc(u8, 4096));
    request.release();
    try t.expectEqual(@as(usize, 0), ledger.of(.retained).count.live.load(.acquire));
}

test "prepare memory: FIFO admission preserves order after a waiting client cancels" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&engine, .{ .residents = 4 });
    defer srv.deinit();
    var active: [4]h.posix.socket_t = undefined;
    for (&active) |*fd| fd.* = try srv.begin(try h.wire(a, "/v1/completions", simple));
    defer for (active[1..]) |fd| h.close(fd);
    try h.waitCount(&engine.active, 4);
    const cancelled = try srv.begin(try h.wire(a, "/v1/completions", simple));
    try srv.wait("waiting", 1);
    const first = try srv.begin(try h.wire(a, "/v1/completions", "{\"prompt\":\"A\",\"max_tokens\":1}"));
    try srv.wait("waiting", 2);
    const second = try srv.begin(try h.wire(a, "/v1/completions", "{\"prompt\":\"B\",\"max_tokens\":1}"));
    defer h.close(second);
    try srv.wait("waiting", 3);
    h.close(cancelled);
    try srv.wait("waiting", 2);
    h.close(active[0]);
    try h.waitCount(&engine.calls, 5);
    try srv.wait("waiting", 1);
    const expected = try srv.files.hf.text().encode(a, "A", true);
    try t.expectEqual(std.hash.Wyhash.hash(0, std.mem.sliceAsBytes(expected)), engine.last_input.load(.acquire));
    h.close(first);
    try h.waitCount(&engine.calls, 6);
    try srv.wait("waiting", 0);
    const next = try srv.files.hf.text().encode(a, "B", true);
    try t.expectEqual(std.hash.Wyhash.hash(0, std.mem.sliceAsBytes(next)), engine.last_input.load(.acquire));
    engine.release.store(true, .release);
    for (active[1..]) |fd| try h.expectStatus(try h.receive(a, fd), "HTTP/1.1 200 ");
    try h.expectStatus(try h.receive(a, second), "HTTP/1.1 200 ");
    try srv.quiet();
}

test "prepare memory: allocation failure in each real route phase releases admission and later requests recover" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&engine, .{ .residents = 4 });
    defer srv.deinit();
    engine.release.store(true, .release);
    const payload = try h.requestBody(a, "/v1/chat/completions", false);
    for ([_]h.memory.Kind{ .retained, .prompt, .render, .tokenize, .media }) |kind| {
        var failing = std.testing.FailingAllocator.init(t.allocator, .{ .fail_index = 0 });
        srv.srv.prepare_memory.of(kind).child = failing.allocator();
        const before = engine.calls.load(.acquire);
        const fd = try srv.begin(try h.wire(a, "/v1/chat/completions", payload));
        defer h.close(fd);
        _ = try h.receive(a, fd);
        try srv.quiet();
        try t.expect(failing.has_induced_failure);
        try t.expectEqual(before, engine.calls.load(.acquire));
        srv.srv.prepare_memory.of(kind).child = t.allocator;
        try h.expectStatus(try srv.request(a, "/v1/chat/completions", payload), "HTTP/1.1 200 ");
        try srv.quiet();
    }
}

test "prepare memory: disabled policy keeps more than four accepted consumers and needs no memory telemetry" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var engine: h.Engine = .{};
    var srv: h.Harness = .{};
    try srv.start(&engine, .{});
    defer srv.deinit();
    h.missing.store(true, .release);
    var sockets: [6]h.posix.socket_t = undefined;
    for (&sockets) |*fd| fd.* = try srv.begin(try h.wire(a, "/v1/completions", simple));
    defer for (sockets) |fd| h.close(fd);
    try h.waitCount(&engine.active, 6);
    try srv.wait("residents", 0);
    engine.release.store(true, .release);
    for (sockets) |fd| try h.expectStatus(try h.receive(a, fd), "HTTP/1.1 200 ");
    try srv.quiet();
}
