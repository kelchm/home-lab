#!/usr/bin/env python3
"""Check v1.8 capacity responses and queue defaults without GPU weights."""
import ast
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import tensorfold
from tensorfold.cuda.http import make_handler
from tensorfold.cuda.scheduler import Scheduler
from tensorfold.server.errors import CapacityError, RequestError


def check_admission():
    # Execute the installed GLM scheduler's constructor; the decoder and thread are
    # fixtures, so importing CUDA kernels or starting a GPU loop is unnecessary.
    source = Path(tensorfold.__file__).parent / "families/glm5_next/cuda/multi.py"
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    nodes += [n for n in ast.parse(source.read_text()).body
              if isinstance(n, ast.ClassDef) and n.name == "GlmScheduler"]
    assert len(nodes) == 2
    namespace = {"Scheduler": Scheduler, "os": os}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(source), "exec"), namespace)
    decoder = SimpleNamespace(group=False, live=lambda: 4)
    with patch.dict(os.environ, {}, clear=True), patch.object(threading.Thread, "start"):
        scheduler = namespace["GlmScheduler"](decoder, max_streams=4)
        assert scheduler.max_in_system is None
        scheduler._check_admission(False)  # An unset cap permits a fifth request to queue.
        os.environ["TF_GLM_MAX_QUEUED"] = ""
        scheduler = namespace["GlmScheduler"](decoder, max_streams=4)
        assert scheduler.max_in_system is None
        scheduler._check_admission(False)  # Upstream exports an empty cap by default.
        os.environ["TF_GLM_MAX_QUEUED"] = "0"
        scheduler = namespace["GlmScheduler"](decoder, max_streams=4)
        assert scheduler.max_in_system == 4
        try:
            scheduler._check_admission(False)
        except CapacityError:
            pass
        else:
            raise AssertionError("An opt-in zero queue must refuse the fifth request")
        scheduler._check_admission(True)  # Background requests keep their yielding policy.


def check_http_capacity():
    class App:
        stage = "prepare"
        error = CapacityError

        def prepare(self, body, chat):
            if self.stage == "prepare":
                raise self.error("Public capacity fixture")
            return None

        def reply_model(self, body):
            return "GLM-5.3-Flash-EXL3"

        def run(self, *args, **kwargs):
            raise self.error("Public capacity fixture")

    app = App()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for route in ("/v1/chat/completions", "/v1/completions"):
            for stage, stream in (("prepare", False), ("prepare", True), ("run", False)):
                app.stage = stage
                for error, expected in ((CapacityError, 429), (RequestError, 400)):
                    app.error = error
                    request = Request(f"http://127.0.0.1:{server.server_port}{route}",
                                      json.dumps({"stream": stream}).encode(),
                                      headers={"Content-Type": "application/json"})
                    try:
                        response = urlopen(request, timeout=3)
                    except HTTPError as exc:
                        response = exc
                    with response:
                        assert response.code == expected
                        assert response.headers.get("Retry-After") == ("5" if expected == 429 else None)
                        assert json.load(response)["error"]["message"] == "Public capacity fixture"
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    check_admission()
    check_http_capacity()
    print("PASS default queue, opt-in admission cap, capacity HTTP 429/Retry-After and request HTTP 400")
