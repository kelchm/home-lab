//! Run against the patched native server, its actual HfText renderer, and the bundled production template.
const std = @import("std");
const json = @import("json");
const api = @import("engine_api");
const Server = @import("server.zig").Server;
const model_text = @import("model_text.zig");
const HfText = @import("hf_text.zig").HfText;
const Conn = @import("http_conn.zig").Conn;
const prompt = @import("prompt.zig");
const Cx = @import("errors.zig").Cx;
const Cache = @import("reasoning_cache.zig").Cache;
const t = std.testing;
const Value = json.Value;
const template = @embedFile("native_api_template.jinja");
const tools_json =
    \\[{"type":"function","function":{"name":"lookup","parameters":{"type":"object","properties":{"q":{"type":"string"}}}}}]
;
const completed = "first reasoning</think><tool_call>lookup<arg_key>q</arg_key><arg_value>same</arg_value></tool_call>" ++
    "<tool_call>lookup<arg_key>q</arg_key><arg_value>same</arg_value></tool_call><|im_end|>";

const Script = struct {
    a: std.mem.Allocator,
    text: model_text.Text,
    reply: []const u32 = &.{},
    seen: []const u8 = "",
    requests: usize = 0,
    stop: bool = true,
    fail: bool = false,

    fn engine(s: *Script) api.Engine {
        return .{ .ctx = s, .vtable = &.{ .info = info, .submit = submit, .cancel = cancel, .status = status, .memory = memory } };
    }
    fn info(_: *anyopaque) api.Info {
        return .{ .context_window = 131072 };
    }
    fn submit(ctx: *anyopaque, id: api.Id, request: *const api.Request, sink: api.Sink) api.SubmitError!void {
        const s: *Script = @ptrCast(@alignCast(ctx));
        s.seen = s.text.decode(s.a, request.prompt) catch return error.MemoryPressure;
        s.requests += 1;
        sink.event(sink.ctx, id, &.{ .prefilled = 0 });
        for (s.reply) |token| sink.event(sink.ctx, id, &.{ .tokens = &.{token} });
        if (s.fail) {
            sink.event(sink.ctx, id, &.{ .finished = .{ .reason = .failed, .message = "scripted failure" } });
        } else sink.event(sink.ctx, id, &.{ .finished = .{ .reason = if (s.stop) .stop else .length } });
    }
    fn cancel(_: *anyopaque, _: api.Id) void {}
    fn status(_: *anyopaque, out: *api.Status, _: []u32) void {
        out.* = .{};
    }
    fn memory(_: *anyopaque, _: bool) ?api.Memory {
        return null;
    }
};

const Wire = struct {
    a: std.mem.Allocator,
    bytes: std.ArrayList(u8) = .empty,
    reject: ?[]const u8 = null,
    fn write(ctx: *anyopaque, bytes: []const u8) error{Closed}!void {
        const w: *Wire = @ptrCast(@alignCast(ctx));
        if (w.reject) |match| if (std.mem.indexOf(u8, bytes, match) != null) return error.Closed;
        w.bytes.appendSlice(w.a, bytes) catch return error.Closed;
    }
    fn body(w: *const Wire) []const u8 {
        const at = std.mem.indexOf(u8, w.bytes.items, "\r\n\r\n") orelse return "";
        return w.bytes.items[at + 4 ..];
    }
    fn value(w: *const Wire) !Value {
        return (try json.parse(w.a, w.body())).ok;
    }
    fn ok(w: *const Wire) bool {
        return std.mem.startsWith(u8, w.bytes.items, "HTTP/1.1 200 ");
    }
};

