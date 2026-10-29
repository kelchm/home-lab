#!/usr/bin/env python3
"""Offline contract tests: no credentials, DSM, Cloudflare, or ACME traffic."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("athena_acme", Path(__file__).parents[1] / "synology/athena-acme.py")
acme = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acme)

SYSTEM = {"owner": "root", "subscriber": "system", "service": "default",
          "multiple_cert": True, "user_setable": True}
BACKUP = {"owner": "root", "subscriber": "HyperBackupVault", "service": "HyperBackupVault"}
S3 = {"owner": "root", "subscriber": "WebStation", "service": "s3"}
OLD = "a" * 64
NEW = "b" * 64
LEAF = b"-----BEGIN CERTIFICATE-----\nbGVhZg==\n-----END CERTIFICATE-----\n"
CHAIN = b"-----BEGIN CERTIFICATE-----\nY2hhaW4=\n-----END CERTIFICATE-----\n"


def baseline():
    return {
        acme.ORIGINAL_ID: {"desc": "", "is_default": True, "services": sorted([SYSTEM, BACKUP], key=acme.canonical)},
        "S3existing": {"desc": "Existing S3 certificate", "is_default": False, "services": [copy.deepcopy(S3)]},
    }


def accepted_map():
    result = baseline()
    result[acme.ORIGINAL_ID]["is_default"] = False
    result[acme.ORIGINAL_ID]["services"] = [copy.deepcopy(BACKUP)]
    result["newDSM"] = {"desc": acme.DESCRIPTION, "is_default": True, "services": [copy.deepcopy(SYSTEM)]}
    return result


def list_response(mapping):
    return {"success": True, "data": {"certificates": [dict(value, id=key, is_broken=False)
                                                       for key, value in mapping.items()]}}


class PureContracts(unittest.TestCase):
    def test_api_preamble_and_strict_success(self):
        self.assertTrue(acme.parse_api(b'[Line 295] Exec WebAPI: api=SYNO.Core.Certificate.CRT, param={}, runner=SYSTEM_ADMIN\n{"success":true,"data":{}}')["success"])
        for output in (b"not JSON", b'{"success":false}', b'{"success":1}', b'{"success":"true"}',
                       b'{"success":true}\ntrailing junk', b'{"success":true}{"success":true}'):
            with self.subTest(output=output), self.assertRaises(acme.Failure):
                acme.parse_api(output)

    def test_full_assignment_schema_is_retained(self):
        self.assertEqual(acme.certificate_map(list_response(baseline())), baseline())
        bad = list_response(baseline())
        bad["data"]["certificates"][1]["services"].append(copy.deepcopy(SYSTEM))
        with self.assertRaisesRegex(acme.Failure, "duplicate service"):
            acme.certificate_map(bad)

    def test_duplicate_name_and_original_id_refused(self):
        current = accepted_map()
        current[acme.ORIGINAL_ID]["desc"] = acme.DESCRIPTION
        with self.assertRaises(acme.Failure):
            acme.named_certificate(current)
        del current["newDSM"]
        with self.assertRaisesRegex(acme.Failure, "original"):
            acme.named_certificate(current)

    def test_accept_allows_only_default_and_system_service_move(self):
        acme.accepted_transition(baseline(), accepted_map(), "newDSM")
        for change in ("s3", "backup", "metadata", "original"):
            current = accepted_map()
            if change == "s3":
                current["newDSM"]["services"].append(current["S3existing"]["services"].pop())
            elif change == "backup":
                current["newDSM"]["services"].append(current[acme.ORIGINAL_ID]["services"].pop())
            elif change == "metadata":
                current["newDSM"]["services"][0]["multiple_cert"] = False
            else:
                del current[acme.ORIGINAL_ID]
            with self.subTest(change=change), self.assertRaises(acme.Failure):
                acme.accepted_transition(baseline(), current, "newDSM")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=str(Path(__file__).parents[2]))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.runner = acme.Runner(self.root)
        # Exercise real ownership/mode/symlink guards on non-root CI machines by
        # modeling root ownership only. Permission/type/inode checks stay real.
        original_lstat = Path.lstat

        def root_lstat(path):
            info = original_lstat(path)
            values = list(info)
            values[4] = 0
            if path not in (self.root,) and path in self.root.parents:
                values[0] &= ~0o022
            return os.stat_result(values)

        self.addCleanup(patch.stopall)
        patch.object(Path, "lstat", root_lstat).start()
        patch.object(acme.os, "geteuid", return_value=0).start()
        self.command = patch.object(self.runner, "command", side_effect=AssertionError("Unmocked external command")).start()
        for name in ("client/dnsapi", "state", "certs", "issued", "bundles"):
            self.root.joinpath(name).mkdir(mode=0o700, parents=True, exist_ok=True)
        # parents=True uses the process umask for intermediate directories.
        self.root.joinpath("client").chmod(0o700)
        self.put("client/acme.sh", b"test core\n")
        self.put("client/dnsapi/dns_cf.sh", b"test dns plugin\n")
        hashes = {name: hashlib.sha256(self.root.joinpath("client", name).read_bytes()).hexdigest()
                  for name in ("acme.sh", "dnsapi/dns_cf.sh")}
        patch.object(acme, "SOURCE_HASHES", hashes).start()
        self.put("cloudflare-token", b"fake_credential_for_offline_tests_only")
        for name, data in (("cert.pem", LEAF), ("key.pem", b"private test key"),
                           ("chain.pem", CHAIN), ("fullchain.pem", LEAF + CHAIN)):
            self.put("issued/" + name, data)

    def put(self, name, data):
        target = self.root / name
        target.write_bytes(data)
        target.chmod(0o600)
        return target

    def save(self, name, data):
        self.put(name, (acme.canonical(data) + "\n").encode())

    def accepted(self):
        self.save("accepted.json", {"cert_id": "newDSM", "mapping": accepted_map()})
        self.save("good.json", {"sha256": OLD})
        self.save("last-verified-success.json", {"sha256": OLD, "at": int(time.time())})

    def mock_validation(self, san=None, key=b"same public key"):
        def output(argv):
            if "-pubkey" in argv:
                return b"same public key"
            if "pkey" in argv:
                return key
            if "-ext" in argv:
                return b"X509v3 Subject Alternative Name:\n    " + (san or ("DNS:" + acme.HOST).encode()) + b"\n"
            return b"OK\n"
        return patch.object(self.runner, "checked", side_effect=output)

    def test_runtime_guards_modes_source_hashes_extra_hooks_and_symlinks(self):
        self.runner.audit()
        token = self.root / "cloudflare-token"
        token.chmod(0o644)
        with self.assertRaisesRegex(acme.Failure, "0700 / file 0600"):
            self.runner.token()
        token.chmod(0o600)
        self.put("client/acme.sh", b"changed core")
        with self.assertRaisesRegex(acme.Failure, "hash"):
            self.runner.audit()
        self.put("client/acme.sh", b"test core\n")
        self.put("client/deploy-hook.sh", b"unexpected")
        with self.assertRaisesRegex(acme.Failure, "Only the pinned"):
            self.runner.audit()
        (self.root / "client/deploy-hook.sh").unlink()
        (self.root / "state/redirect").symlink_to(self.root / "issued")
        with self.assertRaises(acme.Failure):
            self.runner.audit()
        self.command.assert_not_called()

    def test_non_root_file_owner_refused(self):
        info = (self.root / "cloudflare-token").lstat()
        values = list(info)
        values[4] = 1001
        with patch.object(Path, "lstat", return_value=os.stat_result(values)), self.assertRaises(acme.Failure):
            self.runner.token()

    def test_writable_runtime_ancestor_refused(self):
        normal_lstat = Path.lstat
        def unsafe_parent(path):
            info = normal_lstat(path)
            if path == self.root.parent:
                values = list(info)
                values[0] |= 0o002
                return os.stat_result(values)
            return info
        with patch.object(Path, "lstat", unsafe_parent), self.assertRaisesRegex(acme.Failure, "ancestors"):
            self.runner.audit()
        self.command.assert_not_called()

    def test_mutations_share_one_nonblocking_native_lock(self):
        normal_fstat = os.fstat
        def root_fstat(descriptor):
            values = list(normal_fstat(descriptor))
            values[4] = 0
            return os.stat_result(values)
        with patch.object(acme.os, "fstat", side_effect=root_fstat):
            with self.runner.lock():
                with self.assertRaisesRegex(acme.Failure, "Another Athena"):
                    with self.runner.lock():
                        self.fail("Second mutation acquired the same lock")
            with self.runner.lock():
                pass
        self.assertEqual((self.root / "lock").stat().st_mode & 0o777, 0o600)

    def test_configuration_hooks_upgrade_and_extra_sans_refused(self):
        for setting in ("Le_ReloadCmd='secret hook'", "Le_DeployHook='synology_dsm'", "NOTIFY_HOOK='mail'",
                        "AUTO_UPGRADE='1'", "Le_Alt='s3.home.kelch.io'", "Le_Webroot='dns_other'"):
            self.put("state/account.conf", (setting + "\n").encode())
            with self.subTest(setting=setting), self.assertRaises(acme.Failure):
                self.runner.audit()
        self.put("state/account.conf", b"AUTO_UPGRADE='0'\nLe_ReloadCmd=''\n")
        self.runner.audit()
        self.command.assert_not_called()

    def test_api_exit_zero_does_not_override_failure_and_arguments_are_json(self):
        self.command.side_effect = None
        self.command.return_value = (0, b'prefix\n{"success":false,"error":{"code":105}}')
        with self.assertRaises(acme.Failure):
            self.runner.api("import", {"id": "newDSM", "as_default": "true"})
        args = self.command.call_args.args[0]
        self.assertIn('id="newDSM"', args)
        self.assertIn('as_default="true"', args)
        self.assertEqual(args[0], acme.SYNOWEBAPI)
        self.assertIn(b"\"success\":false", self.runner.read(self.root / "last-error.log"))
        self.assertEqual((self.root / "last-error.log").stat().st_mode & 0o777, 0o600)
        self.assertNotIn("shell", self.command.call_args.kwargs)

    def test_wrong_key_and_nonexact_sans_are_refused(self):
        bundle = self.runner.bundle("issued")
        with self.mock_validation(key=b"different key"), self.assertRaisesRegex(acme.Failure, "private key"):
            self.runner.validate(bundle)
        for san in (b"DNS:*.home.kelch.io", ("DNS:" + acme.HOST + ", DNS:s3.home.kelch.io").encode()):
            with self.subTest(san=san), self.mock_validation(san=san), self.assertRaisesRegex(acme.Failure, "exactly"):
                self.runner.validate(bundle)
        with self.mock_validation():
            self.assertEqual(self.runner.validate(bundle), hashlib.sha256(b"leaf").hexdigest())

    def test_normal_chain_and_21_day_check_are_required(self):
        with self.mock_validation():
            self.runner.validate(self.runner.bundle("issued"))
            calls = self.runner.checked.call_args_list
            self.assertTrue(any("-verify_hostname" in item.args[0] and "-untrusted" in item.args[0] for item in calls))
            self.assertTrue(any(str(21 * 86400) in item.args[0] and "-checkend" in item.args[0] for item in calls))

    def test_not_due_still_installs_and_never_forces_renewal(self):
        self.command.side_effect = [(2, b"not due"), (0, b"installed")]
        with patch.object(self.runner, "audit"):
            self.runner.acme()
        renew, install = [call.args[0] for call in self.command.call_args_list]
        self.assertIn("--renew", renew)
        self.assertIn("--install-cert", install)
        self.assertNotIn("--force", renew)
        self.assertNotIn("--reloadcmd", install)
        self.assertNotIn("--deploy-hook", install)
        self.assertFalse(any("fake_credential" in arg for arg in renew + install))
        self.assertEqual(self.command.call_args_list[0].kwargs["env"], {"CF_Token": "fake_credential_for_offline_tests_only"})

    def test_issue_once_then_uses_renew(self):
        self.command.side_effect = [(0, b"issued"), (0, b"installed"), (2, b"not due"), (0, b"installed")]
        with patch.object(self.runner, "audit"):
            self.runner.acme(initial=True)
            folder = self.root / "certs" / (acme.HOST + "_ecc")
            folder.mkdir(mode=0o700)
            self.put(str(folder.relative_to(self.root) / (acme.HOST + ".conf")), b"Le_Domain='" + acme.HOST.encode() + b"'\n")
            self.runner.acme(initial=True)
        self.assertIn("--issue", self.command.call_args_list[0].args[0])
        self.assertIn("--renew", self.command.call_args_list[2].args[0])

    def test_staging_preserves_originals_and_never_targets_ALK0dn(self):
        def import_api(method, params):
            self.assertEqual(method, "import")
            self.assertNotIn("id", params)
            self.assertEqual(params["as_default"], "false")
            for field in ("key_tmp", "cert_tmp", "inter_cert_tmp"):
                staged = Path(params[field])
                self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
                self.assertNotEqual(staged.parent, self.root / "issued")
                staged.unlink()  # Model the native API moving its input files.
            return {"success": True, "data": {"id": "newDSM"}}
        with patch.object(self.runner, "api", side_effect=import_api):
            self.runner.import_bundle(self.runner.bundle("issued"))
        self.assertTrue((self.root / "issued/key.pem").exists())
        with self.assertRaisesRegex(acme.Failure, "original"):
            self.runner.import_bundle(self.runner.bundle("issued"), acme.ORIGINAL_ID)

    def test_initial_import_preserves_snapshot_and_requires_returned_id(self):
        current = baseline()
        added = dict(current, newDSM={"desc": acme.DESCRIPTION, "is_default": False, "services": []})
        with patch.object(self.runner, "snapshot", side_effect=[current, added]), \
                patch.object(self.runner, "validate", return_value=NEW), \
                patch.object(self.runner, "import_bundle", return_value={"success": True, "data": {"id": "newDSM"}}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.runner.import_new()
        self.assertEqual(self.runner.load("baseline.json")["mapping"], baseline())
        self.assertEqual(self.runner.load("candidate.json")["cert_id"], "newDSM")

    def test_recover_initial_success_without_creating_duplicate(self):
        self.save("baseline.json", {"mapping": baseline()})
        added = dict(baseline(), newDSM={"desc": acme.DESCRIPTION, "is_default": False, "services": []})
        with patch.object(self.runner, "snapshot", return_value=added), \
                patch.object(self.runner, "validate", return_value=NEW), \
                patch.object(self.runner, "import_bundle") as imported, contextlib.redirect_stdout(io.StringIO()):
            self.runner.import_new()
        imported.assert_not_called()

    def test_assignment_default_and_identity_drift_prevent_mutation(self):
        self.accepted()
        for change in ("service", "default", "identity", "duplicate"):
            current = accepted_map()
            if change == "service":
                current["newDSM"]["services"].append(current["S3existing"]["services"].pop())
            elif change == "default":
                current["newDSM"]["is_default"] = False
                current[acme.ORIGINAL_ID]["is_default"] = True
            elif change == "identity":
                current["unexpected"] = current.pop("newDSM")
            else:
                current["other"] = copy.deepcopy(current["newDSM"])
            with self.subTest(change=change), patch.object(self.runner, "snapshot", return_value=current), \
                    patch.object(self.runner, "acme") as issue, self.assertRaises(acme.Failure):
                self.runner.run()
            issue.assert_not_called()

    def test_failed_import_retries_same_issued_bundle_and_retains_rollback(self):
        self.accepted()
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "acme", return_value=self.runner.bundle("issued")) as renew, \
                patch.object(self.runner, "validate", return_value=NEW), \
                patch.object(self.runner, "saved_bundle"), \
                patch.object(self.runner, "live", return_value=OLD), \
                patch.object(self.runner, "import_bundle", side_effect=[acme.Failure("import failed"), {"success": True}]) as imported, \
                patch.object(self.runner, "verified") as verified:
            marker = self.runner.read(self.root / "last-verified-success.json")
            with self.assertRaises(acme.Failure):
                self.runner.run()
            self.assertEqual(self.runner.read(self.root / "last-verified-success.json"), marker)
            self.assertEqual(self.runner.load("previous.json"), {"sha256": OLD})
            verified.assert_not_called()
            self.runner.run()
            self.assertEqual(renew.call_count, 2)
            self.assertEqual(imported.call_args_list[0], imported.call_args_list[1])
            verified.assert_called_once_with(self.runner.bundle("issued"), NEW)
            self.assertEqual(self.runner.load("previous.json"), {"sha256": OLD})

    def test_acme_error_is_failure_even_when_existing_live_cert_is_healthy(self):
        self.accepted()
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "acme", side_effect=acme.Failure("renew failed")), \
                patch.object(self.runner, "live") as live:
            marker = self.runner.read(self.root / "last-verified-success.json")
            with self.assertRaises(acme.Failure):
                self.runner.run()
            live.assert_not_called()
            self.assertEqual(self.runner.read(self.root / "last-verified-success.json"), marker)

    def test_verification_failure_never_advances_success_or_good(self):
        self.accepted()
        marker = self.runner.read(self.root / "last-verified-success.json")
        with patch.object(self.runner, "live", side_effect=acme.Failure("not serving leaf")), self.assertRaises(acme.Failure):
            self.runner.verified(self.runner.bundle("issued"), NEW)
        self.assertEqual(self.runner.load("good.json"), {"sha256": OLD})
        self.assertEqual(self.runner.read(self.root / "last-verified-success.json"), marker)

    def test_rollback_retains_previous_after_success_and_failure(self):
        self.accepted()
        self.save("previous.json", {"sha256": OLD})
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "saved_bundle", return_value=self.runner.bundle("issued")), \
                patch.object(self.runner, "import_bundle") as imported, \
                patch.object(self.runner, "verified") as verified, \
                patch.object(self.runner, "acme") as renewal:
            self.runner.rollback()
            imported.assert_called_once_with(self.runner.bundle("issued"), "newDSM", True)
            verified.assert_called_once_with(self.runner.bundle("issued"), OLD)
            self.assertEqual(self.runner.load("previous.json"), {"sha256": OLD})
            imported.side_effect = acme.Failure("native import failed")
            with self.assertRaises(acme.Failure):
                self.runner.rollback()
            self.assertEqual(self.runner.load("previous.json"), {"sha256": OLD})
            renewal.assert_not_called()

    def test_reimport_uses_same_replacement_path_without_renewal_or_backup_change(self):
        self.accepted()
        self.save("previous.json", {"sha256": NEW})
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "saved_bundle", return_value=self.runner.bundle("issued")), \
                patch.object(self.runner, "import_bundle") as imported, \
                patch.object(self.runner, "verified"), patch.object(self.runner, "acme") as renewal:
            self.runner.reimport()
            imported.assert_called_once_with(self.runner.bundle("issued"), "newDSM", True)
            self.assertEqual(self.runner.load("previous.json"), {"sha256": NEW})
            renewal.assert_not_called()

    def test_watchdog_freshness_and_readonly_behavior(self):
        self.accepted()
        before = {path.name: path.read_bytes() for path in self.root.glob("*.json")}
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), patch.object(self.runner, "live") as live:
            self.runner.watchdog()
            live.assert_called_once_with(OLD)
            self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.glob("*.json")})
            self.save("last-verified-success.json", {"sha256": OLD, "at": int(time.time() - acme.FRESHNESS - 2)})
            with self.assertRaisesRegex(acme.Failure, "26 hours"):
                self.runner.watchdog()
            self.save("last-verified-success.json", {"at": int(time.time())})
            with self.assertRaisesRegex(acme.Failure, "fingerprint"):
                self.runner.watchdog()

    def test_tls_normal_trust_sni_both_ports_exact_san_same_leaf(self):
        context = Mock()
        connection = Mock()
        tls = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        tls.__enter__ = Mock(return_value=tls)
        tls.__exit__ = Mock(return_value=False)
        context.wrap_socket.return_value = tls
        cert = {"subjectAltName": (("DNS", acme.HOST),), "notAfter": "Dec 31 23:59:59 2099 GMT"}
        tls.getpeercert.side_effect = lambda binary_form=False: b"served leaf" if binary_form else cert
        with patch.object(acme.ssl, "create_default_context", return_value=context) as trusted, \
                patch.object(acme.socket, "create_connection", return_value=connection) as connect:
            fingerprint = self.runner.live()
            self.assertEqual(fingerprint, hashlib.sha256(b"served leaf").hexdigest())
            self.assertEqual([call.args[0] for call in connect.call_args_list], [("127.0.0.1", 443), ("127.0.0.1", 5001)])
            self.assertEqual(trusted.call_count, 2)
            self.assertTrue(all(call.kwargs["server_hostname"] == acme.HOST for call in context.wrap_socket.call_args_list))
            cert["subjectAltName"] = (("DNS", "*.home.kelch.io"),)
            with self.assertRaisesRegex(acme.Failure, "exactly"):
                self.runner.live()

    def test_main_errors_do_not_echo_child_output_or_credentials(self):
        output = io.StringIO()
        with patch.object(acme.sys, "argv", ["athena-acme.py", "run"]), \
                patch.object(acme.Runner, "execute", side_effect=OSError("fake_credential_for_offline_tests_only")), \
                contextlib.redirect_stderr(output):
            self.assertEqual(acme.main(), 1)
        self.assertNotIn("fake_credential", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
