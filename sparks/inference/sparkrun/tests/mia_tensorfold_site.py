#!/usr/bin/env python3
"""Check Mia's installed tool-history and HTTP health code without GPU weights."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

from tensorfold.cuda.http import make_handler
from tensorfold.families.glm5_next.cuda.kept_reasoning import KeptReasoning


def call(call_id):
    return {"id": call_id, "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"README.md"}'}}


def history():
    return [{"role": "system", "content": "Public project fixture."},
            {"role": "user", "content": "Read README.md and report status."}]


def resume(memory, call_id, **fields):
    request = history() + [{"role": "assistant", "content": None, "tool_calls": [call(call_id)], **fields},
                           {"role": "tool", "tool_call_id": call_id, "content": "Public fixture."}]
    original = deepcopy(request)
    restored = memory.restore(request)
    assert request == original, "Restoration must not mutate the caller's history"
    assert memory.restore(restored) == restored, "Restoration must remain idempotent"
    return restored[2]


def check_reasoning():
    memory = KeptReasoning()
    a, b = "call_" + "1" * 24, "call_" + "2" * 24
    # Two independent replies to identical histories can have identical tools and different reasoning.
    for name, call_id in (("A", a), ("B", b)):
        memory.remember(history(), [call(call_id)], f"Public reasoning from stream {name}.")
    for name, call_id in (("A", a), ("B", b)):
        assert resume(memory, call_id)["reasoning_content"] == f"Public reasoning from stream {name}."
    for call_id in ("client-renumbered-A", "call_" + "3" * 24, None):
        assert "reasoning_content" not in resume(memory, call_id), "Guessed a different reply's reasoning"
    for field in ("reasoning", "reasoning_content"):
        supplied = resume(memory, "client-renumbered-A", **{field: "Caller retained this reasoning."})
        assert supplied[field] == "Caller retained this reasoning."
    memory = KeptReasoning(entries=1)
    memory.remember(history(), [call(a)], "Public A.")
    memory.remember(history(), [call(b)], "Public B.")
    assert "reasoning_content" not in resume(memory, a)
    assert resume(memory, b)["reasoning_content"] == "Public B."


def check_http_health():
    alive = [True]
    decoder = SimpleNamespace(broken=None, streams={}, filling=[], health=lambda: {
        "pool_tokens": 1200000, "pool_free_tokens": 1200000})
    scheduler = SimpleNamespace(decoder=decoder, max_streams=4,
                                thread=SimpleNamespace(is_alive=lambda: alive[0]))
    app = SimpleNamespace(engine=SimpleNamespace(scheduler=scheduler), effective_context_window=850000)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def probe():
        try:
            response = urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.code, json.load(response)

    try:
        status, body = probe()
        assert status == 200 and body["ok"] is True and body["mode"] == "strict", body
        assert body["streams"]["max"] == 4 and body["pool_tokens"] == 1200000
        decoder.broken = RuntimeError("Public rank-failure fixture")
        status, body = probe()
        assert status == 503 and body["ok"] is False and "rank-failure" in body["fatal"], body
        decoder.broken = None
        alive[0] = False
        status, body = probe()
        assert status == 503 and body["ok"] is False, body
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    check_reasoning()
    check_http_health()
    print("PASS exact tool turns, identical parallel histories, caller reasoning, eviction and HTTP health failures")
