#!/usr/bin/env python3
"""Offline guards, plus --docker: isolated real Compose cutover/recovery tests.

Docker fixtures adapt only public-fetch/image-pull identity and paths; the real
updater runs Compose, Docker health/process inspection, TLS, pending and rollback.
No registry writes, production services, real credentials or host sysctl changes.
"""

import contextlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
GUEST = ROOT / "proxmox/guests/hermes-1"


def load(name, path):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


u = load("updater", GUEST / "caddy/hermes-caddy-update")
installer = load("installer", GUEST / "caddy/install.py")
DOCKER = "--docker" in sys.argv
if DOCKER:
    sys.argv.remove("--docker")
A = u.IMAGE + ":candidate@sha256:" + "a" * 64
B = u.IMAGE + ":candidate@sha256:" + "b" * 64
CANARY = "fixture-secret-do-not-emit-5bf49e"


class Offline(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for name, value in (("STATE", self.directory), ("PROJECT", self.directory), ("PING", self.directory / "no-ping")):
            manager = patch.object(u, name, value)
            manager.start()
            self.addCleanup(manager.stop)

    def test_git_is_data_with_one_service_and_exact_owned_digest(self):
        text = (GUEST / "compose.caddy-image.yaml").read_text()
        self.assertRegex(u.pin(text), u.REF)
        for invalid in (text + "  hermes:\n    image: evil\n", text.replace("caddy:", "hermes:"),
                        text.replace("candidate@", "latest@"), text.replace("ghcr.io/kelchm", "evil.io/kelchm"),
                        text + "x: !!python/object/apply:os.system ['touch /tmp/evil']\n"):
            with self.subTest(invalid=invalid), self.assertRaises(u.Failure):
                u.pin(invalid)

    def test_actual_subprocess_boundary_is_scoped_sanitized_and_bounded(self):
        calls = []
        def run(argv, **options):
            calls.append((argv, options))
            return subprocess.CompletedProcess(argv, 0, "", CANARY)
        with patch.object(u.subprocess, "run", side_effect=run):
            u.Updater().up()
            u.Updater().up(rollback=True)
        for index, (argv, options) in enumerate(calls):
            self.assertEqual(argv[-9:], ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "-t", "1" if index else "10", "caddy"])
            self.assertEqual(options["timeout"], 60)
            self.assertNotIn("CLOUDFLARE_API_TOKEN", options["env"])
            self.assertNotIn("COMPOSE_FILE", options["env"])
            self.assertFalse(options.get("shell", False))
        with patch.object(u.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 60)):
            with self.assertRaises(u.Uncertain):
                u.Updater().up()

    def test_image_rejections_happen_before_any_cutover_without_secret_output(self):
        valid = ["sha256:" + "1" * 64, [u.IMAGE + "@" + B.split("@")[1]], "1000:1000", "linux", "amd64",
                 {"org.opencontainers.image.source": u.REPOSITORY,
                  "org.opencontainers.image.revision": "1" * 40}]
        for field, value in ((1, []), (2, "root"), (4, "arm64"), (5, {})):
            bad = valid.copy()
            bad[field] = value
            with patch.object(u, "command", return_value=""), patch.object(u.Updater, "image", return_value=bad), \
                    patch.object(u.Updater, "up") as up:
                with self.assertRaisesRegex(u.Failure, "image-identity"):
                    u.Updater().prepare(B)
                up.assert_not_called()

    def test_old_reviewed_revision_is_valid_without_current_input_or_check_api_gate(self):
        labels = {"org.opencontainers.image.source": u.REPOSITORY, "org.opencontainers.image.revision": "0" * 40}
        with patch.object(u, "command", return_value="") as call, patch.object(u.Updater, "image", return_value=[
                "sha256:" + "1" * 64, [u.IMAGE + "@" + A.split("@")[1]], "1000:1000", "linux", "amd64", labels]):
            self.assertEqual(u.Updater().prepare(A)["oci_revision"], "0" * 40)
        commands = json.dumps(call.call_args_list, default=str)
        self.assertNotIn("api.github", commands)
        self.assertNotIn("/srv/caddy", commands)
        self.assertIn("qualification-only", commands)

    def test_failed_rollback_freezes_and_never_races_uncertain_mutation(self):
        pending = {"previous": {"ref": A, "id": "old"}, "target": {"ref": B, "id": "new"}, "hermes": {}}
        u.save("pending.json", pending)
        with patch.object(u.Updater, "image", return_value=["wrong"]):
            with self.assertRaisesRegex(u.Failure, "recovery-required"):
                u.Updater().run()
        self.assertTrue(u.read("pending.json")["recovery_required"])
        with patch.object(u.Updater, "up") as up, patch.object(u.Updater, "desired") as fetch:
            with self.assertRaisesRegex(u.Failure, "recovery-required"):
                u.Updater().run()
            up.assert_not_called()
            fetch.assert_not_called()

    def test_compose_timeout_leaves_pending_without_starting_a_second_mutator(self):
        old, new = {"ref": A, "id": "old"}, {"ref": B, "id": "new"}
        u.save("current.json", old)
        with patch.object(u.Updater, "healthy", return_value={}), patch.object(u.Updater, "image", return_value=["old"]), \
                patch.object(u.Updater, "container", return_value={}), patch.object(u, "https", return_value="fingerprint"), \
                patch.object(u.Updater, "up", side_effect=u.Uncertain("command-timeout")), patch.object(u.Updater, "recover") as restore:
            with self.assertRaises(u.Uncertain):
                u.Updater().apply(new, "1" * 40)
            restore.assert_not_called()
        self.assertTrue(u.read("pending.json")["recovery_required"])
        self.assertEqual(u.read("current.json"), old)

    def test_installer_stages_legacy_paused_without_recreate_or_secret_resolution(self):
        project, state = self.directory / "project", self.directory / "new-state"
        project.mkdir()
        (project / "compose.yaml").write_text("saved-old-compose")
        expected, calls = "sha256:" + "1" * 64, []
        def call(argv):
            calls.append(argv)
            if argv[1] == "inspect":
                return json.dumps([expected, "hermes"])
            if "config" in argv:
                self.assertIn("--no-env-resolution", argv)
                second = len([item for item in calls if "config" in item]) == 2
                return json.dumps({"services": {"hermes": {"image": "unchanged"}, "caddy":
                    {"image": "legacy", "user": "1000:1000"} if second else {"image": "old", "build": {"context": "."}}}})
            return ""
        installer.prepare(project, state, expected, call, "2" * 40)
        self.assertTrue((state / "paused").exists())
        self.assertEqual(json.loads((state / "current.json").read_text())["id"], expected)
        self.assertIn("COMPOSE_FILE=compose.yaml:compose.caddy-image.yaml", (project / ".env").read_text())
        self.assertEqual((project / "compose.yaml.pre-caddy-delivery").read_text(), "saved-old-compose")
        self.assertFalse(any(item in argv for argv in calls for item in ("up", "restart", "start", "enable")))

    def test_exclusive_lock_rejects_even_status_without_mutation(self):
        with (u.STATE / "lock").open("w") as lock:
            u.fcntl.flock(lock, u.fcntl.LOCK_EX | u.fcntl.LOCK_NB)
            with patch.object(u.os, "geteuid", return_value=0), patch.object(sys, "argv", ["updater", "run"]), \
                    patch.object(u.Updater, "run") as run:
                with self.assertRaisesRegex(SystemExit, "busy"):
                    u.main()
                run.assert_not_called()

    def test_https_requires_hostname_trust_and_expiry_not_fingerprint_equality(self):
        socket_mock = unittest.mock.Mock()
        socket_mock.getpeercert.side_effect = lambda binary_form=False: b"new renewed cert" if binary_form else {"notAfter": "Jan  1 00:00:00 2099 GMT"}
        connection = unittest.mock.Mock(sock=socket_mock)
        connection.getresponse.return_value.status = 200
        with patch.object(u.http.client, "HTTPSConnection", return_value=connection):
            self.assertEqual(len(u.https()), 64)
            socket_mock.getpeercert.side_effect = lambda binary_form=False: b"old" if binary_form else {"notAfter": "Jan  1 00:00:00 2000 GMT"}
            with self.assertRaisesRegex(u.Failure, "expiry"):
                u.https()
        self.assertTrue(u.tls_context().check_hostname)
        self.assertEqual(u.tls_context().verify_mode, ssl.CERT_REQUIRED)

    def test_one_root_only_heartbeat_success_and_failure(self):
        path = self.directory / "ping"
        path.write_text("https://hc-ping.com/00000000-0000-0000-0000-000000000000\n")
        path.chmod(0o600)
        with patch.object(u, "PING", path), patch.object(Path, "stat", return_value=unittest.mock.Mock(st_uid=0, st_mode=0o600)), \
                patch.object(u.urllib.request, "urlopen") as request:
            u.heartbeat(True)
            u.heartbeat(False)
            self.assertEqual([call.args[0].full_url for call in request.call_args_list],
                             [path.read_text().strip(), path.read_text().strip() + "/fail"])

    def test_main_routes_idle_or_rollout_failure_to_one_failure_heartbeat(self):
        with patch.object(u.os, "geteuid", return_value=0), patch.object(sys, "argv", ["updater", "run"]), \
                patch.object(u.Updater, "run", side_effect=u.Failure("process-health")), patch.object(u, "heartbeat") as heartbeat:
            with self.assertRaisesRegex(SystemExit, "process-health"):
                u.main()
            heartbeat.assert_called_once_with(False)

    def test_installer_refuses_unrelated_config_and_existing_environment_without_writes(self):
        project = self.directory / "project"
        project.mkdir()
        (project / "compose.yaml").write_text("old-config")
        expected = "sha256:" + "1" * 64
        for drift in (True, False):
            calls = []
            def call(argv):
                calls.append(argv)
                if argv[1] == "inspect":
                    return json.dumps([expected, "hermes"])
                if "config" in argv:
                    return json.dumps({"services": {"caddy": {"image": "old"},
                                                    "hermes": {"image": "new" if drift and len([c for c in calls if 'config' in c]) == 2 else "old"}}})
                return ""
            if not drift:
                (project / ".env").write_text(CANARY)
            with self.assertRaises(RuntimeError):
                installer.prepare(project, self.directory / "new-state", expected, call)
            self.assertFalse((self.directory / "new-state").exists())
            self.assertEqual((project / "compose.yaml").read_text(), "old-config")
            self.assertFalse(any(item in call for call in calls for item in ("up", "restart", "tag")))


