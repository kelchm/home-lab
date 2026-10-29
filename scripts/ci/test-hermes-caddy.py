#!/usr/bin/env python3
"""Offline regressions; Docker qualification belongs to hosted native CI."""

import importlib.util
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inputs = load("inputs", "scripts/ci/hermes-caddy-inputs.py")
fixture = load("fixture", "proxmox/guests/hermes-1/caddy/fixture.py")
CURRENT_DOCKERFILE = (ROOT / inputs.GUEST / "Dockerfile.caddy").read_text()
CURRENT = inputs.expected(CURRENT_DOCKERFILE)
# Fixed versions belong only to rejection fixtures; real Renovate updates must pass.
DOCKERFILE = CURRENT_DOCKERFILE.replace(CURRENT["core"][1:], "2.11.7").replace(CURRENT["plugin"], "v0.2.4")


class Inputs(unittest.TestCase):
    def test_qualifier_rejects_non_hosted_environments_before_running_docker_or_sysctl(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            # Both commands must be unreachable for these environment failures.
            for command in ("docker", "sysctl", "sudo"):
                path = directory / command
                path.write_text(f"#!/bin/sh\ntouch '{directory / 'unsafe-command'}'\nexit 99\n")
                path.chmod(0o755)
            for actions, runner in (("false", "github-hosted"), ("true", "self-hosted")):
                result = subprocess.run(
                    ["bash", str(ROOT / inputs.GUEST / "caddy/qualify.sh"), "unused", str(directory / "report")],
                    env={**os.environ, "GITHUB_ACTIONS": actions, "RUNNER_ENVIRONMENT": runner,
                         "PATH": f"{directory}:{os.environ['PATH']}"}, capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((directory / "unsafe-command").exists())

    def test_exact_current_direct_inputs(self):
        expect = inputs.expected(CURRENT_DOCKERFILE)
        self.assertRegex(expect["core"], r"^v\d+\.\d+\.\d+$")
        self.assertRegex(expect["plugin"], r"^v\d+\.\d+\.\d+$")
        self.assertIn("@sha256:", expect["builder"])
        self.assertIn("@sha256:", expect["runtime"])

    def test_core_alignment_rejects_one_sided_update(self):
        with self.assertRaisesRegex(ValueError, "same Caddy release"):
            inputs.expected(DOCKERFILE.replace("2.11.7-builder", "2.11.8-builder"))
        updated = DOCKERFILE.replace("2.11.7", "2.11.8")
        self.assertEqual(inputs.expected(updated)["core"], "v2.11.8")

    def test_exact_module_and_base_required(self):
        with self.assertRaises(ValueError):
            inputs.expected(DOCKERFILE.replace("CLOUDFLARE_VERSION=v0.2.4", "CLOUDFLARE_VERSION=latest"))
        with self.assertRaises(ValueError):
            inputs.expected(DOCKERFILE.replace("@sha256:", "@sha512:", 1))

    def test_scope_includes_helpers_config_policy_and_workflows(self):
        for path in inputs.INPUTS:
            self.assertTrue(inputs.relevant(path), path)
        self.assertTrue(inputs.relevant(str(inputs.GUEST / "caddy/new-helper.py")))
        for path in ("kubernetes/apps/example.yaml", "proxmox/guests/hermes-1/managed/config.yaml",
                     "proxmox/guests/hermes-1/caddy-other", "proxmox/guests/netbird-router-2/README.md"):
            self.assertFalse(inputs.relevant(path), path)

    def test_publication_scope_excludes_qualification_and_unrelated_config(self):
        self.assertEqual(set(inputs.PUBLISH_INPUTS), {
            "proxmox/guests/hermes-1/Dockerfile.caddy", "proxmox/guests/hermes-1/.dockerignore",
        })
        for path in inputs.INPUTS:
            self.assertEqual(inputs.relevant(path, publish=True), path in inputs.PUBLISH_INPUTS, path)

    def test_cli_scope_uses_complete_base_diff_and_fails_open_for_missing_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def git(*args):
                return subprocess.check_output(["git", "-c", "commit.gpgsign=false", *args], cwd=directory, stderr=subprocess.DEVNULL).decode().strip()

            git("init", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            (directory / "unrelated").write_text("one")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (directory / inputs.GUEST).mkdir(parents=True)
            (directory / inputs.GUEST / "Caddyfile").write_text("fixture")
            git("add", ".")
            git("commit", "-qm", "relevant earlier commit")
            (directory / "unrelated").write_text("two")
            git("commit", "-qam", "unrelated latest commit")
            for ref, expected in ((base, "true"), ("HEAD^", "false"), ("0" * 40, "true")):
                result = subprocess.check_output(["python3", str(ROOT / "scripts/ci/hermes-caddy-inputs.py"), "scope", ref, "HEAD"], cwd=directory)
                self.assertEqual(result.decode().strip(), expected)
            command = ["python3", str(ROOT / "scripts/ci/hermes-caddy-inputs.py"), "scope", base, "HEAD", "--publish"]
            self.assertEqual(subprocess.check_output(command, cwd=directory).decode().strip(), "false")
            (directory / inputs.GUEST / "Dockerfile.caddy").write_text("image change")
            git("add", ".")
            git("commit", "-qm", "image input")
            self.assertEqual(subprocess.check_output(command, cwd=directory).decode().strip(), "true")
            command[3] = "0" * 40
            self.assertEqual(subprocess.check_output(command, cwd=directory).decode().strip(), "true")


class Inventory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.report = Path(self.temp.name)
        self.expect = inputs.expected(DOCKERFILE)
        values = {
            "build-info.txt": "go\tgo1.27.3\ndep\tgithub.com/caddyserver/caddy/v2\tv2.11.7\th1:core\ndep\tgithub.com/caddy-dns/cloudflare\tv0.2.4\th1:plugin\nbuild\t-compiler=gc\n",
            "version.txt": "v2.11.7 h1:core\n", "runtime-version.txt": "v2.11.7 h1:core\n",
            "go-version.txt": "go version go1.27.3 linux/amd64\n",
            "modules.txt": "dns.providers.cloudflare\n\n  Non-standard modules: 1\n",
        }
        for key, value in values.items():
            (self.report / key).write_text(value)
        for kind in ("builder", "runtime"):
            (self.report / f"{kind}.json").write_text(json.dumps([{
                "Os": "linux", "Architecture": "amd64", "Config": {"Env": ["CADDY_VERSION=v2.11.7"]},
            }]))

    def verify(self):
        return inputs.verify_artifact(self.expect, self.report)

    def test_inventory_comes_from_artifact(self):
        self.assertEqual(self.verify()["go"], "go1.27.3")
        self.assertEqual(self.verify()["modules"]["github.com/caddy-dns/cloudflare"], "v0.2.4")

    def test_promotion_accepts_an_older_reviewed_inventory_without_current_base_inputs(self):
        for name in ("builder.json", "runtime.json", "runtime-version.txt", "go-version.txt"):
            (self.report / name).unlink()
        newer = {**self.expect, "core": "v2.12.0", "plugin": "v0.3.0"}
        result = inputs.verify_artifact(newer, self.report, promotion=True)
        self.assertEqual(result["core"], "v2.11.7")
        self.assertEqual(result["cloudflare"], "v0.2.4")
        (self.report / "modules.txt").write_text("dns.providers.cloudflare\nhttp.handlers.extra\n")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            inputs.verify_artifact(newer, self.report, promotion=True)

    def test_reject_actual_plugin_core_compiler_or_extra_module_mismatch(self):
        for name, before, after in (("build-info.txt", "v0.2.4", "v0.2.3"),
                                    ("build-info.txt", "v2.11.7", "v2.11.8"),
                                    ("build-info.txt", "go1.27.3", "go1.28.0"),
                                    ("modules.txt", "dns.providers.cloudflare", "dns.providers.cloudflare\nhttp.handlers.extra")):
            with self.subTest(name=name, after=after):
                path = self.report / name
                original = path.read_text()
                path.write_text(original.replace(before, after))
                with self.assertRaises(ValueError):
                    self.verify()
                path.write_text(original)

    def test_module_replacement_cannot_hide_behind_version_line(self):
        with (self.report / "build-info.txt").open("a") as output:
            output.write("=>\texample.invalid/override\tv1.0.0\th1:override\n")
        with self.assertRaisesRegex(ValueError, "replacement"):
            self.verify()

    def test_both_base_payloads_must_match(self):
        for kind in ("builder", "runtime"):
            path = self.report / f"{kind}.json"
            original = path.read_text()
            path.write_text(original.replace("v2.11.7", "v2.11.8"))
            with self.assertRaisesRegex(ValueError, kind):
                self.verify()
            path.write_text(original)


class WebSockets(unittest.TestCase):
    def test_real_forwarding_fixture_echoes_text_on_ws_and_binary_on_pty(self):
        server = fixture.ThreadingHTTPServer(("127.0.0.1", 0), fixture.Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path, opcode in zip(fixture.PATHS, (1, 2)):
                with socket.create_connection(server.server_address, timeout=2) as connection:
                    fixture.exchange(connection, path, opcode)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_handshake_and_forwarded_echo_require_valid_headers_and_paths(self):
        for mode in ("fake-101", "no-echo", "wrong-path", "bad-connection", "mixed-connection"):
            with self.subTest(mode=mode):
                client, upstream = socket.socketpair()
                client.settimeout(2)
                errors = []

                def pretend():
                    try:
                        with upstream, upstream.makefile("rb") as stream:
                            request = b""
                            while not request.endswith(b"\r\n\r\n"):
                                request += stream.read(1)
                            key = next(line.split(b": ", 1)[1].decode() for line in request.split(b"\r\n") if line.startswith(b"Sec-WebSocket-Key:"))
                            connection_header = {"bad-connection": "notupgrade", "mixed-connection": "keep-alive, UpGrAdE"}.get(mode, "Upgrade")
                            response = f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: {connection_header}\r\n"
                            response += f"Sec-WebSocket-Accept: {fixture.accept(key) if mode != 'fake-101' else 'fake'}\r\n\r\n"
                            upstream.sendall(response.encode())
                            if mode != "fake-101":
                                opcode, payload = fixture.read_frame(stream, masked=True)
                                if mode == "wrong-path":
                                    upstream.sendall(fixture.frame(opcode, b"/wrong:" + payload))
                                elif mode in ("bad-connection", "mixed-connection"):
                                    upstream.sendall(fixture.frame(opcode, b"/api/ws:" + payload))
                    except Exception as error:
                        if not (mode == "bad-connection" and isinstance(error, ValueError) and str(error) == "truncated WebSocket frame"):
                            errors.append(error)

                thread = threading.Thread(target=pretend)
                thread.start()
                with client:
                    if mode == "mixed-connection":
                        fixture.exchange(client, "/api/ws", 1)
                    else:
                        with self.assertRaises(ValueError):
                            fixture.exchange(client, "/api/ws", 1)
                thread.join()
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
