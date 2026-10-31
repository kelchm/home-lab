#!/usr/bin/env python3
"""Small offline smoke tests for the local DSM bridge; no NAS or ACME requests."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("deploy_athena_dsm", Path(__file__).parents[1] / "synology/deploy-athena-dsm.py")
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


class ImportHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "issued").mkdir()
        for name in ("key.pem", "cert.pem", "chain.pem"):
            (self.root / "issued" / name).write_text("synthetic " + name)
        self.certs = [
            {"id": hook.CERT_ID, "desc": hook.DESCRIPTION, "is_default": True, "is_broken": False,
             "services": [{"owner": "root", "subscriber": "system", "service": "default"}]},
            {"id": "unrelated", "desc": "another certificate", "is_default": False, "is_broken": True,
             "services": []},
        ]
        self.addCleanup(patch.stopall)
        patch.object(hook, "ROOT", self.root).start()
        patch.object(hook.os, "geteuid", return_value=0).start()

    def test_same_id_import_uses_private_copies_and_preserves_source_files(self):
        calls = []

        def command(args, **kwargs):
            calls.append(args)
            if args[0] != hook.API:
                return subprocess.CompletedProcess(args, 0, b"OK")
            if "method=list" in args:
                data = {"certificates": self.certs}
            else:
                params = {key: json.loads(value) for key, value in
                          (arg.split("=", 1) for arg in args[5:])}
                self.assertEqual(params["id"], hook.CERT_ID)
                self.assertEqual(params["as_default"], "true")
                for field in ("key_tmp", "cert_tmp", "inter_cert_tmp"):
                    staged = Path(params[field])
                    self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(staged.read_text(), "synthetic " + staged.name)
                    staged.unlink()  # Model DSM consuming the supplied files.
                data = {"id": hook.CERT_ID}
            return subprocess.CompletedProcess(args, 0, json.dumps({"success": True, "data": data}).encode())

        with patch.object(hook.subprocess, "run", side_effect=command):
            hook.deploy()
        self.assertEqual(sum("method=import" in args for args in calls), 1)
        self.assertEqual({p.name for p in (self.root / "issued").iterdir()}, {"key.pem", "cert.pem", "chain.pem"})
        self.assertEqual(list(self.root.glob("dsm-import-*")), [])

    def test_wrong_target_or_non_root_never_imports(self):
        for field, value in (("id", "other"), ("desc", "other"), ("is_default", False),
                             ("is_broken", True), ("services", [])):
            certs = copy.deepcopy(self.certs)
            certs[0][field] = value
            with self.subTest(field=field), patch.object(hook, "dsm", return_value={"certificates": certs}) as api, \
                    patch.object(hook.subprocess, "run"), self.assertRaises(RuntimeError):
                hook.deploy()
            api.assert_called_once_with("list")
        with patch.object(hook.os, "geteuid", return_value=1000), patch.object(hook, "dsm") as api, \
                self.assertRaises(RuntimeError):
            hook.deploy()
        api.assert_not_called()

    def test_api_errors_remain_failures(self):
        for failure in (False, "command"):
            with self.subTest(failure=failure):
                result = subprocess.CompletedProcess([], 0, b'{"success":false}')
                with patch.object(hook.subprocess, "run", return_value=result,
                                  side_effect=subprocess.CalledProcessError(1, [hook.API]) if failure == "command" else None), \
                        self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                    hook.dsm("import", id=hook.CERT_ID)


if __name__ == "__main__":
    unittest.main()