const Harness = struct {
    a: std.mem.Allocator,
    fixture: @import("control_tokens_fixture.zig").Fixture,
    hf: *HfText,
    script: Script,
    srv: *Server,

    fn init(h: *Harness, a: std.mem.Allocator) !void {
        var fixture = try @import("control_tokens_fixture.zig").Fixture.init(a, 1);
        errdefer fixture.deinit();
        // Fill the synthetic vocabulary's sparse IDs so the production EOS validator can accept its stop token.
        const tokenizer = (try json.parse(a, @embedFile("fixtures/control_tokens/mini_tokenizer.json"))).ok;
        try tokenizer.object.put(a, "pre_tokenizer", (try json.parse(a, "{\"type\":\"ByteLevel\",\"add_prefix_space\":false,\"use_regex\":true}")).ok);
        const vocab = tokenizer.get("model").?.get("vocab").?.object;
        var present: [1014]bool = @splat(false);
        for (vocab.values()) |value| {
            present[@intCast(value.int64().?)] = true;
        }
        for (tokenizer.get("added_tokens").?.array) |token| {
            const id = token.get("id").?;
            try vocab.put(a, token.strField("content").?, id);
            present[@intCast(id.int64().?)] = true;
        }
        for (present, 0..) |exists, id| if (!exists) {
            try vocab.put(a, try std.fmt.allocPrint(a, "<fixture-pad-{d}>", .{id}), try json.intValue(a, id));
        };
        try fixture.tmp.dir.writeFile(t.io, .{ .sub_path = "tokenizer.json", .data = try json.stringify(a, tokenizer, .{}) });
        try fixture.tmp.dir.writeFile(t.io, .{ .sub_path = "chat_template.jinja", .data = template });
        try fixture.tmp.dir.writeFile(t.io, .{ .sub_path = "config.json", .data = "{\"model_type\":\"glm5_next\",\"eos_token_id\":1001}" });
        var problem: []const u8 = "";
        const hf = try HfText.load(t.allocator, t.io, fixture.path, null, a, &problem);
        errdefer hf.deinit();
        h.a = a;
        h.fixture = fixture;
        h.hf = hf;
        h.script = .{ .a = a, .text = hf.text() };
        h.srv = try Server.init(t.allocator, t.io, h.script.engine(), hf.text(), .{ .served_name = "arbitrary-name", .model_ids = &.{"arbitrary-name"}, .use_drafts = false }, null);
        try h.output(completed, true);
    }
    fn deinit(h: *Harness) void {
        h.srv.deinit();
        h.hf.deinit();
        h.fixture.deinit();
    }
    fn output(h: *Harness, text: []const u8, stop: bool) !void {
        h.script.reply = try h.hf.text().encode(h.a, text, false);
        h.script.stop = stop;
    }
    fn request(h: *Harness, streaming: bool) !Value {
        const body = (try json.parse(h.a, "{\"messages\":[{\"role\":\"user\",\"content\":\"search\"}],\"max_tokens\":512}")).ok;
        try body.object.put(h.a, "tools", (try json.parse(h.a, tools_json)).ok);
        try body.object.put(h.a, "stream", .{ .bool = streaming });
        return body;
    }
    fn send(h: *Harness, path: []const u8, body: Value, reject: ?[]const u8) !Wire {
        // Every request gets a separate arena; the cache must own copies after this returns.
        var request_arena: std.heap.ArenaAllocator = .init(t.allocator);
        defer request_arena.deinit();
        const a = request_arena.allocator();
        const data = try json.stringify(a, body, .{});
        var conn = try Conn.init(a, -1, "fixture");
        defer conn.deinit();
        var wire: Wire = .{ .a = h.a, .reject = reject };
        conn.write_hooks = .{ .ctx = &wire, .write = Wire.write };
        conn.path = path;
        conn.version = "HTTP/1.1";
        conn.quiet_log = true;
        var headers = [_]@import("http_conn.zig").Header{.{ .name = "Content-Length", .value = try std.fmt.allocPrint(a, "{d}", .{data.len}) }};
        conn.headers = &headers;
        @memcpy(conn.buf[0..data.len], data);
        conn.end = data.len;
        if (std.mem.eql(u8, path, "/v1/models")) try @import("status_routes.zig").models(h.srv, &conn, a) else if (std.mem.eql(u8, path, "/v1/tokenize")) @import("tokens.zig").post(h.srv, &conn, a, false) else if (std.mem.eql(u8, path, "/v1/responses")) @import("responses.zig").post(h.srv, &conn, a) else if (std.mem.startsWith(u8, path, "/v1/messages")) @import("anthropic.zig").post(h.srv, &conn, a) else @import("openai.zig").post(h.srv, &conn, a, true);
        return wire;
    }
    fn continuation(h: *Harness, calls: Value, reason: ?[]const u8) !Value {
        const body = try h.request(false);
        const assistant = try json.newObject(h.a);
        try assistant.put(h.a, "role", .{ .string = "assistant" });
        try assistant.put(h.a, "content", .null);
        try assistant.put(h.a, "tool_calls", calls);
        if (reason) |r| try assistant.put(h.a, "reasoning_content", .{ .string = r });
        var messages: std.ArrayList(Value) = .empty;
        try messages.appendSlice(h.a, body.get("messages").?.array);
        try messages.append(h.a, .{ .object = assistant });
        for (calls.array) |call| {
            const tool = try json.newObject(h.a);
            try tool.put(h.a, "role", .{ .string = "tool" });
            try tool.put(h.a, "tool_call_id", call.get("id").?);
            try tool.put(h.a, "content", .{ .string = "RESULT" });
            try messages.append(h.a, .{ .object = tool });
        }
        try body.object.put(h.a, "messages", .{ .array = messages.items });
        return body;
    }
};

