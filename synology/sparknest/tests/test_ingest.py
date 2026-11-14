import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ingest
import lmstudio
import progress


class NativeModelImport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "config.json"
        self.source.write_text('{"architecture":"example"}')
        self.sha, self.git = ingest.digest_file(self.source, with_git=True)
        self.item = {"key": "original", "source": "mac", "host": "local", "store": "lmstudio",
                     "kind": "tree", "name": "owner/model", "files": [ingest.file_record(self.source, "config.json")]}
        self.metadata = {"repo": "owner/model", "revision": "1" * 40,
                         "files": [{"type": "file", "path": "config.json", "size": self.source.stat().st_size,
                                    "oid": self.git}]}

    def convert(self, receipts=None):
        return lmstudio.convert(self.item, self.metadata, receipts or {}, self.root)

    def test_real_revision_and_file_identity_are_required(self):
        item, _ = self.convert()
        self.assertEqual(item["kind"], "hf")
        self.assertEqual(item["files"][0]["etag"], self.git)
        self.assertNotEqual(item["key"], self.item["key"])
        self.assertEqual(item["legacy_import_name"], "owner/model")
        self.metadata["files"][0]["oid"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "no unique published match"):
            self.convert()

    def test_renamed_metadata_maps_by_content(self):
        self.metadata["files"][0]["path"] = "assets/config.json"
        item, _ = self.convert()
        self.assertEqual(item["files"][0]["rel"], "assets/config.json")
        self.assertEqual(item["provenance"]["mappings"][0]["local"], "config.json")

    def test_stale_source_and_conflicting_receipt_are_rejected(self):
        fid = lmstudio.file_id(self.item, self.item["files"][0])
        with self.assertRaisesRegex(ValueError, "source/receipt mismatch"):
            self.convert({fid: {"sha256": "f" * 64}})
        self.source.write_text("changed")
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.convert()

    def test_git_receipt_repair_is_bound_to_sha256(self):
        fid = lmstudio.file_id(self.item, self.item["files"][0])
        _, proofs = self.convert({fid: {"sha256": self.sha, "git_sha1": None}})
        self.assertEqual(proofs[fid], {"sha256": self.sha, "git_sha1": self.git})

    def test_conversion_identity_is_stable_on_retry(self):
        first, _ = self.convert()
        second, _ = self.convert()
        self.assertEqual(first["key"], second["key"])

    def catalog(self, item, branch=None, missing=0):
        branch = branch or ingest.source_ref(item, "imported")
        return json.dumps({"repos": [{"repo": "owner/model", "branches": ["main", branch],
                                      "hosts": [{"host": "athena", "missing_files": missing}]}]}).encode()

    def test_missing_catalog_or_sharing_skip_prevents_cleanup(self):
        item, _ = self.convert()
        with patch.object(ingest, "docker", return_value=b'{"repos": []}') as docker:
            with self.assertRaisesRegex(RuntimeError, "missing or incomplete in the native catalog"):
                ingest.finish_lmstudio(item, {"share_job": {}}, "m")
            self.assertEqual(docker.call_count, 1)
        with patch.object(ingest, "docker") as docker:
            with self.assertRaisesRegex(RuntimeError, "sharing skips"):
                ingest.finish_lmstudio(item, {"share_job": {"job": {"notes": ["repo: kept abc"]}}}, "m")
            docker.assert_not_called()

    def test_cleanup_requires_the_converted_revision_complete_on_athena(self):
        item, _ = self.convert()
        for catalog in (self.catalog(item, branch="sources/mac/lmstudio/other/imported"), self.catalog(item, missing=1)):
            with patch.object(ingest, "docker", return_value=catalog):
                with self.assertRaisesRegex(RuntimeError, "missing or incomplete"):
                    ingest.finish_lmstudio(item, {"share_job": {}}, "m")

    def test_cleanup_is_idempotent_when_generic_path_is_absent(self):
        item, _ = self.convert()
        with patch.object(ingest, "docker", side_effect=[self.catalog(item), b'false']) as docker:
            result = ingest.finish_lmstudio(item, {"share_job": {}}, "m")
            self.assertTrue(result["absent"])
            self.assertEqual(docker.call_count, 2)

    def test_unmatched_or_unshared_legacy_content_is_kept(self):
        item, _ = self.convert()
        for check in ({"unshared": ["a" * 64], "unmatched": []}, {"unshared": [], "unmatched": ["/x/extra.bin"]}):
            with patch.object(ingest, "docker", side_effect=[self.catalog(item), b'true', json.dumps(check).encode()]) as docker:
                with self.assertRaisesRegex(RuntimeError, "legacy import kept"):
                    ingest.finish_lmstudio(item, {"share_job": {}}, "m")
                self.assertFalse(any(call.args[:3] == ("nest", "--json", "rm") for call in docker.call_args_list))

    def test_acceptance_checks_legacy_files_and_shared_links(self):
        with tempfile.TemporaryDirectory() as folder:
            mnt = Path(folder)
            hub = mnt / "hf-home/hub"
            payload = hub / "blobs/ab/" / ("ab" * 32)
            payload.parent.mkdir(parents=True)
            payload.write_text("weights")
            repo = hub / "models--owner--model/blobs"
            repo.mkdir(parents=True)
            (repo / ("1" * 64)).symlink_to(os.path.relpath(payload, repo))
            (repo / ("2" * 64)).write_text("weights")
            legacy = mnt / "imports/mac/lmstudio/owner/model"
            legacy.mkdir(parents=True)
            (legacy / "model.gguf").write_text("weights")
            (legacy / "extra.txt").write_text("unmatched")
            manifest = mnt / "manifest.json"
            sha, _ = ingest.digest_file(legacy / "model.gguf")
            manifest.write_text(json.dumps([{"sha256": sha, "bytes": 7}]))
            original = Path
            with patch.object(ingest, "HUB", hub), patch.object(ingest, "Path",
                    side_effect=lambda p, *a: mnt if p == "/mnt/sparknest" else original(p, *a)):
                result = ingest.lmstudio_acceptance({"name": "models--owner--model", "lfs": ["1" * 64, "2" * 64],
                                                     "legacy": "/imports/mac/lmstudio/owner/model", "manifest": str(manifest)})
            self.assertEqual(result["unshared"], ["2" * 64])
            self.assertEqual([Path(p).name for p in result["unmatched"]], ["extra.txt"])
            self.assertEqual(result["legacy_files"], 2)

    def test_sha_reuse_selects_receipt_with_required_git_identity(self):
        rec = dict(self.item["files"][0], sha256=self.sha, etag=self.git)
        missing = {"bytes": rec["identity"][0], "sha256": self.sha, "git_sha1": None}
        valid = dict(missing, git_sha1=self.git)
        self.assertIsNone(ingest.reusable_copy(rec, [missing]))
        self.assertEqual(ingest.reusable_copy(rec, [missing, valid]), valid)

    def test_converted_source_ref_is_not_published_as_upstream_branch(self):
        item, _ = self.convert()
        inventory = ingest.annotate_inventory({"items": [item]})
        self.assertEqual(inventory["items"][0]["shared_refs"], {})


