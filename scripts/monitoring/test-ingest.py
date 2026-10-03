#!/usr/bin/env python3
"""Exercise the repository's write-only VMAuth configuration with fixture tokens.

VMAUTH_BIN must point to the pinned v1.152.0 binary. No production secrets or
cluster connection are used. Tests TLS, all host tokens, listener separation,
revocation, rejected paths, and removal of credentials before forwarding.
"""
import http.server
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "kubernetes/apps/observability/external-metrics-ingest/app/auth.yaml"
received = []


class Receiver(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        received.append((self.path, self.headers.get("Authorization", ""),
                         self.rfile.read(int(self.headers.get("Content-Length", 0)))))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


with tempfile.TemporaryDirectory(prefix="ingest-test-") as directory:
    scratch = Path(directory)
    receiver = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=receiver.serve_forever, daemon=True).start()
    backend = f"http://127.0.0.1:{receiver.server_port}/"
    config = scratch / "auth.yaml"
    config.write_text(CONFIG.read_text().replace(
        "http://vmsingle-victoria-metrics-k8s-stack.observability.svc.cluster.local:8428/", backend))
    cert, key = scratch / "cert.pem", scratch / "key.pem"
    openssl_config = scratch / "openssl.cnf"
    openssl_config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\n[dn]\n[ext]\nsubjectAltName=IP:127.0.0.1\n")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-days", "1", "-subj", "/CN=localhost", "-config", str(openssl_config),
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    context = ssl.create_default_context(cafile=str(cert))
    write, internal = free_port(), free_port()
    keys = ["SPARK_1_TOKEN", "SPARK_2_TOKEN", "PVE_SBX_1_TOKEN", "PVE_SBX_2_TOKEN", "PVE_SBX_3_TOKEN"]
    environment = dict(os.environ, **{name: f"fixture-{index}" for index, name in enumerate(keys)})
    binary = os.environ["VMAUTH_BIN"]
    args = [binary, f"-auth.config={config}", f"-httpListenAddr=127.0.0.1:{write}",
            f"-httpInternalListenAddr=127.0.0.1:{internal}", "-tls",
            f"-tlsCertFile={cert}", f"-tlsKeyFile={key}"]

    def request(port, path, token=None, data=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = urllib.request.Request(f"https://127.0.0.1:{port}{path}", headers=headers, data=data)
        try:
            # VMAuth intentionally delays invalid-token responses.
            with urllib.request.urlopen(req, context=context, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def start(env, log):
        process = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("VMAuth exited; inspect fixture log")
            try:
                if request(internal, "/health") == 200:
                    return process
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError("VMAuth did not become healthy")

    with (scratch / "vmauth.log").open("w+") as log:
        process = start(environment, log)
        try:
            for index in range(5):
                assert request(write, "/api/v1/write", f"fixture-{index}", b"fixture-write") == 204
            assert len(received) == 5 and all(not entry[1] for entry in received)
            assert request(write, "/api/v1/write", data=b"bad") == 401
            assert request(write, "/api/v1/write", "invalid", b"bad") == 401
            for path in ["/api/v1/query", "/api/v1/query_range", "/api/v1/admin/tsdb/delete_series",
                         "/api/v1/import/prometheus", "/api/v1/write/extra", "/metrics", "/flags",
                         "/health", "/-/reload", "/debug/pprof/", "/api/v1/write/../query"]:
                assert request(write, path, "fixture-0", b"bad") >= 400, path
            assert len(received) == 5, "denied requests reached the receiver"
            assert request(internal, "/metrics") == 200
        finally:
            process.terminate()
            process.wait(timeout=5)
        environment["SPARK_1_TOKEN"] = "rotated-token"
        process = start(environment, log)
        try:
            assert request(write, "/api/v1/write", "fixture-0", b"bad") == 401
            assert request(write, "/api/v1/write", "rotated-token", b"new") == 204
            assert request(write, "/api/v1/write", "fixture-1", b"other-host") == 204
        finally:
            process.terminate()
            process.wait(timeout=5)
            receiver.shutdown()
    print("Passed: TLS, five host tokens, write-only routes, internal listener, credential stripping and rotation.")