fn callsFrom(a: std.mem.Allocator, wire: Wire, streaming: bool) !Value {
    if (!streaming) return (try wire.value()).get("choices").?.array[0].get("message").?.get("tool_calls").?;
    var out: std.ArrayList(Value) = .empty;
    var lines = std.mem.splitScalar(u8, wire.body(), '\n');
    while (lines.next()) |line| {
        if (!std.mem.startsWith(u8, line, "data: ") or std.mem.eql(u8, line[6..], "[DONE]")) continue;
        const payload = (try json.parse(a, line[6..])).ok;
        const choices = payload.get("choices") orelse continue;
        for (choices.array) |choice| if (choice.get("delta")) |delta| if (delta.get("tool_calls")) |calls| {
            for (calls.array) |call| {
                const index: usize = @intCast(call.get("index").?.int64().?);
                if (index == out.items.len) try out.append(a, try json.deepCopy(a, call)) else {
                    const function = out.items[index].get("function").?.object;
                    const extra = call.get("function").?.strField("arguments") orelse "";
                    try function.put(a, "arguments", .{ .string = try std.mem.concat(a, u8, &.{ function.get("arguments").?.string, extra }) });
                }
            }
        };
    }
    return .{ .array = out.items };
}

fn contains(hay: []const u8, needle: []const u8) !void {
    try t.expect(std.mem.indexOf(u8, hay, needle) != null);
}
fn absent(hay: []const u8, needle: []const u8) !void {
    try t.expect(std.mem.indexOf(u8, hay, needle) == null);
}

test "native API parity: actual template discovery and on/off rendered prompts agree" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var h: Harness = undefined;
    try h.init(a);
    defer h.deinit();
    try absent(template, "enable_thinking");
    try absent(template, "thinking_mode");
    try t.expect(h.srv.thinking_switch);
    try t.expect(h.srv.config.enable_thinking);
    try t.expect(!h.srv.config.loop_guard);
    try t.expectEqual(@as(i64, 0), h.srv.config.thinking_budget);
    try t.expectEqual(@as(?bool, false), h.hf.clear_thinking);
    const models = try (try h.send("/v1/models", .null, null)).value();
    const reasoning = models.get("data").?.array[0].get("reasoning").?;
    try t.expect(reasoning.get("default_enabled").?.bool);
    var none = false;
    for (reasoning.get("supported_efforts").?.array) |effort| {
        if (std.mem.eql(u8, effort.string, "none")) none = true;
    }
    try t.expect(none);
    var cx: Cx = .{ .a = a };
    const body = try h.request(false);
    const on = try prompt.renderIds(h.srv, &cx, body.get("messages").?, &.{}, true, "max", true);
    const off = try prompt.renderIds(h.srv, &cx, body.get("messages").?, &.{}, false, "max", true);
    try t.expectEqual(on.len + 1, off.len);
    try t.expectEqualSlices(u32, on, off[0..on.len]);
    try t.expect(std.mem.endsWith(u8, try h.hf.text().decode(a, on), "<|assistant|><think>"));
    try t.expect(std.mem.endsWith(u8, try h.hf.text().decode(a, off), "<|assistant|><think></think>"));
    try body.object.put(a, "reasoning_effort", .{ .string = "none" });
    const got = try h.send("/v1/chat/completions", body, null);
    try t.expect(got.ok());
    try t.expect(std.mem.endsWith(u8, h.script.seen, "<|assistant|><think></think>"));
}

