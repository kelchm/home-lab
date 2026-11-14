"""Regression checks for the opt-in HF helper patch (requires pinned HF SDKs)."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HELPER = os.environ.get("SPARKNEST_HELPER", "/usr/local/libexec/sparknest/sparknest-hf-fetch")
loader = importlib.machinery.SourceFileLoader("hf_fetch", HELPER)
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class OfflineShare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = os.environ.get("SPARKNEST_REAL_MODEL_FIXTURE")
        if not fixture:
            raise RuntimeError("Set SPARKNEST_REAL_MODEL_FIXTURE to the actual SNAC pytorch_model.bin")
        cls.data = Path(fixture).read_bytes()
        cls.sha = hashlib.sha256(cls.data).hexdigest()
        if cls.sha != "4b8164cc6606bfa627f1a784734c1e539891518f1191ed9194fe1e3b9b4bff40":
            raise RuntimeError("The real-model fixture checksum differs")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, name, payload=None, blob_name=None):
        repo = self.hub / ("models--qualification--" + name)
        blob = repo / "blobs" / (blob_name or self.sha)
        blob.parent.mkdir(parents=True)
        blob.write_bytes(self.data if payload is None else payload)
        snapshot = repo / "snapshots" / hashlib.sha1(name.encode()).hexdigest()
        snapshot.mkdir(parents=True)
        (snapshot / "model.bin").symlink_to("../../blobs/" + blob.name)
        (repo / "refs").mkdir()
        (repo / "refs/main").write_text(snapshot.name)
        return repo, blob, snapshot / "model.bin"

    def share(self, repo, **overrides):
        from argparse import Namespace
        opts = dict(repo=repo.name, cache_dir=str(self.hub), offline=True,
                    dry_run=False, verify=True, hash_missing=True)
        opts.update(overrides)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            helper.share(Namespace(**opts))
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_offline_two_repos_share_one_payload_and_delete_safely(self):
        a, ba, sa = self.repo("a")
        b, bb, sb = self.repo("b")
        first, second = self.share(a), self.share(b)
        self.assertEqual(first[-1]["moved"], 1)
        self.assertEqual(second[-1]["linked"], 1)
        self.assertEqual(second[-1]["freed"], len(self.data))
        self.assertEqual(first[0]["xet_source"], "local")
        self.assertTrue(ba.is_symlink() and bb.is_symlink())
        self.assertFalse(os.path.isabs(os.readlink(ba)))
        self.assertEqual(ba.resolve(), bb.resolve())
        store = ba.resolve()
        refs = store.with_name(store.name + ".refs").read_text()
        self.assertIn(a.name, refs)
        self.assertIn(b.name, refs)
        from huggingface_hub import hf_hub_download, scan_cache_dir
        with patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}):
            local = hf_hub_download("qualification/b", "model.bin", cache_dir=str(self.hub), local_files_only=True)
        self.assertEqual(Path(local).read_bytes(), self.data)
        scan_cache_dir(self.hub).delete_revisions(sa.parent.name).execute()
        self.assertTrue(store.is_file())
        self.assertEqual(sb.read_bytes(), self.data)
        scan_cache_dir(self.hub).delete_revisions(sb.parent.name).execute()
        self.assertFalse(store.exists())

    def test_known_metadata_wins(self):
        repo, blob, _ = self.repo("known")
        tree = repo / "trees/commit.json"
        tree.parent.mkdir()
        known = "a1" * 32
        tree.write_text(json.dumps({"format_version": 1, "files": {
            "model.bin": {"size": len(self.data), "lfs_sha256": self.sha, "xet_hash": known}}}))
        with patch("hf_xet.hash_files", side_effect=AssertionError("metadata must take precedence")):
            result = self.share(repo)
        self.assertEqual(result[-1]["moved"], 1)
        self.assertEqual(blob.resolve().name, known)

    def test_bad_sha_and_git_blob_are_not_published(self):
        bad, blob, _ = self.repo("bad", payload=self.data[:-7])
        result = self.share(bad)
        self.assertEqual(result[-1]["skipped"], 1)
        self.assertFalse(blob.is_symlink())
        git, blob, _ = self.repo("git", blob_name="b2" * 20)
        result = self.share(git)
        self.assertFalse(blob.is_symlink())
        self.assertEqual(result[-1]["moved"], 0)

    def test_corrupt_existing_store_is_not_reused(self):
        a, ba, _ = self.repo("a")
        self.share(a)
        store = ba.resolve()
        # Same size corruption is invisible to the SDK's size-only guard.
        store.chmod(0o600)
        store.write_bytes(bytes([self.data[0] ^ 1]) + self.data[1:])
        b, bb, sb = self.repo("b")
        result = self.share(b)
        self.assertEqual(result[-1]["skipped"], 1)
        self.assertFalse(bb.is_symlink())
        self.assertEqual(sb.read_bytes(), self.data)

    def test_hashing_requires_explicit_verification(self):
        repo, _, _ = self.repo("a")
        result = subprocess.run([sys.executable, HELPER, "share", repo.name, "--cache-dir", str(self.hub),
                                 "--offline", "--hash-missing"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--verify", result.stderr)

    def test_local_hash_reuses_an_existing_shared_id(self):
        a, ba, _ = self.repo("known")
        tree = a / "trees/commit.json"
        tree.parent.mkdir()
        old_id = "a1" * 32
        tree.write_text(json.dumps({"format_version": 1, "files": {
            "model.bin": {"size": len(self.data), "lfs_sha256": self.sha, "xet_hash": old_id}}}))
        self.share(a)
        b, bb, _ = self.repo("missing")
        with patch("hf_xet.hash_files", side_effect=AssertionError("reuse the existing shared ID")):
            result = self.share(b)
        self.assertEqual(result[-1]["linked"], 1)
        self.assertEqual(bb.resolve(), ba.resolve())

    def test_runtime_opt_in_only_affects_verified_jobs(self):
        repo, blob, _ = self.repo("env")
        with patch.dict(os.environ, {"SPARKNEST_HF_HASH_MISSING": "1"}):
            result = self.share(repo, hash_missing=False, verify=False)
            self.assertFalse(blob.is_symlink())
            self.assertEqual(result[-1]["skipped"], 1)
            result = self.share(repo, hash_missing=False, verify=True)
            self.assertTrue(blob.is_symlink())
            self.assertEqual(result[-1]["moved"], 1)

    def test_changed_source_is_not_published(self):
        repo, blob, _ = self.repo("changed")
        from hf_xet import hash_files
        def changing(paths):
            result = hash_files(paths)
            blob.write_bytes(bytes([self.data[0] ^ 1]) + self.data[1:])
            return result
        with patch("hf_xet.hash_files", side_effect=changing):
            result = self.share(repo)
        self.assertFalse(blob.is_symlink())
        self.assertEqual(result[-1]["skipped"], 1)
        self.assertIn("changed", result[0]["why"])

    def test_unchanged_without_opt_in(self):
        repo, blob, _ = self.repo("default")
        with patch("hf_xet.hash_files", side_effect=AssertionError("opt-in only")):
            result = self.share(repo, hash_missing=False)
        self.assertEqual(result[-1]["skipped"], 1)
        self.assertFalse(blob.is_symlink())


class ChunkerGolden(unittest.TestCase):
    def test_pinned_chunker_golden(self):
        from hf_xet import hash_files
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deterministic-chunking-fixture"
            path.write_bytes(b"".join(hashlib.sha256(n.to_bytes(4, "big")).digest() for n in range(65536)))
            self.assertEqual(hash_files([str(path)])[0].hash, "a8ee5859cc2afcdb0b2c92cd4d9a24832d47a2472e15ade6573456a2775cc69f")


if __name__ == "__main__":
    unittest.main()
