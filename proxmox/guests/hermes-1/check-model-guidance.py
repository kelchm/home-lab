"""Check the system prompt Hermes sends to the Sparks under managed/config.yaml.

Run inside the pinned Hermes image with no network; see README.md. A fake Spark
endpoint serves a DeepSeek model and records each chat request. The check runs
one `hermes chat` turn with the managed settings and one with the `agent` pins
removed, and fails unless only the managed run carries the model-family
guidance blocks that the fixed `spark` name would otherwise lose.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ruamel.yaml import YAML

MANAGED = Path(__file__).resolve().parent / "managed" / "config.yaml"
SERVED = "DeepSeek-V4.1-Flash"
BLOCKS = ("# Tool-use enforcement", "# Execution discipline")
requests = []


class FakeSpark(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            model = {"id": SERVED, "object": "model", "owned_by": "tensorfold", "max_model_len": 524288}
            return self._send({"object": "list", "data": [model]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append(body)
        usage = {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}
        if not body.get("stream"):
            message = {"role": "assistant", "content": "ok"}
            return self._send({"id": "x", "object": "chat.completion", "model": SERVED, "usage": usage,
                               "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})
        chunks = [{"role": "assistant", "content": "ok"}, {}]
        events = []
        for i, delta in enumerate(chunks):
            chunk = {"id": "x", "object": "chat.completion.chunk", "model": SERVED,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": "stop" if i else None}]}
            if i:
                chunk["usage"] = usage
            events.append(f"data: {json.dumps(chunk)}\n\n")
        self._send(("".join(events) + "data: [DONE]\n\n").encode(), "text/event-stream")


def system_prompt(config, workdir):
    managed = workdir / "managed"
    home = workdir / "home"
    managed.mkdir()
    home.mkdir()
    yaml = YAML()
    with (managed / "config.yaml").open("w") as f:
        yaml.dump(config, f)
    env = dict(os.environ, HERMES_MANAGED_DIR=str(managed), HERMES_HOME=str(home), HOME=str(home))
    before = len(requests)
    run = subprocess.run(["/opt/hermes/.venv/bin/hermes", "chat", "-Q", "-q", "say ok"], check=False, env=env,
                         capture_output=True, text=True, timeout=180, cwd=workdir)
    if run.returncode:
        sys.exit(f"hermes failed (exit {run.returncode}):\n{run.stdout}\n{run.stderr}")
    sent = requests[before:]
    if not sent:
        sys.exit(f"hermes sent no request (exit {run.returncode}):\n{run.stdout}\n{run.stderr}")
    model = sent[0]["model"]
    if model != "spark":
        sys.exit(f"hermes requested model {model!r}, not 'spark'")
    return next(m["content"] for m in sent[0]["messages"] if m["role"] == "system")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSpark)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    config = YAML().load(MANAGED)
    config.pop("mcp_servers", None)
    config["model"]["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
    unpinned = copy.deepcopy(config)
    unpinned.pop("agent", None)

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, cfg, want in (("managed", config, True), ("without agent pins", unpinned, False)):
            workdir = Path(tmp) / name.replace(" ", "-")
            workdir.mkdir()
            prompt = system_prompt(cfg, workdir)
            for block in BLOCKS:
                present = block in prompt
                print(f"{name}: {block!r} {'present' if present else 'absent'}")
                if present != want:
                    failures.append(f"{name}: expected {block!r} {'present' if want else 'absent'}")
    server.shutdown()
    if failures:
        sys.exit("\n".join(failures))
    print("ok")


if __name__ == "__main__":
    main()