class Inventory(unittest.TestCase):
    def test_linked_repo_is_skipped_and_external_orphan_link_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            root = p / "hub"
            (root / "blobs/ab").mkdir(parents=True)
            shared = root / "blobs/ab/payload"
            shared.write_text("{}")
            outside = p / "outside"
            (outside / "blobs").mkdir(parents=True)
            (outside / "blobs/config").write_text("{}")
            (root / "models--unit--linked").symlink_to(outside, target_is_directory=True)
            repo = root / "models--unit--cache/blobs"
            repo.mkdir(parents=True)
            (repo / "etag").symlink_to(shared)
            external = p / "external"
            external.write_text("{}")
            (repo / "external-etag").symlink_to(external)
            result = ingest.inventory({"name": "mac", "host": "local", "stores": [{"name": "hf", "root": str(root), "kind": "hf"}]})
            sources = [f["src"] for i in result["items"] for f in i["files"]]
            self.assertNotIn(str((outside / "blobs/config").resolve()), sources)
            self.assertEqual(sources.count(str(shared.resolve())), 1)
            self.assertEqual(sources.count(str(external.resolve())), 1)

    def test_incomplete_refs_do_not_count_as_agreement(self):
        a = {"kind": "hf", "name": "models--x--y", "files": [], "refs": {"main": "a" * 40}}
        b = dict(a, refs={}, refs_complete=False)
        self.assertEqual(ingest.annotate_inventory({"items": [a, b]})["items"][0]["shared_refs"], {})
        self.assertEqual(ingest.annotate_inventory({"items": [dict(a)]})["items"][0]["shared_refs"], {"main": "a" * 40})


