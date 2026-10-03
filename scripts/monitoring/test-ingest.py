#!/usr/bin/env python3
"""Exercise the repository's write-only VMAuth configuration with fixture tokens.

VMAUTH_BIN must point to the pinned v1.152.0 binary. No production secrets or
cluster connection are used. Tests TLS, all host tokens, listener separation,
revocation, rejected paths, and removal of credentials before forwarding.
"""
import http.server
import json
import re
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
CONFIG = ROOT / "kubernetes/apps/observability/external-metrics-ingest/app/auth.template"
# Inspect the actual Deployment and encrypted Secret's public key names. Kubelet
# rejects missing required secretKeyRefs; envFrom would silently omit the key.
def yaml_document(path, expression="."):
    return json.loads(subprocess.check_output(["yq", "-o=json", expression, str(path)]))

container = yaml_document(CONFIG.with_name("deployment.yaml"), 'select(.kind == "Deployment")')["spec"]["template"]["spec"]["containers"][0]
template = CONFIG.read_text()
keys = re.findall(r"%\{([A-Z0-9_]+)\}", template)
expanded = template
for index, name in enumerate(keys):
    expanded = expanded.replace("%{" + name + "}", f"fixture-{index}")
users = json.loads(subprocess.check_output(["yq", "-o=json", ".", "-"], input=expanded.encode()))["users"]
assert len(users) == len(keys), "every user must have one token placeholder"
assert [user["bearer_token"] for user in users] == [f"fixture-{index}" for index in range(len(keys))]
bearer_lines = re.findall(r"(?m)^\s*bearer_token:\s*(.*?)\s*$", template)
assert len(bearer_lines) == len(users) and all(re.fullmatch(r"%\{[A-Z0-9_]+\}", value) for value in bearer_lines), "tokens must be unquoted canonical placeholders"
assert keys and len(keys) == len(set(keys)), "token placeholders must be distinct"
assert set(yaml_document(CONFIG.with_name("auth.sops.yaml"))["stringData"]) == set(keys)
assert "envFrom" not in container, "envFrom allows missing credentials to become literal placeholders"
refs = {entry["name"]: entry["valueFrom"]["secretKeyRef"] for entry in container["env"]}
assert len(refs) == len(container["env"]) and set(refs) == set(keys)
for name, ref in refs.items():
    assert ref["name"] == "external-metrics-ingest-auth" and ref["key"] == name and not ref.get("optional", False)
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
    environment = dict(os.environ, **{name: f"fixture-{index}" for index, name in enumerate(keys)})
    binary = os.environ["VMAUTH_BIN"]
    replacements = {"-auth.config": str(config), "-httpListenAddr": f"127.0.0.1:{write}",
                    "-httpInternalListenAddr": f"127.0.0.1:{internal}",
                    "-tlsCertFile": str(cert), "-tlsKeyFile": str(key)}
    args = [binary] + [f"{flag.split('=', 1)[0]}={replacements[flag.split('=', 1)[0]]}"
                       if flag.split('=', 1)[0] in replacements else flag for flag in container["args"]]

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
            for index in range(len(keys)):
                assert request(write, "/api/v1/write", f"fixture-{index}", b"fixture-write") == 204
            assert len(received) == len(keys) and all(not entry[1] for entry in received)
            assert request(write, "/api/v1/write", data=b"bad") == 401
            assert request(write, "/api/v1/write", "invalid", b"bad") == 401
            assert request(write, "/api/v1/write", "%{" + keys[0] + "}", b"bad") == 401
            for path in ["/api/v1/query", "/api/v1/query_range", "/api/v1/admin/tsdb/delete_series",
                         "/api/v1/import/prometheus", "/api/v1/write/extra", "/metrics", "/flags",
                         "/health", "/-/reload", "/debug/pprof/", "/api/v1/write/../query"]:
                assert request(write, path, "fixture-0", b"bad") >= 400, path
            assert len(received) == len(keys), "denied requests reached the receiver"
            assert request(internal, "/metrics") == 200
        finally:
            process.terminate()
            process.wait(timeout=5)
        environment[keys[0]] = "rotated-token"
        process = start(environment, log)
        try:
            assert request(write, "/api/v1/write", "fixture-0", b"bad") == 401
            assert request(write, "/api/v1/write", "rotated-token", b"new") == 204
            assert request(write, "/api/v1/write", "fixture-1", b"other-host") == 204
        finally:
            process.terminate()
            process.wait(timeout=5)
        environment[keys[0]] = "0" * 64
        process = start(environment, log)
        try:
            assert request(write, "/api/v1/write", "0" * 64, b"numeric-hex-token") == 204
        finally:
            process.terminate()
            process.wait(timeout=5)
            receiver.shutdown()
    missing_environment = dict(environment)
    missing_environment.pop(keys[0])
    for bad_environment in [dict(environment, **{keys[0]: ""}), missing_environment]:
        with (scratch / "bad-token.log").open("w") as log:
            process = subprocess.Popen(args, env=bad_environment, stdout=log, stderr=log)
            try:
                assert process.wait(timeout=10) != 0, "empty or unresolved token must refuse startup"
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
    print("Passed: required token keys, TLS, all host tokens, write-only routes, internal listener, credential stripping and rotation.")
