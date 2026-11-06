// Appended to the existing API fixture so the real routes, renderer and stream translators are exercised.
const MediaFinish = struct {
    reason: api.Reason,

    fn engine(s: *MediaFinish) api.Engine {
        return .{ .ctx = s, .vtable = &.{ .info = Script.info, .submit = submit, .cancel = Script.cancel, .status = Script.status, .memory = Script.memory } };
    }

    fn submit(ctx: *anyopaque, id: api.Id, _: *const api.Request, sink: api.Sink) api.SubmitError!void {
        const s: *MediaFinish = @ptrCast(@alignCast(ctx));
        sink.event(sink.ctx, id, &.{ .finished = .{
            .reason = s.reason,
            .message = if (s.reason == .refused) "image or video preparation exceeded its time budget" else "scripted internal fault",
        } });
    }
};

test "media request errors: whole and streamed failures retain their class across all three APIs" {
    for ([_]api.Reason{ .refused, .failed }) |reason| for ([_]bool{ false, true }) |streaming| for ([_][]const u8{
        "/v1/chat/completions", "/v1/messages", "/v1/responses",
    }) |path| {
        var arena: std.heap.ArenaAllocator = .init(t.allocator);
        defer arena.deinit();
        const a = arena.allocator();
        var h: Harness = undefined;
        try h.init(a);
        defer h.deinit();
        var ending: MediaFinish = .{ .reason = reason };
        h.srv.engine = ending.engine();
        const body = (try json.parse(a, if (std.mem.eql(u8, path, "/v1/responses"))
            "{\"model\":\"arbitrary-name\",\"input\":\"search\",\"max_output_tokens\":64}"
        else
            "{\"model\":\"arbitrary-name\",\"messages\":[{\"role\":\"user\",\"content\":\"search\"}],\"max_tokens\":64}")).ok;
        try body.object.put(a, "stream", .{ .bool = streaming });
        const wire = try h.send(path, body, null);
        const request_marker = if (streaming and std.mem.eql(u8, path, "/v1/responses")) "invalid_prompt" else "invalid_request_error";
        if (reason == .refused) {
            if (std.mem.indexOf(u8, wire.bytes.items, request_marker) == null)
                std.debug.print("media refusal path={s} stream={} wire={s}\n", .{ path, streaming, wire.bytes.items });
            try contains(wire.bytes.items, request_marker);
            try contains(wire.bytes.items, "image or video preparation exceeded its time budget");
            try absent(wire.bytes.items, "server_error");
            try absent(wire.bytes.items, "MediaDeadline");
            if (!streaming) try t.expect(std.mem.startsWith(u8, wire.bytes.items, "HTTP/1.1 400 "));
        } else {
            try contains(wire.bytes.items, "scripted internal fault");
            try absent(wire.bytes.items, request_marker);
            if (!streaming) try t.expect(std.mem.startsWith(u8, wire.bytes.items, "HTTP/1.1 500 "));
        }
    };
}
