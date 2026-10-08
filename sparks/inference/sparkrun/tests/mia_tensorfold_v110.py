#!/usr/bin/env python3
"""Check v1.10's launcher switches, streamed admission and Anthropic route on installed code without GPU weights.

Run it where the rank's environment applies (`docker exec` in the SparkRun-owned container): the switch
check reads the process environment, so it reports what the running recipe selected.
"""
import ast
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import tensorfold
from tensorfold.cuda.http import make_handler
from tensorfold.cuda.sampling import union_cover
from tensorfold.families.glm5_next.cuda.kept_reasoning import KeptReasoning
from tensorfold.families.glm5_next.prompts import effort_tail_enabled
from tensorfold.server.anthropic_translate import Reply, translate
from tensorfold.server.errors import CapacityError


def installed(relative, *names):
    # Execute only the named parsers; importing these modules would load CUDA kernels.
    source = Path(tensorfold.__file__).parent / relative
    wanted = [n for n in ast.parse(source.read_text()).body
              if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(wanted) == len(names), f"{relative} lacks one of {names}"
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + wanted
    namespace = {"os": os, "BACKEND_ENV": "TF_GLM_DISPLAY_KV_BACKEND", "ENV": "TF_GLM_DISPLAY_KV_MIB",
                 "LARGEST_MIB": 2032}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(source), "exec"), namespace)
    return [namespace[name] for name in names]


def check_switches():
    keep_per_chat, kept_bytes_cap = installed("families/glm5_next/cuda/multi.py", "keep_per_chat", "kept_bytes_cap")
    backend, mib = installed("families/glm5_next/cuda/display_kv.py", "backend", "mib")
    dec_order, = installed("families/glm5_next/cuda/exl3_mm.py", "dec_order")
    assert union_cover() is True, "TENSORFOLD_NUCLEUS_UNION=1 did not reach this rank"
    assert effort_tail_enabled() is False, "The effort line must stay at the head of the prompt"
    assert keep_per_chat() == 0 and kept_bytes_cap() == 0, "Kept-state limits must stay off"
    assert (backend(), mib()) == ("drm", 0), "The display reservation must stay off"
    assert dec_order() == 0, "The decode launch order must stay stock"
    assert os.environ.get("TF_GLM_LOOP_GUARD", "0") == "0" and os.environ.get("TF_GLM_CACHE_SHARE_PCT", "0") == "0"
    with patch.dict(os.environ, {}, clear=True):
        assert union_cover() is False, "The engine default changed; the recipe line may no longer be needed"


class App:
    stage = "prepare"

    def prepare(self, body, chat):
        if self.stage == "prepare":
            raise CapacityError("Public capacity fixture")
        return object()

    def admit(self, body, chat, prepared):
        raise CapacityError("Public capacity fixture")

    def reply_model(self, body):
        return "GLM-5.3-Flash-EXL3"

    def run(self, *args, **kwargs):
        raise AssertionError("A refused request must not reach generation")


def check_http_refusals():
    app = App()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(route, body):
        request = Request(f"http://127.0.0.1:{server.server_port}{route}", json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.code, response.headers, json.load(response)

    try:
        # A streamed request refused at admission gets a status, not HTTP 200 with an error event.
        app.stage = "admit"
        code, headers, body = post("/v1/chat/completions", {"stream": True, "messages": []})
        assert code == 429 and headers.get("Retry-After") == "5", (code, dict(headers))
        assert headers.get("Content-Type") == "application/json" and body["error"]["message"] == "Public capacity fixture"
        message = {"model": "GLM-5.3-Flash-EXL3", "max_tokens": 8,
                   "messages": [{"role": "user", "content": "Public fixture."}]}
        for stage in ("prepare", "admit"):
            app.stage = stage
            for stream in (False, True):
                if stage == "admit" and not stream:
                    continue  # Only a streamed request is admitted before generation.
                code, headers, body = post("/v1/messages", {**message, "stream": stream})
                assert code == 429 and body == {"type": "error", "error": {
                    "type": "overloaded_error", "message": "Public capacity fixture"}}, (code, body)
                assert headers.get("Retry-After") is None, "The Anthropic route now sends Retry-After; update the notes"
    finally:
        server.shutdown()
        server.server_close()


def check_anthropic_tool_turn():
    call_id = "call_" + "1" * 24
    call = {"id": call_id, "type": "function", "function": {"name": "read_file", "arguments": '{"path": "README.md"}'}}
    sent = Reply("GLM-5.3-Flash-EXL3", lambda event: None).completion({"choices": [{"finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "reasoning_content": "Public reasoning.",
                    "tool_calls": [call]}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    use = [block for block in sent["content"] if block["type"] == "tool_use"]
    assert [block["id"] for block in use] == [call_id], "The route must expose the server's own call id"

    def resume(blocks, **fields):
        chat = translate({"model": "GLM-5.3-Flash-EXL3", "max_tokens": 8, **fields, "messages": [
            {"role": "user", "content": "Read README.md and report status."},
            {"role": "assistant", "content": blocks},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": blocks[-1]["id"],
                                          "content": "Public fixture."}]}]})
        assert chat["messages"][1]["tool_calls"][0]["id"] == blocks[-1]["id"]
        assert chat["messages"][2]["tool_call_id"] == blocks[-1]["id"]
        return chat

    # A client that drops the thinking block gets it back only through the original call id.
    chat = resume(use)
    memory = KeptReasoning()
    memory.remember(chat["messages"][:1], [call], "Public reasoning.")
    assert memory.restore(chat["messages"])[1]["reasoning_content"] == "Public reasoning."
    renumbered = resume([{**use[0], "id": "toolu_public_fixture"}])
    assert "reasoning_content" not in memory.restore(renumbered["messages"])[1], "Guessed reasoning for a renumbered call"
    kept = resume([{"type": "thinking", "thinking": "Caller retained this.", "signature": ""},
                   {**use[0], "id": "toolu_public_fixture"}])
    assert memory.restore(kept["messages"])[1]["reasoning_content"] == "Caller retained this."
    # Without a thinking field the route turns thinking off, unlike the chat route's server default.
    assert chat["chat_template_kwargs"] == {"enable_thinking": False}
    assert resume(use, thinking={"type": "adaptive"})["chat_template_kwargs"] == {"enable_thinking": True}


if __name__ == "__main__":
    check_switches()
    check_http_refusals()
    check_anthropic_tool_turn()
    print("PASS launcher switches as selected, streamed HTTP 429, Anthropic overloaded_error and exact-id tool turns")
