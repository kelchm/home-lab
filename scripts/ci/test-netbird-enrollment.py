#!/usr/bin/env python3
"""Offline enrollment fixtures: dummy input, isolated paths, no root/network/NetBird."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "proxmox/guests/netbird-router/netbird-router-enroll.sh"


class Enrollment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="netbird-enrollment-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        timeout = shutil.which("timeout")
        self.assertIsNotNone(timeout, "GNU timeout is required")
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        FIXTURE=str(self.base), REAL_TIMEOUT=timeout, CASE="success")
        self.write("timeout", """#!/bin/sh
[ "$1 $2 $3" = '--signal=TERM --kill-after=5s 90s' ] || exit 99
shift 3
exec "$REAL_TIMEOUT" --signal=TERM --kill-after=1s 1s "$@"
""")
        self.write("mktemp", """#!/usr/bin/env python3
import os, pathlib, sys, tempfile
assert sys.argv[1:] == ['/run/netbird-router-enroll.XXXXXX']
fd, name = tempfile.mkstemp(prefix='key-', dir=os.environ['FIXTURE'])
os.close(fd)
with open(pathlib.Path(os.environ['FIXTURE']) / 'created', 'a') as f:
    f.write(name + '\\n')
print(name)
""")
        self.write("netbird", """#!/usr/bin/env python3
import os, pathlib, stat, sys, time
assert len(sys.argv) == 6, sys.argv
assert sys.argv[1:3] == ['up', '--setup-key-file']
assert sys.argv[4:] == ['--disable-client-routes', '--disable-dns']
p = pathlib.Path(sys.argv[3])
assert p.parent == pathlib.Path(os.environ['FIXTURE'])
assert stat.S_IMODE(p.stat().st_mode) == 0o600
assert p.read_bytes() == b'fixture-input\\n'
assert all('fixture-input' not in arg for arg in sys.argv)
(pathlib.Path(os.environ['FIXTURE']) / 'called').touch()
if os.environ['CASE'] == 'hang':
    time.sleep(20)
sys.exit(23 if os.environ['CASE'] == 'failure' else 0)
""")

    def write(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)

    def run_helper(self, data=b"fixture-input\n", case="success", args=()):
        self.env["CASE"] = case
        result = subprocess.run(["sh", str(HELPER), *args], input=data, env=self.env,
                                capture_output=True, timeout=5)
        self.assertNotIn(b"fixture-input", result.stdout + result.stderr)
        self.assertEqual(list(self.base.glob("key-*")), [])
        return result

    def test_success_and_unique_files(self):
        for _ in range(2):
            self.assertEqual(self.run_helper().returncode, 0)
        paths = (self.base / "created").read_text().splitlines()
        self.assertEqual(len(paths), 2)
        self.assertEqual(len(set(paths)), 2)

    def test_netbird_failure_cleanup(self):
        self.assertEqual(self.run_helper(case="failure").returncode, 23)

    def test_empty_input_does_not_enroll(self):
        self.assertEqual(self.run_helper(data=b"").returncode, 65)
        self.assertFalse((self.base / "called").exists())

    def test_reject_argv(self):
        self.assertEqual(self.run_helper(args=("unexpected",)).returncode, 64)
        self.assertFalse((self.base / "created").exists())

    def test_enrollment_timeout_cleanup(self):
        self.assertEqual(self.run_helper(case="hang").returncode, 124)

    def test_stalled_stdin_cleanup(self):
        p = subprocess.Popen(["sh", str(HELPER)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        self.addCleanup(lambda: p.kill() if p.poll() is None else None)
        p.wait(timeout=5)
        p.communicate()
        self.assertEqual(p.returncode, 124)
        self.assertEqual(list(self.base.glob("key-*")), [])
        self.assertFalse((self.base / "called").exists())

    def test_signal_cleanup(self):
        self.env["CASE"] = "hang"
        p = subprocess.Popen(["sh", str(HELPER)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        self.addCleanup(lambda: p.kill() if p.poll() is None else None)
        p.stdin.write(b"fixture-input\n")
        p.stdin.close()
        p.stdin = None
        deadline = time.monotonic() + 3
        while not (self.base / "called").exists() and p.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.base / "called").exists())
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=5)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(list(self.base.glob("key-*")), [])


if __name__ == "__main__":
    unittest.main()
