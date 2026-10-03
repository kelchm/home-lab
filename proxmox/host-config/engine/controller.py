#!/usr/bin/python3
"""PVE-only local controller. The fetched tree supplies data, never executable code."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import uuid

PAYLOAD = "proxmox/host-config/payload"
VERSIONS = {"git": "1:2.47.3-0+deb13u1", "ansible-core": "2.19.11-0+deb13u1"}


def atomic(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", delete=False) as out:
        out.write(data)
        os.chmod(out.name, 0o644)
        out.flush()
        os.fsync(out.fileno())
    os.replace(out.name, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run(args, timeout=60, env=None, cwd=None, on_interrupt=None):
    # Kill the process group as well: a hung Git transport must not retain the lock.
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, env=env, cwd=cwd, start_new_session=True) as proc:
        try:
            output, _ = proc.communicate(timeout=timeout)
        except BaseException as error:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if on_interrupt:
                on_interrupt()
            proc.communicate()
            if isinstance(error, subprocess.TimeoutExpired):
                raise RuntimeError(f"timeout: {args[0]} after {timeout}s") from error
            raise
        if proc.returncode:
            raise RuntimeError(f"exit {proc.returncode}: {' '.join(args)}\n{output}")
        return output


class Controller:
    def __init__(self, config):
        self.c = config
        self.base = Path(config["state_dir"])
        self.base.mkdir(parents=True, exist_ok=True)
        self.repo = self.base / "repo.git"
        self.engine = Path(config["engine_dir"])
        self.status = self.base / "status.json"
        self.s = json.loads(self.status.read_text()) if self.status.exists() else {}
        self.fixture = config.get("unscoped_fixture_executor", False)
        if self.fixture and Path("/etc/pve").exists():
            raise RuntimeError("unscoped fixture executor is forbidden on PVE")
        if not self.fixture and (os.geteuid() != 0 or not Path("/run/systemd/system").exists()):
            raise RuntimeError("production plays require root and systemd; harmless CI must explicitly select the unscoped fixture executor")
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.base), "LANG": "C.UTF-8",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oConnectTimeout=5",
                    "ANSIBLE_CONFIG": str(self.engine / "ansible.cfg"),
                    "ANSIBLE_NOCOLOR": "1", "ANSIBLE_LOCAL_TEMP": str(self.base / "tmp")}

    def git(self, *args):
        return run(["git", "--git-dir", str(self.repo), *args],
                   self.c.get("fetch_timeout", 45), self.env).strip()

    def node(self, revision):
        node = json.loads(self.git("show", f"{revision}:{PAYLOAD}/nodes.json"))[self.c["host_id"]]
        if not isinstance(node, dict) or set(node) not in (
                {"enabled", "node_exporter", "collectors"},
                {"enabled", "node_exporter", "collectors", "vmagent"}):
            raise ValueError("unexpected node keys")
        if type(node["enabled"]) is not bool or node["node_exporter"] not in ("present", "absent"):
            raise ValueError("invalid enrollment/exporter state")
        if not isinstance(node["collectors"], list) or len(node["collectors"]) > 16 or any(
                not isinstance(x, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", x)
                for x in node["collectors"]):
            raise ValueError("invalid collectors")
        node.setdefault("vmagent", "absent")  # Older applied trees remain checkable.
        if node["vmagent"] not in ("present", "absent"):
            raise ValueError("invalid vmagent state")
        if len(set(node["collectors"])) != len(node["collectors"]):
            raise ValueError("duplicate collectors")
        return node

    def stop_play(self, unit):
        if not re.fullmatch(r"pve-hostcfg-play-[0-9a-f]{32}\.service", unit):
            raise RuntimeError("invalid recorded play unit")
        # Fail closed: keep the controller lock until this exact unit is gone.
        # A process group cannot contain Ansible workers that call setsid().
        stopped = False
        while True:
            if not stopped:
                try:
                    run(["systemctl", "stop", "--no-block", unit], 10, self.env)
                    stopped = True
                except (RuntimeError, OSError):
                    pass  # A collected unit is already absent; show confirms it below.
            try:
                result = subprocess.run(["systemctl", "show", unit, "-p", "LoadState", "--value"],
                                        env=self.env, text=True, capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                time.sleep(1)
                continue
            if result.stdout.strip() == "not-found":
                return
            time.sleep(0.1)

    def play(self, revision, mode, force_restart=False):
        node = self.node(revision)
        if not re.fullmatch(r"pve-sbx-[123]", self.c["host_id"]):
            raise ValueError("invalid engine-owned host identity")
        # Only immutable JSON from git-show enters the engine-owned playbook.
        vars_file = self.base / "vars.json"
        atomic(vars_file, json.dumps({"exporter_state": node["node_exporter"],
                                     "collectors": node["collectors"],
                                     "vmagent_state": node["vmagent"],
                                     "host_id": self.c["host_id"],
                                     "force_restart": force_restart,
                                     "engine_dir": str(self.engine),
                                     "textfile_dir": self.c["textfile_dir"]}))
        args = [self.c.get("ansible", "/usr/bin/ansible-playbook"),
                str(self.engine / "node-exporter.yml"), "-i", "localhost,",
                "-c", "local", "--limit", "localhost", "-e", "@" + str(vars_file)]
        if mode == "syntax":
            args.append("--syntax-check")
        elif mode == "check":
            args.append("--check")
        timeout = 30 if mode == "syntax" else 120 if mode == "check" else min(self.c.get("apply_timeout", 300), 300)
        if self.fixture:
            output = run(args, timeout, self.env, self.engine)
        else:
            unit = "pve-hostcfg-play-" + uuid.uuid4().hex + ".service"
            self.s["play_unit"] = unit
            self.report()
            scoped = ["systemd-run", "--unit=" + unit, "--wait", "--pipe", "--collect",
                      "--property=RuntimeMaxSec=" + str(timeout), "--property=KillMode=control-group",
                      "--property=TimeoutStopSec=5", "--working-directory=" + str(self.engine),
                      "/usr/bin/env", "-i", *[f"{k}={v}" for k, v in self.env.items()], *args]
            try:
                output = run(scoped, timeout + 15, self.env, self.engine,
                             on_interrupt=lambda: self.stop_play(unit))
            finally:
                self.stop_play(unit)
                self.s.pop("play_unit", None)
                self.report()
        print(output, flush=True)
        if mode == "syntax":
            return 0
        recap = re.search(r"localhost\s+:\s+ok=\d+\s+changed=(\d+)\s+unreachable=0\s+failed=0", output)
        if not recap:
            raise RuntimeError("missing successful localhost recap")
        return int(recap[1])

    def report(self):
        self.s["paused"] = int((self.base / "paused").exists())
        self.s["status_timestamp_seconds"] = int(time.time())
        atomic(self.status, json.dumps(self.s, sort_keys=True) + "\n")
        metrics = []
        for key in ("paused", "dirty", "fetch_success", "attempt_success", "drift", "service_active",
                    "vmagent_service_active", "status_timestamp_seconds", "fetch_timestamp_seconds", "attempt_timestamp_seconds",
                    "success_timestamp_seconds", "drift_timestamp_seconds"):
            metrics.append(f"pve_hostcfg_{key} {self.s.get(key, -1)}")
        for key in ("desired", "applied", "attempt"):
            sha = self.s.get(key, "")
            metrics.append(f'pve_hostcfg_revision_info{{kind="{key}",revision="{sha}"}} 1')
        textdir = Path(self.c["textfile_dir"])
        textdir.mkdir(parents=True, exist_ok=True)
        atomic(textdir / "pve-hostcfg.prom", "\n".join(metrics) + "\n")

    def drift(self):
        self.s["drift_timestamp_seconds"] = int(time.time())
        try:
            self.s["drift"] = int(self.play(self.s["applied"], "check") > 0) if self.s.get("applied") else -1
        except Exception as error:
            self.s["drift"] = -1
            print(f"drift check failed: {error}", flush=True)
        for metric, unit in (("service_active", "prometheus-node-exporter.service"),
                             ("vmagent_service_active", "pve-host-vmagent.service")):
            try:
                result = subprocess.run(["systemctl", "is-active", "--quiet", unit],
                                        env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                self.s[metric] = int(result.returncode == 0)
            except (OSError, subprocess.TimeoutExpired):
                self.s[metric] = -1

    def tick(self, command):
        # Emergency pause must persist even while an apply owns the lock.
        if command == "pause":
            atomic(self.base / "paused", "operator pause\n")
        with (self.base / "lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("another controller holds the lock", flush=True)
                return int(command == "resume")
            self.s = json.loads(self.status.read_text()) if self.status.exists() else {}
            # A forcibly killed manual controller can leave its bounded play alive.
            # Settle it before another apply/check can overlap its mutations.
            if self.s.get("play_unit"):
                self.stop_play(self.s["play_unit"])
                self.s.pop("play_unit")
                self.report()
            if command in ("pause", "resume"):
                if command == "resume":
                    (self.base / "paused").unlink(missing_ok=True)
                self.report()
                return 0
            failed = False
            if command == "sync":
                try:
                    if not self.repo.exists():
                        run(["git", "init", "--bare", str(self.repo)], env=self.env)
                    self.git("fetch", "--no-tags", "--force", self.c["repository"],
                             f'+refs/heads/{self.c["branch"]}:refs/heads/desired')
                    desired = self.git("rev-parse", "refs/heads/desired^{commit}")
                    self.s.update(desired=desired, fetch_success=1, fetch_timestamp_seconds=int(time.time()))
                    node = self.node(desired)
                    applied = self.s.get("applied")
                    relevant = not applied or self.git("rev-parse", f"{desired}:{PAYLOAD}") != self.git(
                        "rev-parse", f"{applied}:{PAYLOAD}")
                    if node["enabled"] and not (self.base / "paused").exists() and (relevant or self.s.get("dirty")):
                        force_restart = bool(self.s.get("dirty"))
                        self.s.update(attempt=desired, attempt_timestamp_seconds=int(time.time()), attempt_success=0)
                        self.report()
                        self.play(desired, "syntax")
                        # A failed/interrupted apply can have changed the host. Never skip its recovery.
                        self.s["dirty"] = 1
                        self.report()
                        self.play(desired, "apply", force_restart=force_restart)
                        self.git("update-ref", "refs/heads/applied", desired)
                        self.s.update(applied=desired, dirty=0, attempt_success=1,
                                      success_timestamp_seconds=int(time.time()))
                except Exception as error:
                    failed = True
                    if "desired" not in locals():
                        self.s["fetch_success"] = 0
                    else:
                        self.s.update(attempt=desired, attempt_success=0, attempt_timestamp_seconds=int(time.time()))
                    print(f"sync failed: {error}", flush=True)
            self.drift()
            self.report()
            return int(failed)


def main():
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("sync", "check", "pause", "resume"))
    args = parser.parse_args()
    config_path = Path("/etc/pve-hostcfg/config.json")
    if config_path.stat().st_uid != 0 or config_path.stat().st_mode & 0o022:
        raise RuntimeError("engine config must be root-owned and not writable by group/other")
    config = json.loads(config_path.read_text())
    for package, version in (VERSIONS.items() if args.command in ("sync", "check") else []):
        if run(["dpkg-query", "-W", "-f=${Version}", package]).strip() != version:
            raise RuntimeError(f"{package} engine version differs; explicitly re-bootstrap")
    raise SystemExit(Controller(config).tick(args.command))


if __name__ == "__main__":
    main()