test "native API parity: whole and streamed original IDs restore parallel calls across request arenas" {
    for ([_]bool{ false, true }) |streaming| {
        var arena: std.heap.ArenaAllocator = .init(t.allocator);
        defer arena.deinit();
        const a = arena.allocator();
        var h: Harness = undefined;
        try h.init(a);
        defer h.deinit();
        const first = try h.send("/v1/chat/completions", try h.request(streaming), null);
        try t.expect(first.ok());
        const calls = try callsFrom(a, first, streaming);
        try t.expectEqual(@as(usize, 2), calls.array.len);
        try t.expectEqual(@as(usize, 1), h.srv.reasoning.count);
        for (calls.array) |call| try t.expect(h.srv.reasoning.by_id.contains(call.get("id").?.string));
        const parsed = try @import("tool_parse.zig").parse(a, "<tool_call>lookup<arg_key>q</arg_key><arg_value>same</arg_value></tool_call>", (try json.parse(a, tools_json)).ok.array, null);
        try t.expect(!h.srv.reasoning.by_id.contains(parsed.calls.?[0].strField("id").?));
        try h.output("done<|im_end|>", true);
        for ([_]?[]const u8{ null, "", " \t\n" }) |blank| {
            const follow = try h.continuation(calls, blank);
            const response = try h.send("/v1/chat/completions", follow, null);
            try t.expect(response.ok());
            try contains(h.script.seen, "<think>first reasoning</think>");
            try contains(h.script.seen, "<tool_response>RESULT</tool_response>");
            try absent(h.script.seen, "None");
            try absent(h.script.seen, "null<tool_call>");
            const tokenized = try (try h.send("/v1/tokenize", follow, null)).value();
            const ids = try a.alloc(u32, tokenized.get("tokens").?.array.len);
            for (tokenized.get("tokens").?.array, ids) |token, *slot| slot.* = @intCast(token.int64().?);
            try t.expectEqualStrings(h.script.seen, try h.hf.text().decode(a, ids));
        }
        const caller = try h.continuation(calls, "caller wins");
        _ = try h.send("/v1/chat/completions", caller, null);
        try contains(h.script.seen, "<think>caller wins</think>");
        try absent(h.script.seen, "first reasoning");
        const alias = try h.continuation(calls, "");
        try alias.get("messages").?.array[1].object.put(a, "reasoning", .{ .string = "alias wins" });
        _ = try h.send("/v1/chat/completions", alias, null);
        try contains(h.script.seen, "<think>alias wins</think>");
        try absent(h.script.seen, "first reasoning");
    }
}

