import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ingest
import lmstudio


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

    def test_missing_catalog_or_sharing_skip_prevents_cleanup(self):
        item, _ = self.convert()
        with patch.object(ingest, "docker", return_value=b'{"repos": []}') as docker:
            with self.assertRaisesRegex(RuntimeError, "missing from the native catalog"):
                ingest.finish_lmstudio(item, {"share_job": {}})
            self.assertEqual(docker.call_count, 1)
        with patch.object(ingest, "docker") as docker:
            with self.assertRaisesRegex(RuntimeError, "sharing skips"):
                ingest.finish_lmstudio(item, {"share_job": {"job": {"notes": ["repo: kept abc"]}}})
            docker.assert_not_called()

    def test_cleanup_is_idempotent_when_generic_path_is_absent(self):
        item, _ = self.convert()
        with patch.object(ingest, "docker", side_effect=[b'{"repos":[{"repo":"owner/model"}]}', b'false']) as docker:
            result = ingest.finish_lmstudio(item, {"share_job": {}})
            self.assertTrue(result["absent"])
            self.assertEqual(docker.call_count, 2)

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


class DirectTransfer(unittest.TestCase):
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
