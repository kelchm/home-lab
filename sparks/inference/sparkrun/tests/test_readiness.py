"""The shared TensorFold readiness gate against a fake server in each health format."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

MOD = Path(__file__).resolve().parents[1] / "mods/tensorfold-readiness"
MODEL = "Test-Model"
CONTEXT = 524288
LANES = 4
# DeepSeek's shape: three named levels plus thinking off, unlisted names mapped to the nearest.
LEVELS = {"max": 100, "high": 75, "low": 50, "none": 0}
NEAREST = {"xhigh": "max", "medium": "high", "minimal": "low"}


def zig_health():
    return {"status": "ok", "warming": False, "model": MODEL, "max_batch_size": LANES}


def python_health():
    return {"ok": True, "mode": "strict", "streams": {"max": LANES}, "context_length": CONTEXT,
            "pool_tokens": 2000000}


def models():
    return {"object": "list", "data": [{
        "id": MODEL, "object": "model", "created": 0, "owned_by": "tensorfold", "context_length": CONTEXT,
        "max_model_len": CONTEXT, "reasoning": {"default_enabled": True, "supported_efforts": list(LEVELS)}}]}


class Fake(BaseHTTPRequestHandler):
    health = staticmethod(zig_health)
    models = staticmethod(models)
    tokenize = True
    levels = dict(LEVELS)

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, type(self).health())
        if self.path == "/v1/models":
            return self._send(200, type(self).models())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/tokenize":
            if not type(self).tokenize:
                return self._send(404, {"error": "not found"})
            effort = NEAREST.get(body["reasoning_effort"], body["reasoning_effort"])
            return self._send(200, {"count": 3, "tokens": [1, 2, type(self).levels[effort]]})
        if body.get("tools"):
            call = {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Oslo"}'}}
            return self._send(200, {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [call]}}]})
        self._send(200, {"choices": [{"finish_reason": "stop", "message": {"content": "323"}}],
                         "usage": {"prompt_tokens": 20, "completion_tokens": 2}})

    def log_message(self, fmt, *args):
        del fmt, args


@pytest.fixture
def server():
    handler = type("Handler", (Fake,), {})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield handler, "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def gate(url, efforts=",".join(LEVELS), *extra):
    return subprocess.run(
        [sys.executable, str(MOD / "readiness.py"), "--url", url + "/v1", "--model", MODEL, "--context", str(CONTEXT),
         "--lanes", str(LANES), "--efforts", efforts, *extra], capture_output=True, text=True)


def test_zig_server_passes_and_maps_every_standard_level(server):
    _, url = server
    result = gate(url)
    assert result.returncode == 0, result.stderr
    assert '"xhigh": "max"' in result.stdout and '"minimal": "low"' in result.stdout
    assert "TensorFold readiness passed" in result.stdout


def test_python_server_passes_without_a_tokenize_route(server):
    handler, url = server
    handler.health, handler.tokenize = staticmethod(python_health), False
    result = gate(url, ",".join(LEVELS), "--min-pool-tokens", "1200000", "--warmup-words", "128")
    assert result.returncode == 0, result.stderr
    assert "no /tokenize route" in result.stdout
    assert gate(url, ",".join(LEVELS), "--min-pool-tokens", "3000000").returncode != 0


@pytest.mark.parametrize("change,message", [
    (lambda h: {**h, "warming": True}, "warming"),
    (lambda h: {**h, "max_batch_size": 2}, "lanes"),
    (lambda h: {**h, "model": "other"}, "names model"),
])
def test_unready_health_fails(server, change, message):
    handler, url = server
    handler.health = staticmethod(lambda: change(zig_health()))
    result = gate(url)
    assert result.returncode != 0 and message in result.stderr


@pytest.mark.parametrize("field,value,message", [
    ("owned_by", "vllm", "owned_by"),
    ("context_length", 1048576, "context_length"),
    ("max_model_len", 1048576, "max_model_len"),
])
def test_discovery_must_match_the_profile(server, field, value, message):
    handler, url = server
    body = models()
    body["data"][0][field] = value
    handler.models = staticmethod(lambda: body)
    result = gate(url)
    assert result.returncode != 0 and message in result.stderr


def test_levels_are_compared_in_the_servers_order(server):
    _, url = server
    result = gate(url, "low,high,max,none")
    assert result.returncode != 0 and "supported_efforts" in result.stderr


def test_advertised_levels_must_render_differently(server):
    handler, url = server
    handler.levels = {**LEVELS, "low": LEVELS["high"]}
    result = gate(url)
    assert result.returncode != 0 and "share a rendered prompt" in result.stderr


def test_every_standard_name_must_land_on_an_advertised_level(server):
    handler, url = server
    handler.levels = {**LEVELS, "xhigh": 88}
    with mock.patch.dict(NEAREST, {"xhigh": "xhigh"}):
        result = gate(url)
    assert result.returncode != 0 and "'xhigh' renders as none" in result.stderr


def test_mod_is_frozen(tmp_path):
    bundle = tmp_path / MOD.name
    shutil.copytree(MOD, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    check = [sys.executable, str(bundle / "prepare.py"), "--check"]
    subprocess.run(check, check=True, capture_output=True)
    (bundle / "readiness.py").write_text((bundle / "readiness.py").read_text() + "\n# Unreviewed change\n")
    result = subprocess.run(check, capture_output=True, text=True)
    assert result.returncode != 0 and "Missing or changed frozen mod file: readiness.py" in result.stderr


def test_a_recipe_carries_the_gate_beside_its_engine_mod(tmp_path):
    from sparkrun.core.mods import resolve_and_inject_mods
    from sparkrun.core.recipe import Recipe

    engine = tmp_path / "mods/engine-mod"
    engine.mkdir(parents=True)
    (engine / "run.sh").write_text("#!/usr/bin/env bash\ntrue\n")
    shutil.copytree(MOD, tmp_path / "mods" / MOD.name, ignore=shutil.ignore_patterns("__pycache__"))
    path = tmp_path / "profiles/profile.yaml"
    path.parent.mkdir()
    recipe = Recipe({"recipe_version": "2", "model": "org/model", "runtime": "tensorfold",
                     "mods": ["../mods/tensorfold-readiness", "../mods/engine-mod"],
                     "post_exec": ["python3 /opt/tensorfold-readiness/readiness.py --model m"]}, source_path=str(path))
    resolve_and_inject_mods(recipe, mock.Mock())
    copies = [step["dest"] for step in recipe.pre_exec if isinstance(step, dict)]
    commands = [step for step in recipe.pre_exec if isinstance(step, str)]
    assert copies == ["/workspace/mods/tensorfold-readiness", "/workspace/mods/engine-mod"]
    assert [dest in command for dest, command in zip(copies, commands)] == [True, True]