class Restart(unittest.TestCase):
    def test_waits_only_for_unfinished_jobs_using_this_staging(self):
        jobs = {"jobs": [
            {"id": 1, "finished": False, "what": "import /data/incoming/bulk/key1/hub -> /hf-home/hub"},
            {"id": 2, "finished": True, "what": "import /data/incoming/bulk/key1/hub -> /hf-home/hub"},
            {"id": 3, "finished": False, "what": "import /data/incoming/bulk/key10/hub -> /hf-home/hub"}]}
        with patch.object(ingest, "docker", side_effect=[json.dumps(jobs).encode(), b"{}"]) as docker:
            self.assertEqual(ingest.wait_for_staging_jobs("key1"), [1])
        self.assertEqual(docker.call_args_list[1].args, ("nest", "--json", "jobs", "1", "--wait"))


class Monitor(unittest.TestCase):
    def test_empty_error_missing_journal_and_reused_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / "inventory.json").write_text(json.dumps({"items": [{"key": "x", "name": "unit/model"}]}))
            self.assertIn("waiting", progress.display(p))
            (p / "journal.json").write_text(json.dumps({"items": {"x": {"state": "failed", "name": "unit/model",
                                                                         "source": "mac", "store": "hf", "error": ""}}}))
            (p / "worker.json").write_text(json.dumps({"pid": os.getpid(), "process_start": "earlier process"}))
            output = progress.display(p)
            self.assertIn("unknown error", output)
            self.assertIn("Worker process: not found", output)
            (p / "worker.json").write_text(json.dumps({"pid": os.getpid(), "process_start": ingest.process_start(os.getpid())}))
            self.assertIn("Worker process: running", progress.display(p))


class DirectTransfer(unittest.TestCase):
    class Source:
        def __init__(self, data, returncode=0):
            self.stdout, self.stderr = io.BytesIO(data), io.BytesIO(b"ssh: connect failed")
            # A failed SSH client has already exited; a healthy one is still streaming.
            self.final, self.returncode, self.terminated = returncode, returncode or None, False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated, self.final = True, -15

        def wait(self):
            self.returncode = self.final
            return self.returncode

        def communicate(self):
            raise AssertionError("must not buffer the stream")

    def direct(self, source, receive):
        with patch.object(ingest, "DIRECT_CONFIG") as cfg, patch.object(ingest.subprocess, "Popen", return_value=source), \
                patch.object(ingest, "receive", side_effect=receive):
            cfg.read_text.return_value = json.dumps({"spark": {"host": "h", "known_hosts": "k", "identity": "i"}})
            return ingest.receive_direct({"source_host": "spark", "file": {}})

    def test_receipt_found_mid_transfer_stops_source_without_buffering(self):
        source = self.Source(b"x" * 1000)
        result = self.direct(source, [None, {"bytes": 1, "path": "already"}])
        self.assertTrue(source.terminated)
        self.assertEqual(result["path"], "already")

    def test_existing_receipt_skips_the_source(self):
        with patch.object(ingest.subprocess, "Popen") as popen, patch.object(ingest, "receive", return_value={"path": "cached"}):
            self.assertEqual(ingest.receive_direct({"source_host": "spark", "file": {}})["path"], "cached")
            popen.assert_not_called()

    def test_source_ssh_failure_is_a_transport_failure(self):
        source = self.Source(b"", returncode=255)
        with self.assertRaises(ConnectionError):
            self.direct(source, [None, ValueError("transfer ended early")])
        self.assertTrue(ingest.transport_failure(subprocess.CalledProcessError(ingest.TRANSPORT_EXIT, "agent")))


    def test_forced_command_rejects_uninventoried_file(self):
        with tempfile.TemporaryDirectory() as folder:
            allow = Path(folder) / "allow.json"
            allow.write_text(json.dumps({"/allowed": [[1, 2, 3, 4]]}))
            request = {"file": {"src": "/other", "identity": [1, 2, 3, 4]}}
            with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": ingest.encoded(request)}):
                with patch.object(ingest, "send") as send:
                    with self.assertRaisesRegex(ValueError, "outside this transfer"):
                        ingest.send_authorized(allow)
                    send.assert_not_called()

    def test_main_vlan_uses_existing_host_key_pin(self):
        args = ingest.remote_command("kelchm@10.32.10.5", ["true"])
        self.assertIn("HostKeyAlias=10.32.20.5", args)


if __name__ == "__main__":
    unittest.main()