test "native API parity: repeated identical requests never restore by signatures or renumbered IDs" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var h: Harness = undefined;
    try h.init(a);
    defer h.deinit();
    const first = try callsFrom(a, try h.send("/v1/chat/completions", try h.request(false), null), false);
    try h.output("other reasoning</think><tool_call>lookup<arg_key>q</arg_key><arg_value>same</arg_value></tool_call><tool_call>lookup<arg_key>q</arg_key><arg_value>same</arg_value></tool_call><|im_end|>", true);
    const second = try callsFrom(a, try h.send("/v1/chat/completions", try h.request(false), null), false);
    try h.output("done<|im_end|>", true);
    for ([_]Value{ first, second }, [_][]const u8{ "first reasoning", "other reasoning" }) |calls, expected| {
        _ = try h.send("/v1/chat/completions", try h.continuation(calls, null), null);
        try contains(h.script.seen, expected);
    }
    for ([_][]const u8{ "renumbered", "call_000000000000000000000000" }) |id| {
        const changed = try json.deepCopy(a, first);
        try changed.array[0].object.put(a, "id", .{ .string = id });
        _ = try h.send("/v1/chat/completions", try h.continuation(changed, null), null);
        try absent(h.script.seen, "first reasoning");
        try absent(h.script.seen, "other reasoning");
    }
}