@unittest.skipUnless(DOCKER, "use --docker on a disposable fixture runtime")
class Docker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep paths on the shared home filesystem for macOS Colima bind mounts.
        cls.scratch = tempfile.TemporaryDirectory(prefix=".caddy-fixture-", dir=ROOT)
        cls.directory = Path(cls.scratch.name)
        cls.project_name = "caddy-test-" + uuid.uuid4().hex[:12]
        cls.binary = shutil.which("docker")
        cls.original_command = u.command
        cls.images = {}
        config = cls.directory / "docker-config"
        config.mkdir()
        plugins = Path("/opt/homebrew/lib/docker/cli-plugins")
        (config / "config.json").write_text(json.dumps({"cliPluginsExtraDirs": [str(plugins)]} if plugins.exists() else {}))
        cls.stack = contextlib.ExitStack()
        cls.stack.enter_context(patch.object(u, "PROJECT", cls.directory))
        cls.stack.enter_context(patch.object(u, "STATE", cls.directory / "state"))
        cls.stack.enter_context(patch.object(u, "PING", cls.directory / "no-real-heartbeat"))
        cls.stack.enter_context(patch.multiple(u, HOST="localhost", READY=5, SETTLE=5, INTERVAL=0.2, PID_LIMIT=32))
        def command(argv, **options):
            argv = list(argv)
            if argv[0] == "docker":
                argv[0] = cls.binary
                argv[1:1] = ["--config", str(config)]
                if os.environ.get("DOCKER_HOST"):
                    argv[1:1] = ["--host", os.environ["DOCKER_HOST"]]
                for index, value in enumerate(argv):
                    if value == "-p" and argv[index + 1] == "hermes":
                        argv[index + 1] = cls.project_name
            try:
                return cls.original_command(argv, **options)
            except u.Failure:
                if any(item in argv for item in ("inspect", "top", "exec")):
                    result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
                    print("fixture diagnostic:", argv, result.stderr.replace(CANARY, "[redacted]"), file=sys.stderr)
                raise
        cls.stack.enter_context(patch.object(u, "command", side_effect=command))
        try:
            for mode in ("good", "new-good", "exit", "serve-fail", "slow", "leak"):
                tag = f"localhost/{cls.project_name}:{mode}"
                u.command(["docker", "build", "--build-arg", f"MODE={mode}", "-t", tag, str(GUEST / "caddy/test-fixture")], timeout=180)
                cls.images[mode] = tag
            tls = cls.directory / "tls"
            tls.mkdir()
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
                            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                            "-keyout", str(tls / "key.pem"), "-out", str(tls / "cert.pem")], check=True, capture_output=True)
            (tls / "key.pem").chmod(0o644)  # Only a generated fixture key.
            cls.stack.enter_context(patch.object(u, "tls_context", side_effect=lambda: ssl.create_default_context(cafile=str(tls / "cert.pem"))))
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                cls.stack.enter_context(patch.object(u, "PORT", sock.getsockname()[1]))
            (cls.directory / ".env").write_text("COMPOSE_FILE=compose.yaml:compose.caddy-image.yaml\n")
            (cls.directory / "fixture.env").write_text("CLOUDFLARE_API_TOKEN=" + CANARY + "\n")
            (cls.directory / "fixture.env").chmod(0o600)
            health = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:1', timeout=1)"]
            # HTTPS fixture certificate is verified separately by the updater.
            health[-1] = "import ssl,urllib.request; urllib.request.urlopen('https://localhost:8443/api/status', context=ssl._create_unverified_context(),timeout=1)"
            compose = {"services": {"hermes": {"image": cls.images["good"], "command": ["python", "-c", "import time; time.sleep(600)"]},
                "caddy": {"image": cls.images["good"], "user": "1000:1000", "read_only": True,
                          "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
                          "tmpfs": ["/tmp"], "ports": [f"127.0.0.1:{u.PORT}:8443"],
                          "volumes": ["./tls:/tls:ro"], "env_file": ["./fixture.env"],
                          "depends_on": ["hermes"], "restart": "unless-stopped",
                          "healthcheck": {"test": health, "interval": "1s", "timeout": "1s", "retries": 1}}}}
            (cls.directory / "compose.yaml").write_text(json.dumps(compose))
            u.overlay(cls.images["good"])
            u.Updater().compose("up", "-d", "--pull", "never")
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            u.Updater().compose("down", "--volumes", "--remove-orphans")
            for tag in cls.images.values():
                u.command(["docker", "image", "rm", tag])
        finally:
            cls.stack.close()
            cls.scratch.cleanup()

    def setUp(self):
        shutil.rmtree(u.STATE, ignore_errors=True)
        u.STATE.mkdir()
        u.overlay(self.images["good"])
        u.Updater().compose("up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "caddy")
        self.old = {"ref": self.images["good"], "id": u.Updater().image(self.images["good"])[0]}
        u.save("current.json", self.old)
        u.Updater().ready(self.old)
        self.hermes = u.Updater().container("hermes")
        self.secret = (u.PROJECT / "fixture.env").read_bytes()
        self.base = (u.PROJECT / "compose.yaml").read_text()

    def tearDown(self):
        self.assertEqual(u.Updater().container("hermes"), self.hermes, "only Caddy may change")
        self.assertEqual((u.PROJECT / "fixture.env").read_bytes(), self.secret)
        self.assertEqual((u.PROJECT / "fixture.env").stat().st_mode & 0o777, 0o600)
        (u.PROJECT / "compose.yaml").write_text(self.base)
        for file in u.STATE.rglob("*.json"):
            self.assertNotIn(CANARY, file.read_text(), "no secrets in receipts")

    def target(self, mode):
        ref = self.images[mode]
        return {"ref": ref, "id": u.Updater().image(ref)[0], "oci_revision": "1" * 40}

    def test_success_noop_and_old_reviewed_git_digest_rollback(self):
        updater = u.Updater()
        # Ordinary Compose discovery really consumes .env, with relative mounts intact.
        self.assertIn("caddy", u.command(["docker", "compose", "-p", "hermes", "ps", "--services"], cwd=u.PROJECT))
        u.command(["docker", "compose", "-p", "hermes", "logs", "--tail", "1", "hermes"], cwd=u.PROJECT)
        # Without --no-deps, Compose would recreate this deliberately drifted
        # sentinel. Identity assertions therefore catch a scope regression.
        drift = json.loads(self.base)
        drift["services"]["hermes"]["labels"] = {"fixture.config": "changed"}
        (u.PROJECT / "compose.yaml").write_text(json.dumps(drift))
        for desired in (self.target("new-good"), self.old):
            # Models a main pin reverting to older accepted bytes; no source-input gate.
            with patch.object(updater, "desired", return_value=("2" * 40, desired["ref"])), \
                    patch.object(updater, "prepare", return_value=desired), patch.object(u, "heartbeat"):
                updater.run()
                accepted = updater.container("caddy")
                updater.run()
                self.assertEqual(updater.container("caddy"), accepted)
                self.assertEqual(u.read("last.json")["result"], "accepted")
                self.assertIn("sampled_outage_seconds", u.read("last.json"))

    def test_exit_serving_and_slow_failure_restore_exact_predecessor_and_quarantine(self):
        for mode in ("exit", "serve-fail", "slow", "leak"):
            with self.subTest(mode=mode), self.assertRaises(u.Failure):
                u.Updater().apply(self.target(mode), "3" * 40)
            self.assertEqual(u.Updater().container("caddy")["id"], self.old["id"])
            self.assertFalse((u.STATE / "pending.json").exists())
            self.assertEqual(u.read("last.json")["result"], "rolled-back")
            with patch.object(u.Updater, "desired", return_value=("4" * 40, self.images[mode])), \
                    patch.object(u.Updater, "prepare") as pull:
                with self.assertRaisesRegex(u.Failure, "quarantined"):
                    u.Updater().run()
                pull.assert_not_called()

    def test_pending_crash_recovery_before_and_after_cutover_even_when_paused(self):
        original = u.Updater.up
        for cutover in (False, True):
            def crash(instance, rollback=False):
                if cutover:
                    original(instance, rollback=rollback)
                raise SystemExit("simulated abrupt termination")
            with patch.object(u.Updater, "up", crash), self.assertRaises(SystemExit):
                u.Updater().apply(self.target("new-good"), "5" * 40)
            self.assertTrue((u.STATE / "pending.json").exists())
            (u.STATE / "paused").touch()
            u.Updater().run()  # New instance, pending recovery precedes pause/fetch.
            self.assertEqual(u.Updater().container("caddy")["id"], self.old["id"])
            self.assertFalse((u.STATE / "pending.json").exists())

    def test_idle_slow_process_leak_is_reported_without_recreation(self):
        before = u.Updater().container("caddy")
        u.command(["docker", "exec", "-d", before["container"], "python", "-c",
                   "import subprocess,time; [subprocess.Popen(['sleep','120']) for _ in range(40)]; time.sleep(120)"])
        deadline = time.monotonic() + 5
        while int(u.command(["docker", "exec", before["container"], "cat", "/sys/fs/cgroup/pids.current"])) <= u.PID_LIMIT:
            if time.monotonic() > deadline:
                self.fail("fixture leak did not appear")
        with patch.object(u.Updater, "desired", return_value=("6" * 40, self.old["ref"])), \
                self.assertRaisesRegex(u.Failure, "process-health"):
            u.Updater().run()
        self.assertEqual(u.Updater().container("caddy"), before)

    def test_real_tls_trust_and_readonly_runtime(self):
        with patch.object(u, "tls_context", side_effect=ssl.create_default_context), self.assertRaises(u.Failure):
            u.https()  # The generated fixture CA is not public system trust.
        self.assertEqual(len(u.https()), 64)
        with self.assertRaises(u.Failure):
            u.command(["docker", "exec", u.Updater().container("caddy")["container"], "touch", "/readonly-canary"])

    def test_failed_restore_retains_pending_and_stops_all_automatic_mutations(self):
        original = u.Updater.up
        def fail_restore(instance, rollback=False):
            if rollback:
                raise u.Failure("fixture-restore-failure")
            return original(instance)
        with patch.object(u.Updater, "up", fail_restore), self.assertRaisesRegex(u.Failure, "recovery-required"):
            u.Updater().apply(self.target("serve-fail"), "7" * 40)
        self.assertTrue(u.read("pending.json")["recovery_required"])
        with patch.object(u.Updater, "up") as up, self.assertRaisesRegex(u.Failure, "recovery-required"):
            u.Updater().run()
        up.assert_not_called()
        u.Updater().recover(u.read("pending.json"))  # Attended recovery after inspecting the failure.
        self.assertEqual(u.Updater().container("caddy")["id"], self.old["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
