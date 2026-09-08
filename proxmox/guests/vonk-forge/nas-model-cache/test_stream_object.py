#!/usr/bin/env python3
"""Unittest coverage for stream-object.py (Python 3.8, stdlib only)."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest


def _load_stream_object():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stream-object.py")
    spec = importlib.util.spec_from_file_location("stream_object", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stream_object = _load_stream_object()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _write_file(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _leftover_temps(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.startswith(".stream-import-"):
                found.append(os.path.join(dirpath, name))
    return found


class TestStreamObject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stream-object-test-")
        self.source_root = os.path.join(self.tmp, "src")
        self.dest_root = os.path.join(self.tmp, "dst")
        os.mkdir(self.source_root)
        os.mkdir(self.dest_root)
        self.payload = b"MDL\x00\xff\xfe-retained-" + os.urandom(2048)
        self.sha = _sha256(self.payload)
        self.size = len(self.payload)
        self.source_path = os.path.join(self.source_root, "model.bin")
        _write_file(self.source_path, self.payload)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _object_path(self, root=None, sha=None):
        sha = self.sha if sha is None else sha
        root = self.dest_root if root is None else root
        return os.path.join(root, "objects", sha[:2], sha)

    def _send(self, source=None, sha=None, size=None, source_roots=None, max_mbps=20.0):
        stdout = io.BytesIO()
        stream_object.send_object(
            source=self.source_path if source is None else source,
            sha256=self.sha if sha is None else sha,
            size=self.size if size is None else size,
            stdout=stdout,
            source_roots=[self.source_root] if source_roots is None else source_roots,
            max_mbps=max_mbps,
        )
        return stdout.getvalue()

    def _receive(self, blob, dest_root=None, sha=None, size=None):
        stdout = io.BytesIO()
        result = stream_object.receive_object(
            destination_root=self.dest_root if dest_root is None else dest_root,
            sha256=self.sha if sha is None else sha,
            size=self.size if size is None else size,
            stdin=io.BytesIO(blob),
            stdout=stdout,
        )
        return result, stdout.getvalue()

    def test_successful_transfer(self):
        blob = self._send()
        self.assertTrue(blob.endswith(stream_object.SUCCESS_TRAILER))
        self.assertEqual(blob[: self.size], self.payload)
        result, raw = self._receive(blob)
        dest = self._object_path()
        self.assertTrue(os.path.isfile(dest))
        self.assertFalse(os.path.islink(dest))
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), self.payload)
        self.assertEqual(stat.S_IMODE(os.lstat(dest).st_mode), 0o640)
        prefix = os.path.dirname(dest)
        self.assertEqual(stat.S_IMODE(os.lstat(prefix).st_mode), 0o750)
        self.assertEqual(result["sha256"], self.sha)
        self.assertEqual(result["bytes"], self.size)
        self.assertTrue(result["verified"])
        self.assertGreaterEqual(result["time"], 0)
        parsed = json.loads(raw.decode("ascii"))
        self.assertEqual(parsed["sha256"], self.sha)
        self.assertEqual(parsed["bytes"], self.size)
        self.assertTrue(parsed["verified"])
        self.assertEqual(_leftover_temps(self.dest_root), [])

    def test_source_hash_mismatch(self):
        other = _sha256(self.payload + b"x")
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.send_object(
                source=self.source_path,
                sha256=other,
                size=self.size,
                stdout=stdout,
                source_roots=[self.source_root],
            )
        blob = stdout.getvalue()
        self.assertFalse(blob.endswith(stream_object.SUCCESS_TRAILER))
        self.assertNotIn(stream_object.SUCCESS_TRAILER, blob)

    def test_source_size_mismatch(self):
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.send_object(
                source=self.source_path,
                sha256=self.sha,
                size=self.size + 1,
                stdout=stdout,
                source_roots=[self.source_root],
            )
        self.assertEqual(stdout.getvalue(), b"")

    def test_source_symlink_rejected(self):
        link = os.path.join(self.source_root, "model.link")
        os.symlink(self.source_path, link)
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.send_object(
                source=link,
                sha256=self.sha,
                size=self.size,
                stdout=stdout,
                source_roots=[self.source_root],
            )
        self.assertEqual(stdout.getvalue(), b"")

    def test_source_outside_allowed_root(self):
        outside_root = os.path.join(self.tmp, "outside")
        os.mkdir(outside_root)
        outside = os.path.join(outside_root, "model.bin")
        _write_file(outside, self.payload)
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.send_object(
                source=outside,
                sha256=self.sha,
                size=self.size,
                stdout=stdout,
                source_roots=[self.source_root],
            )
        self.assertEqual(stdout.getvalue(), b"")

    def test_corrupted_stream(self):
        blob = bytearray(self._send())
        blob[0] ^= 0xFF
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.receive_object(
                destination_root=self.dest_root,
                sha256=self.sha,
                size=self.size,
                stdin=io.BytesIO(bytes(blob)),
                stdout=stdout,
            )
        self.assertEqual(stdout.getvalue(), b"")
        self.assertFalse(os.path.lexists(self._object_path()))
        self.assertEqual(_leftover_temps(self.dest_root), [])

    def test_truncated_stream(self):
        full = self._send()
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.receive_object(
                destination_root=self.dest_root,
                sha256=self.sha,
                size=self.size,
                stdin=io.BytesIO(full[: self.size // 2]),
                stdout=stdout,
            )
        self.assertEqual(stdout.getvalue(), b"")
        self.assertFalse(os.path.lexists(self._object_path()))
        self.assertEqual(_leftover_temps(self.dest_root), [])

        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.receive_object(
                destination_root=self.dest_root,
                sha256=self.sha,
                size=self.size,
                stdin=io.BytesIO(full[: -len(stream_object.SUCCESS_TRAILER) // 2]),
                stdout=stdout,
            )
        self.assertEqual(_leftover_temps(self.dest_root), [])

    def test_trailing_bytes(self):
        blob = self._send() + b"EXTRA"
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.receive_object(
                destination_root=self.dest_root,
                sha256=self.sha,
                size=self.size,
                stdin=io.BytesIO(blob),
                stdout=stdout,
            )
        self.assertEqual(stdout.getvalue(), b"")
        self.assertFalse(os.path.lexists(self._object_path()))
        self.assertEqual(_leftover_temps(self.dest_root), [])

    def test_existing_corrupt_destination_preserved(self):
        dest = self._object_path()
        os.makedirs(os.path.dirname(dest), mode=0o750)
        corrupt = b"old-corrupt-content-not-matching-hash"
        _write_file(dest, corrupt)
        before = os.lstat(dest)
        blob = self._send()
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.receive_object(
                destination_root=self.dest_root,
                sha256=self.sha,
                size=self.size,
                stdin=io.BytesIO(blob),
                stdout=stdout,
            )
        self.assertEqual(stdout.getvalue(), b"")
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), corrupt)
        after = os.lstat(dest)
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual(_leftover_temps(self.dest_root), [])

    def test_valid_existing_no_overwrite(self):
        blob = self._send()
        self._receive(blob)
        dest = self._object_path()
        before = os.lstat(dest)
        result, _raw = self._receive(self._send())
        after = os.lstat(dest)
        self.assertTrue(result["verified"])
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(before.st_dev, after.st_dev)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), self.payload)
        self.assertEqual(_leftover_temps(self.dest_root), [])

    def test_destination_parent_symlink_rejected(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.mkdir(elsewhere)
        objects = os.path.join(self.dest_root, "objects")
        os.symlink(elsewhere, objects)
        blob = self._send()
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.receive_object(
                destination_root=self.dest_root,
                sha256=self.sha,
                size=self.size,
                stdin=io.BytesIO(blob),
                stdout=stdout,
            )
        self.assertEqual(stdout.getvalue(), b"")
        self.assertEqual(_leftover_temps(self.dest_root), [])
        self.assertEqual(_leftover_temps(elsewhere), [])

        os.unlink(objects)
        os.mkdir(objects)
        prefix = os.path.join(objects, self.sha[:2])
        os.symlink(elsewhere, prefix)
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.receive_object(
                destination_root=self.dest_root,
                sha256=self.sha,
                size=self.size,
                stdin=io.BytesIO(blob),
                stdout=stdout,
            )
        self.assertEqual(stdout.getvalue(), b"")
        self.assertEqual(_leftover_temps(self.dest_root), [])
        self.assertFalse(os.path.lexists(os.path.join(elsewhere, self.sha)))

    def test_destination_root_symlink_rejected(self):
        real = os.path.join(self.tmp, "real-root")
        os.mkdir(real)
        link = os.path.join(self.tmp, "link-root")
        os.symlink(real, link)
        stdout = io.BytesIO()
        with self.assertRaises(stream_object.StreamObjectError):
            stream_object.receive_object(
                destination_root=link,
                sha256=self.sha,
                size=self.size,
                stdin=io.BytesIO(self._send()),
                stdout=stdout,
            )
        self.assertEqual(stdout.getvalue(), b"")
        self.assertEqual(_leftover_temps(real), [])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux setgid inheritance")
    def test_native_group_inheritance_is_retained(self):
        os.chmod(self.dest_root, 0o2750)
        self._receive(self._send())
        for relative in ("objects", os.path.join("objects", self.sha[:2])):
            mode = os.stat(os.path.join(self.dest_root, relative)).st_mode
            self.assertTrue(mode & stat.S_ISGID)
            self.assertEqual(stat.S_IMODE(mode) & 0o777, 0o750)

    def test_cli_receive_rejects_wrong_uid(self):
        if os.getuid() == stream_object.RECEIVE_UID and os.getgid() == stream_object.RECEIVE_GID:
            self.skipTest("running as service uid/gid 10001")
        rc = stream_object.main(
            [
                "receive",
                "--destination-root",
                self.dest_root,
                "--sha256",
                self.sha,
                "--bytes",
                str(self.size),
            ]
        )
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.lexists(self._object_path()))


if __name__ == "__main__":
    unittest.main()