test "native API parity: unknown tool-result IDs refuse before admission and missing IDs remain lossless" {
    var arena: std.heap.ArenaAllocator = .init(t.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var h: Harness = undefined;
    try h.init(a);
    defer h.deinit();
    const calls = try callsFrom(a, try h.send("/v1/chat/completions", try h.request(false), null), false);
    const follow = try h.continuation(calls, null);
    const result = follow.get("messages").?.array[2].object;
    try result.put(a, "tool_call_id", .{ .string = "unknown" });
    const before = h.script.requests;
    const refused = try h.send("/v1/chat/completions", follow, null);
    try contains(refused.bytes.items, "400 Bad Request");
    try contains(refused.bytes.items, "does not match the preceding assistant tool_calls");
    try t.expectEqual(before, h.script.requests);
    try result.put(a, "tool_call_id", .{ .string = "" });
    try result.put(a, "content", .{ .string = "MISSING-ID-PRESERVED" });
    const good = try h.send("/v1/chat/completions", follow, null);
    try t.expect(good.ok());
    try contains(h.script.seen, "MISSING-ID-PRESERVED");
    const without_ids = try json.deepCopy(a, calls);
    for (without_ids.array) |call| _ = call.object.fetchOrderedRemove("id");
    try follow.get("messages").?.array[1].object.put(a, "tool_calls", without_ids);
    try result.put(a, "tool_call_id", .{ .string = "unknown-but-unsorted" });
    try result.put(a, "content", .{ .string = "UNSORTED-PRESERVED" });
    const unsorted = try h.send("/v1/chat/completions", follow, null);
    try t.expect(unsorted.ok());
    try contains(h.script.seen, "UNSORTED-PRESERVED");
}

test "native API parity: truncated failed and undelivered replies never enter the cache" {
    for ([_]bool{ false, true }) |streaming| for (0..6) |case| {
        var arena: std.heap.ArenaAllocator = .init(t.allocator);
        defer arena.deinit();
        const a = arena.allocator();
        var h: Harness = undefined;
        try h.init(a);
        defer h.deinit();
        var reject: ?[]const u8 = null;
        switch (case) {
            0 => try h.output("reason</think><tool_call>lookup<arg_key>q</arg_key><arg_value>cut", false),
            1 => try h.output("reason</think><tool_call>lookup<arg_key>q</arg_key><arg_value>done</arg_value></tool_call>", false),
            2 => {
                h.script.fail = true;
            },
            3 => {
                reject = if (streaming) "[DONE]" else "HTTP/1.1";
            },
            4 => {
                reject = "call_";
            },
            5 => try h.output("reason</think><tool_call>lookup<arg_key>q</arg_key><arg_value>cut<|im_end|>", true),
            else => unreachable,
        }
        _ = try h.send("/v1/chat/completions", try h.request(streaming), reject);
        try t.expectEqual(@as(usize, 0), h.srv.reasoning.count);
    };
}

fn translatedCall(a: std.mem.Allocator, item: Value, anthropic: bool) !Value {
    const id = item.get(if (anthropic) "id" else "call_id").?;
    const call = try json.newObject(a);
    try call.put(a, "id", id);
    try call.put(a, "type", .{ .string = "function" });
    const function = try json.newObject(a);
    try function.put(a, "name", item.get("name").?);
    const args = if (anthropic) (item.get("input") orelse (try json.parse(a, "{\"q\":\"same\"}")).ok) else item.get("arguments").?;
    try function.put(a, "arguments", if (args == .string) args else .{ .string = try json.stringify(a, args, .{}) });
    try call.put(a, "function", .{ .object = function });
    return .{ .object = call };
}

fn translatedCalls(a: std.mem.Allocator, wire: Wire, streaming: bool, anthropic: bool) !Value {
    var calls: std.ArrayList(Value) = .empty;
    if (!streaming) {
        const body = try wire.value();
        const items = body.get(if (anthropic) "content" else "output").?.array;
        for (items) |item| if (std.mem.eql(u8, item.strField("type") orelse "", if (anthropic) "tool_use" else "function_call")) {
            try calls.append(a, try translatedCall(a, item, anthropic));
        };
    } else {
        var lines = std.mem.splitScalar(u8, wire.body(), '\n');
        while (lines.next()) |line| {
            if (!std.mem.startsWith(u8, line, "data: ")) continue;
            const payload = (try json.parse(a, line[6..])).ok;
            const kind = payload.strField("type") orelse "";
            if (anthropic and std.mem.eql(u8, kind, "content_block_start")) {
                const item = payload.get("content_block").?;
                if (std.mem.eql(u8, item.strField("type") orelse "", "tool_use")) {
                    const call = try translatedCall(a, item, true);
                    // The following input_json_delta events hold the arguments, not the header's empty input.
                    try call.get("function").?.object.put(a, "arguments", .{ .string = "" });
                    try calls.append(a, call);
                }
            } else if (anthropic and std.mem.eql(u8, kind, "content_block_delta")) {
                const delta = payload.get("delta").?;
                if (delta.strField("partial_json")) |piece| {
                    const f = calls.items[calls.items.len - 1].get("function").?.object;
                    try f.put(a, "arguments", .{ .string = try std.mem.concat(a, u8, &.{ f.get("arguments").?.string, piece }) });
                }
            } else if (!anthropic and std.mem.eql(u8, kind, "response.output_item.done")) {
                const item = payload.get("item").?;
                if (std.mem.eql(u8, item.strField("type") orelse "", "function_call")) try calls.append(a, try translatedCall(a, item, false));
            }
        }
    }
    return .{ .array = calls.items };
}

test "native API parity: Responses and Messages deliver original IDs in whole and streaming replies" {
    for ([_]bool{ false, true }) |anthropic| for ([_]bool{ false, true }) |streaming| {
        var arena: std.heap.ArenaAllocator = .init(t.allocator);
        defer arena.deinit();
        const a = arena.allocator();
        var h: Harness = undefined;
        try h.init(a);
        defer h.deinit();
        const path = if (anthropic) "/v1/messages" else "/v1/responses";
        const request = (try json.parse(a, if (anthropic)
            \\{"model":"arbitrary-name","thinking":{"type":"adaptive"},"messages":[{"role":"user","content":"search"}],"max_tokens":512,"tools":[{"name":"lookup","input_schema":{"type":"object","properties":{"q":{"type":"string"}}}}]}
        else
            \\{"model":"arbitrary-name","input":"search","max_output_tokens":512,"store":false,"tools":[{"type":"function","name":"lookup","parameters":{"type":"object","properties":{"q":{"type":"string"}}}}]}
        )).ok;
        try request.object.put(a, "stream", .{ .bool = streaming });
        const wire = try h.send(path, request, null);
        try t.expect(wire.ok());
        const calls = try translatedCalls(a, wire, streaming, anthropic);
        try t.expectEqual(@as(usize, 2), calls.array.len);
        try t.expectEqual(@as(usize, 1), h.srv.reasoning.count);
        for (calls.array) |call| try t.expect(h.srv.reasoning.by_id.contains(call.strField("id").?));
        try h.output("done<|im_end|>", true);
        const next = try h.send("/v1/chat/completions", try h.continuation(calls, null), null);
        try t.expect(next.ok());
        try contains(h.script.seen, "<think>first reasoning</think>");
        const continued = try json.deepCopy(a, request);
        try continued.object.put(a, "stream", .{ .bool = false });
        var history: std.ArrayList(Value) = .empty;
        try history.append(a, (try json.parse(a, "{\"role\":\"user\",\"content\":\"search\"}")).ok);
        var blocks: std.ArrayList(Value) = .empty;
        for (calls.array) |call| {
            const f = call.get("function").?;
            const item = try json.newObject(a);
            try item.put(a, "type", .{ .string = if (anthropic) "tool_use" else "function_call" });
            try item.put(a, if (anthropic) "id" else "call_id", call.get("id").?);
            try item.put(a, "name", f.get("name").?);
            try item.put(a, if (anthropic) "input" else "arguments", if (anthropic) (try json.parse(a, f.strField("arguments").?)).ok else f.get("arguments").?);
            try blocks.append(a, .{ .object = item });
        }
        if (anthropic) {
            const assistant = try json.newObject(a);
            try assistant.put(a, "role", .{ .string = "assistant" });
            try assistant.put(a, "content", .{ .array = blocks.items });
            try history.append(a, .{ .object = assistant });
        } else try history.appendSlice(a, blocks.items);
        var outputs: std.ArrayList(Value) = .empty;
        for (calls.array) |call| {
            const item = try json.newObject(a);
            try item.put(a, "type", .{ .string = if (anthropic) "tool_result" else "function_call_output" });
            try item.put(a, if (anthropic) "tool_use_id" else "call_id", call.get("id").?);
            try item.put(a, if (anthropic) "content" else "output", .{ .string = "RESULT" });
            try outputs.append(a, .{ .object = item });
        }
        if (anthropic) {
            const user = try json.newObject(a);
            try user.put(a, "role", .{ .string = "user" });
            try user.put(a, "content", .{ .array = outputs.items });
            try history.append(a, .{ .object = user });
        } else try history.appendSlice(a, outputs.items);
        try continued.object.put(a, if (anthropic) "messages" else "input", .{ .array = history.items });
        const translated_next = try h.send(path, continued, null);
        try t.expect(translated_next.ok());
        try contains(h.script.seen, "<think>first reasoning</think>");
        if (anthropic) {
            const count = try (try h.send("/v1/messages/count_tokens", continued, null)).value();
            try t.expectEqual((try h.hf.text().encode(a, h.script.seen, false)).len, @as(usize, @intCast(count.get("input_tokens").?.int64().?)));
        }
    };
}

test "native API parity: failed translated delivery never caches whole or streamed replies" {
    for ([_]bool{ false, true }) |anthropic| for ([_]bool{ false, true }) |streaming| {
        var arena: std.heap.ArenaAllocator = .init(t.allocator);
        defer arena.deinit();
        const a = arena.allocator();
        var h: Harness = undefined;
        try h.init(a);
        defer h.deinit();
        const request = (try json.parse(a, if (anthropic)
            \\{"model":"arbitrary-name","thinking":{"type":"adaptive"},"messages":[{"role":"user","content":"search"}],"max_tokens":512,"tools":[{"name":"lookup","input_schema":{"type":"object","properties":{"q":{"type":"string"}}}}]}
        else
            \\{"input":"search","max_output_tokens":512,"store":false,"tools":[{"type":"function","name":"lookup","parameters":{"type":"object","properties":{"q":{"type":"string"}}}}]}
        )).ok;
        try request.object.put(a, "stream", .{ .bool = streaming });
        const reject = if (!streaming) "HTTP/1.1" else if (anthropic) "message_stop" else "response.completed";
        _ = try h.send(if (anthropic) "/v1/messages" else "/v1/responses", request, reject);
        try t.expectEqual(@as(usize, 0), h.srv.reasoning.count);
    };
}
