#!/usr/bin/env python3
"""Offline contract tests: no credentials, DSM, Cloudflare, or ACME traffic."""

import contextlib
import ast
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import sys
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
    def test_operator_recovery_and_renewal_cli_require_exactly_one_digest(self):
        digest = "a" * 64
        for action in ("recover-initial", "renew-now"):
            with patch.object(acme.sys, "argv", ["athena-acme.py", action, digest]), \
                    patch.object(acme.Runner, "execute") as execute:
                self.assertEqual(acme.main(), 0)
                execute.assert_called_once_with(action, [digest])
            for arguments in ([], [digest, digest]):
                with self.subTest(action=action, arguments=arguments), \
                        patch.object(acme.sys, "argv", ["athena-acme.py", action] + arguments), \
                        patch.object(acme.Runner, "execute") as execute, contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit) as error:
                    acme.main()
                self.assertEqual(error.exception.code, 2)
                execute.assert_not_called()

    def test_adapter_dash_leading_txt_reaches_main_unchanged(self):
        value = "-" + "A" * 42
        for action in ("dns-add", "dns-remove"):
            with patch.object(acme.sys, "argv", ["athena-acme.py", action, "--", "_acme-challenge." + acme.HOST, value]), \
                    patch.object(acme.Runner, "execute") as execute:
                self.assertEqual(acme.main(), 0)
                execute.assert_called_once_with(action, ["_acme-challenge." + acme.HOST, value])
        adapter = Path(acme.__file__).with_name("dns_athena.sh").read_text()
        self.assertIn('dns-add -- "$1" "$2"', adapter)
        self.assertIn('dns-remove -- "$1" "$2"', adapter)

    def test_python38_grammar(self):
        for path in (Path(acme.__file__), Path(__file__)):
            ast.parse(path.read_text(), feature_version=(3, 8))

    def test_local_adapter_hash_matches_reviewed_source(self):
        adapter = Path(acme.__file__).with_name("dns_athena.sh")
        self.assertEqual(hashlib.sha256(adapter.read_bytes()).hexdigest(), acme.SOURCE_HASHES["dnsapi/dns_athena.sh"])

    def test_api_preamble_and_strict_success(self):
        self.assertTrue(acme.parse_api(b'{"success":true,"data":{}}')["success"])
        for output in (b"not JSON", b'prefix\n{"success":true}', b'{"success":false}', b'{"success":1}', b'{"success":"true"}',
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
        self.put("client/dnsapi/dns_athena.sh", b"test dns plugin\n")
        hashes = {name: hashlib.sha256(self.root.joinpath("client", name).read_bytes()).hexdigest()
                  for name in ("acme.sh", "dnsapi/dns_athena.sh")}
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

    @contextlib.contextmanager
    def live_leaves(self, leaves=(b"leaf", b"leaf")):
        contexts = []
        connection = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
        for leaf in leaves:
            tls = Mock(__exit__=Mock(return_value=False))
            tls.__enter__ = Mock(return_value=tls)
            cert = {"subjectAltName": (("DNS", acme.HOST),), "notAfter": "Dec 31 23:59:59 2099 GMT"}
            tls.getpeercert.side_effect = lambda binary_form=False, value=leaf: value if binary_form else cert
            context = Mock()
            context.wrap_socket.return_value = tls
            contexts.append(context)
        with patch.object(acme.ssl, "create_default_context", side_effect=contexts), \
                patch.object(acme.socket, "create_connection", return_value=connection) as connect:
            yield connect

    def runtime_files(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

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

    def test_normal_chain_and_seven_day_check_are_required(self):
        with self.mock_validation():
            self.runner.validate(self.runner.bundle("issued"))
            calls = self.runner.checked.call_args_list
            self.assertTrue(any("-verify_hostname" in item.args[0] and "-untrusted" in item.args[0] for item in calls))
            self.assertTrue(any(str(7 * 86400) in item.args[0] and "-checkend" in item.args[0] for item in calls))

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
        self.assertNotIn("env", self.command.call_args_list[0].kwargs)
        self.assertEqual(self.command.call_args_list[0].kwargs["timeout"], 1800)

    def test_forced_client_only_forces_renewal_and_refuses_not_due_exit(self):
        self.command.side_effect = [(0, b"renewed"), (0, b"installed")]
        with patch.object(self.runner, "audit"):
            self.runner.acme(force=True)
        renew, install = [call.args[0] for call in self.command.call_args_list]
        self.assertEqual(renew.count("--force"), 1)
        self.assertIn("--renew", renew)
        self.assertNotIn("--force", install)
        self.command.reset_mock()
        self.command.side_effect = [(2, b"not due")]
        with self.assertRaisesRegex(acme.Failure, "ACME issue/renew failed"):
            self.runner.acme(force=True)
        self.assertEqual(self.command.call_count, 1)

    def test_force_requires_boolean_and_cannot_force_initial_issuance(self):
        with patch.object(self.runner, "token") as token, patch.object(self.runner, "snapshot") as snapshot:
            for force in (None, 1, "false", "true"):
                for method in (self.runner.acme, self.runner.run):
                    with self.subTest(force=force, method=method.__name__), self.assertRaises(acme.Failure):
                        method(force=force)
            with self.assertRaises(acme.Failure):
                self.runner.acme(initial=True, force=True)
            token.assert_not_called()
            snapshot.assert_not_called()
        self.command.assert_not_called()

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
            self.assertNotIn("as_default", params)
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

    def test_replacement_preserves_default_with_native_string_field(self):
        self.command.side_effect = None
        self.command.return_value = (0, b'{"success":true,"data":{"id":"newDSM"}}')
        self.runner.import_bundle(self.runner.bundle("issued"), "newDSM", is_default=True)
        args = self.command.call_args.args[0]
        self.assertIn('id="newDSM"', args)
        self.assertEqual([arg for arg in args if arg.startswith("as_default=")], ['as_default="true"'])
        self.assertTrue((self.root / "issued/key.pem").exists())

    def test_nonboolean_default_flag_is_refused_before_staging_or_import(self):
        with patch.object(acme.tempfile, "TemporaryDirectory") as staged, \
                patch.object(self.runner, "write") as written, patch.object(self.runner, "api") as imported:
            for value in (0, 1, "false", "true", None, [], {}):
                with self.subTest(value=value), self.assertRaisesRegex(acme.Failure, "must be a boolean"):
                    self.runner.import_bundle(self.runner.bundle("issued"), "newDSM", is_default=value)
            staged.assert_not_called()
            written.assert_not_called()
            imported.assert_not_called()

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

    def test_initial_default_change_fails_closed_and_retry_does_not_duplicate(self):
        changed = baseline()
        changed[acme.ORIGINAL_ID]["is_default"] = False
        changed["newDSM"] = {"desc": acme.DESCRIPTION, "is_default": True, "services": []}
        with patch.object(self.runner, "snapshot", side_effect=[baseline(), changed, changed]), \
                patch.object(self.runner, "validate", return_value=NEW), \
                patch.object(self.runner, "import_bundle", return_value={"success": True, "data": {"id": "newDSM"}}) as imported:
            for attempt in ("initial", "recovery"):
                with self.subTest(attempt=attempt), self.assertRaisesRegex(acme.Failure, "changed existing defaults"):
                    self.runner.import_new()
                self.assertEqual(self.runner.load("baseline.json")["mapping"], baseline())
                for name in ("candidate.json", "accepted.json", "good.json", "last-verified-success.json"):
                    self.assertFalse(self.root.joinpath(name).exists())
            imported.assert_called_once()

    def test_recover_initial_success_without_creating_duplicate(self):
        self.save("baseline.json", {"mapping": baseline()})
        added = dict(baseline(), newDSM={"desc": acme.DESCRIPTION, "is_default": False, "services": []})
        with patch.object(self.runner, "snapshot", return_value=added), \
                patch.object(self.runner, "validate", return_value=NEW), \
                patch.object(self.runner, "import_bundle") as imported, contextlib.redirect_stdout(io.StringIO()):
            self.runner.import_new()
        imported.assert_not_called()

    def test_recover_initial_only_publishes_candidate_after_both_listeners_verify(self):
        self.save("baseline.json", {"mapping": baseline()})
        before = self.runtime_files()
        digest = hashlib.sha256(acme.canonical(accepted_map()).encode()).hexdigest()
        fingerprint = hashlib.sha256(b"leaf").hexdigest()
        with patch.object(self.runner, "api", return_value=list_response(accepted_map())) as api, \
                self.mock_validation(), self.live_leaves() as connect, \
                patch.object(self.runner, "acme") as client, patch.object(self.runner, "import_bundle") as imported, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.runner.recover_initial(digest)
        self.assertEqual([call.args for call in api.call_args_list], [("list",), ("list",)])
        self.assertEqual([call.args[0] for call in connect.call_args_list], [("127.0.0.1", 443), ("127.0.0.1", 5001)])
        client.assert_not_called()
        imported.assert_not_called()
        self.assertIn("Run accept", output.getvalue())
        after = self.runtime_files()
        self.assertEqual(json.loads(after.pop("candidate.json")), {"cert_id": "newDSM", "sha256": fingerprint})
        self.assertEqual(after, before)

    def test_recover_initial_refuses_invalid_or_stale_digest_without_publication(self):
        self.save("baseline.json", {"mapping": baseline()})
        before = self.runtime_files()
        with patch.object(self.runner, "api", return_value=list_response(accepted_map())), \
                patch.object(self.runner, "validate") as validate, patch.object(self.runner, "live") as live:
            for digest in (None, "", "a" * 63, "A" * 64, "0" * 64):
                with self.subTest(digest=digest), self.assertRaises(acme.Failure):
                    self.runner.recover_initial(digest)
                self.assertEqual(self.runtime_files(), before)
            validate.assert_not_called()
            live.assert_not_called()

    def test_recover_initial_requires_exact_transition_and_unique_identity(self):
        self.save("baseline.json", {"mapping": baseline()})
        before = self.runtime_files()
        for change in ("default-only", "unrelated", "metadata", "original", "duplicate"):
            current = accepted_map()
            if change == "default-only":
                current[acme.ORIGINAL_ID]["services"].append(current["newDSM"]["services"].pop())
            elif change == "unrelated":
                current["newDSM"]["services"].append(current[acme.ORIGINAL_ID]["services"].pop())
            elif change == "metadata":
                current["newDSM"]["services"][0]["user_setable"] = False
            elif change == "original":
                del current[acme.ORIGINAL_ID]
            else:
                current["duplicate"] = {"desc": acme.DESCRIPTION, "is_default": False, "services": []}
            digest = hashlib.sha256(acme.canonical(current).encode()).hexdigest()
            with self.subTest(change=change), patch.object(self.runner, "api", return_value=list_response(current)), \
                    patch.object(self.runner, "validate") as validate, patch.object(self.runner, "live") as live, \
                    self.assertRaises(acme.Failure):
                self.runner.recover_initial(digest)
            validate.assert_not_called()
            live.assert_not_called()
            self.assertEqual(self.runtime_files(), before)

    def test_recover_initial_requires_saved_baseline_and_no_existing_candidate_or_acceptance(self):
        digest = hashlib.sha256(acme.canonical(accepted_map()).encode()).hexdigest()
        for state in ("missing baseline", "candidate.json", "accepted.json"):
            if state != "missing baseline":
                self.save("baseline.json", {"mapping": baseline()})
                self.save(state, {"untouched": True})
            before = self.runtime_files()
            with self.subTest(state=state), patch.object(self.runner, "api") as api, \
                    patch.object(self.runner, "validate") as validate, patch.object(self.runner, "live") as live, \
                    self.assertRaises(acme.Failure):
                self.runner.recover_initial(digest)
            api.assert_not_called()
            validate.assert_not_called()
            live.assert_not_called()
            self.assertEqual(self.runtime_files(), before)
            if state != "missing baseline":
                self.root.joinpath(state).unlink()

    def test_recover_initial_rejects_invalid_issued_key_or_san(self):
        self.save("baseline.json", {"mapping": baseline()})
        before = self.runtime_files()
        digest = hashlib.sha256(acme.canonical(accepted_map()).encode()).hexdigest()
        for invalid in ({"key": b"different key"}, {"san": b"DNS:*.home.kelch.io"}):
            with self.subTest(invalid=invalid), patch.object(self.runner, "api", return_value=list_response(accepted_map())), \
                    self.mock_validation(**invalid), patch.object(self.runner, "live") as live, self.assertRaises(acme.Failure):
                self.runner.recover_initial(digest)
            live.assert_not_called()
            self.assertEqual(self.runtime_files(), before)

    def test_recover_initial_rejects_wrong_live_leaf_on_either_listener(self):
        self.save("baseline.json", {"mapping": baseline()})
        before = self.runtime_files()
        digest = hashlib.sha256(acme.canonical(accepted_map()).encode()).hexdigest()
        for leaves in ((b"old", b"old"), (b"leaf", b"old"), (b"old", b"leaf")):
            with self.subTest(leaves=leaves), patch.object(self.runner, "api", return_value=list_response(accepted_map())), \
                    self.mock_validation(), self.live_leaves(leaves), self.assertRaises(acme.Failure):
                self.runner.recover_initial(digest)
            self.assertEqual(self.runtime_files(), before)

    def test_recover_initial_rejects_map_change_during_verification(self):
        self.save("baseline.json", {"mapping": baseline()})
        before = self.runtime_files()
        digest = hashlib.sha256(acme.canonical(accepted_map()).encode()).hexdigest()
        changed = accepted_map()
        changed[acme.ORIGINAL_ID]["services"][0]["metadata"] = "changed"
        with patch.object(self.runner, "api", side_effect=[list_response(accepted_map()), list_response(changed)]), \
                self.mock_validation(), self.live_leaves(), self.assertRaisesRegex(acme.Failure, "changed during initial recovery"):
            self.runner.recover_initial(digest)
        self.assertEqual(self.runtime_files(), before)

    def test_inspect_reports_digest_of_sanitized_certificate_map(self):
        output = io.StringIO()
        with patch.object(self.runner, "api", return_value=list_response(accepted_map())), contextlib.redirect_stdout(output):
            self.runner.inspect()
        self.assertEqual(json.loads(output.getvalue()), {
            "hostname": acme.HOST, "certificates": accepted_map(),
            "current_sha256": hashlib.sha256(acme.canonical(accepted_map()).encode()).hexdigest(),
        })

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
                patch.object(self.runner, "live", side_effect=[OLD, OLD, NEW]), \
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
            verified.assert_called_once_with(self.runner.bundle("issued"), NEW, minimum=acme.MIN_VALIDITY)
            self.assertEqual(self.runner.load("previous.json"), {"sha256": OLD})

    def test_renew_now_prechecks_refuse_client_on_drift_pending_import_or_wrong_leaf(self):
        self.accepted()
        before = self.runtime_files()
        for failure in ("invalid digest", "wrong good", "pending issued", "wrong live", "map drift", "map changes"):
            changed = accepted_map()
            changed[acme.ORIGINAL_ID]["services"][0]["metadata"] = "changed"
            snapshots = [accepted_map(), changed] if failure == "map changes" else None
            with self.subTest(failure=failure), \
                    patch.object(self.runner, "snapshot", side_effect=snapshots,
                                 return_value=changed if failure == "map drift" else accepted_map()), \
                    patch.object(self.runner, "saved_bundle"), \
                    patch.object(self.runner, "validate", return_value=NEW if failure == "pending issued" else OLD), \
                    patch.object(self.runner, "live", side_effect=acme.Failure("wrong live leaf") if failure == "wrong live" else None), \
                    patch.object(self.runner, "acme") as client, self.assertRaises(acme.Failure):
                self.runner.renew_now("invalid" if failure == "invalid digest" else NEW if failure == "wrong good" else OLD)
            client.assert_not_called()
            self.assertEqual(self.runtime_files(), before)

    def test_renew_now_requires_a_deployable_rollback_key_before_any_order(self):
        old = hashlib.sha256(b"leaf").hexdigest()
        self.accepted()
        self.save("good.json", {"sha256": old})
        with self.mock_validation():
            self.runner.archive(self.runner.bundle("issued"), old)
        before = self.runtime_files()
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                self.mock_validation(key=b"mismatched rollback public key"), \
                patch.object(self.runner, "acme") as client, \
                self.assertRaisesRegex(acme.Failure, "does not match its private key"):
            self.runner.renew_now(old)
        client.assert_not_called()
        self.assertEqual(self.runtime_files(), before)

    def test_renew_now_same_leaf_never_imports_or_advances_success(self):
        self.accepted()
        before = self.runtime_files()
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "saved_bundle"), patch.object(self.runner, "validate", return_value=OLD), \
                patch.object(self.runner, "live", return_value=OLD), \
                patch.object(self.runner, "acme", return_value=self.runner.bundle("issued")) as client, \
                patch.object(self.runner, "import_bundle") as imported, self.assertRaisesRegex(acme.Failure, "different leaf"):
            self.runner.renew_now(OLD)
        client.assert_called_once_with(force=True)
        imported.assert_not_called()
        self.assertEqual(self.runtime_files(), before)

    def test_renew_now_success_retains_real_rollback_bundle_and_refuses_same_approval(self):
        old = hashlib.sha256(b"leaf").hexdigest()
        new = hashlib.sha256(b"new-leaf").hexdigest()
        self.accepted()
        self.save("good.json", {"sha256": old})
        with self.mock_validation():
            self.runner.archive(self.runner.bundle("issued"), old)
        def renew(**kwargs):
            self.put("issued/cert.pem", b"-----BEGIN CERTIFICATE-----\nbmV3LWxlYWY=\n-----END CERTIFICATE-----\n")
            self.put("issued/fullchain.pem", self.root.joinpath("issued/cert.pem").read_bytes() + CHAIN)
            return self.runner.bundle("issued")
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), self.mock_validation(), \
                patch.object(self.runner, "live", side_effect=[old, old, new, new]), \
                patch.object(self.runner, "acme", side_effect=renew) as client, \
                patch.object(self.runner, "import_bundle", return_value={"success": True}) as imported:
            self.runner.renew_now(old)
            before_repeat = self.runtime_files()
            with self.assertRaisesRegex(acme.Failure, "differs from the reviewed fingerprint"):
                self.runner.renew_now(old)
            self.assertEqual(self.runtime_files(), before_repeat)
        client.assert_called_once_with(force=True)
        imported.assert_called_once_with(self.runner.bundle("issued"), "newDSM", True)
        self.assertEqual(self.runner.load("good.json"), {"sha256": new})
        self.assertEqual(self.runner.load("previous.json"), {"sha256": old})
        self.assertEqual(self.runner.load("last-verified-success.json")["sha256"], new)
        self.assertEqual(set(path.name for path in self.root.joinpath("bundles").iterdir()), {old, new})

    def test_renew_now_failed_import_refuses_another_order_and_unforced_run_recovers(self):
        old = hashlib.sha256(b"leaf").hexdigest()
        new = hashlib.sha256(b"new-leaf").hexdigest()
        self.accepted()
        self.save("good.json", {"sha256": old})
        marker = self.runner.read(self.root / "last-verified-success.json")
        with self.mock_validation():
            self.runner.archive(self.runner.bundle("issued"), old)
        def renew(**kwargs):
            self.put("issued/cert.pem", b"-----BEGIN CERTIFICATE-----\nbmV3LWxlYWY=\n-----END CERTIFICATE-----\n")
            self.put("issued/fullchain.pem", self.root.joinpath("issued/cert.pem").read_bytes() + CHAIN)
            return self.runner.bundle("issued")
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), self.mock_validation(), \
                patch.object(self.runner, "live", return_value=old) as live, \
                patch.object(self.runner, "acme", side_effect=renew) as client, \
                patch.object(self.runner, "import_bundle", side_effect=[acme.Failure("import failed"), {"success": True}]) as imported:
            with self.assertRaisesRegex(acme.Failure, "import failed"):
                self.runner.renew_now(old)
            self.assertEqual(self.runner.load("previous.json"), {"sha256": old})
            self.assertEqual(self.runner.load("good.json"), {"sha256": old})
            self.assertEqual(self.runner.read(self.root / "last-verified-success.json"), marker)
            with self.assertRaisesRegex(acme.Failure, "use ordinary run"):
                self.runner.renew_now(old)
            client.assert_called_once_with(force=True)
            live.side_effect = [old, new, new]
            self.runner.run()
        self.assertEqual([call.kwargs for call in client.call_args_list], [{"force": True}, {}])
        self.assertEqual(imported.call_count, 2)
        self.assertEqual(self.runner.load("previous.json"), {"sha256": old})
        self.assertEqual(self.runner.load("good.json"), {"sha256": new})
        self.assertEqual(self.runner.load("last-verified-success.json")["sha256"], new)

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
                patch.object(self.runner, "live"), \
                patch.object(self.runner, "verified") as verified, \
                patch.object(self.runner, "acme") as renewal:
            self.runner.rollback()
            imported.assert_called_once_with(self.runner.bundle("issued"), "newDSM", True)
            verified.assert_called_once_with(self.runner.bundle("issued"), OLD, minimum=acme.RECOVERY_VALIDITY)
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
                patch.object(self.runner, "live"), \
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
            with self.assertRaisesRegex(acme.Failure, "3 hours"):
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

    def test_dns_scope_and_exact_value_cleanup(self):
        name, value = "_acme-challenge." + acme.HOST, "A" * 43
        zone, record = "1" * 32, "2" * 32
        ours = {"id": record, "type": "TXT", "name": name, "content": value}
        other = dict(ours, id="3" * 32, content="B" * 43)
        with patch.object(self.runner, "cloudflare", side_effect=[[{"id": zone, "name": "kelch.io"}], [], ours]) as cf:
            self.runner.dns("dns-add", name, value)
            self.assertEqual(cf.call_args.args, ("POST", "zones/" + zone + "/dns_records",
                             {"type": "TXT", "name": name, "content": value, "ttl": 120}))
        with patch.object(self.runner, "cloudflare", side_effect=[[{"id": zone, "name": "kelch.io"}], [ours, other], {"id": record}]) as cf:
            self.runner.dns("dns-remove", name, value)
            self.assertEqual(cf.call_args.args, ("DELETE", "zones/" + zone + "/dns_records/" + record))
            self.assertEqual(cf.call_count, 3)
        with patch.object(self.runner, "cloudflare") as cf:
            for bad_name, bad_value in (("_acme-challenge.s3-storage.home.kelch.io", value), (name, "bad")):
                with self.assertRaises(acme.Failure):
                    self.runner.dns("dns-add", bad_name, bad_value)
            cf.assert_not_called()

    def test_cloudflare_token_stays_in_https_headers(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"success":true,"result":[]}'
        opener = Mock()
        opener.open.return_value = response
        with patch.object(acme.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(self.runner.cloudflare("GET", "zones?name=kelch.io"), [])
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.cloudflare.com/client/v4/zones?name=kelch.io")
            self.assertEqual(request.get_header("Authorization"), "Bearer fake_credential_for_offline_tests_only")
            handlers = build.call_args.args
            self.assertEqual(handlers[0].proxies, {})
            self.assertIsNone(handlers[1].redirect_request(request, None, 302, "", {}, "https://elsewhere.invalid/"))
            self.command.assert_not_called()

    def test_quoted_txt_cleanup_and_idempotent_add_preserve_other_value(self):
        name, value = "_acme-challenge." + acme.HOST, "-" + "A" * 42
        zone, record = "1" * 32, "2" * 32
        ours = {"id": record, "type": "TXT", "name": name, "content": '"' + value + '"'}
        other = dict(ours, id="3" * 32, content='"' + "B" * 43 + '"')
        with patch.object(self.runner, "cloudflare", side_effect=[[{"id": zone, "name": "kelch.io"}], [ours, other]]) as cf:
            self.runner.dns("dns-add", name, value)
            self.assertEqual(cf.call_count, 2)
        with patch.object(self.runner, "cloudflare", side_effect=[[{"id": zone, "name": "kelch.io"}], [ours, other], {"id": record}]) as cf:
            self.runner.dns("dns-remove", name, value)
            self.assertEqual(cf.call_args.args, ("DELETE", "zones/" + zone + "/dns_records/" + record))

    def test_failed_cleanup_is_visible_to_parent_and_retried_without_issuance(self):
        name, value, zone = "_acme-challenge." + acme.HOST, "A" * 43, "1" * 32
        self.save("dns-cleanup-pending.json", {"name": name, "value": value})
        with patch.object(self.runner, "cloudflare", side_effect=acme.Failure("DNS cleanup unavailable")), self.assertRaises(acme.Failure):
            self.runner.acme()
        self.command.assert_not_called()
        self.assertTrue((self.root / "dns-cleanup-pending.json").exists())
        with patch.object(self.runner, "cloudflare", side_effect=[[{"id": zone, "name": "kelch.io"}], []]):
            self.runner.finish_dns_cleanup()
        self.assertFalse((self.root / "dns-cleanup-pending.json").exists())

        def successful_client_with_failed_cleanup(*args, **kwargs):
            self.save("dns-cleanup-pending.json", {"name": name, "value": value})
            return 0, b"client ignored cleanup error"
        self.command.side_effect = successful_client_with_failed_cleanup
        with patch.object(self.runner, "cloudflare", side_effect=acme.Failure("DNS cleanup unavailable")), self.assertRaises(acme.Failure):
            self.runner.acme()
        self.assertTrue((self.root / "dns-cleanup-pending.json").exists())

    def test_provision_reads_valid_stdin_before_exclusive_token_creation(self):
        token = self.root / "cloudflare-token"
        token.unlink()
        for value in (b"", b"invalid", b"x" * 1025):
            with patch.object(acme.sys, "stdin", io.TextIOWrapper(io.BytesIO(value))), self.assertRaises(acme.Failure):
                self.runner.provision_token()
            self.assertFalse(token.exists())
        with patch.object(acme.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"fake_valid_credential_for_offline_test\n"))):
            self.runner.provision_token()
        self.assertEqual(token.read_bytes(), b"fake_valid_credential_for_offline_test")
        with patch.object(acme.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"another_valid_credential_for_offline_test"))), self.assertRaises(FileExistsError):
            self.runner.provision_token()
        self.assertEqual(token.read_bytes(), b"fake_valid_credential_for_offline_test")

    def test_review_bindings_digest_and_previous_bundle_retention(self):
        fingerprint = hashlib.sha256(b"leaf").hexdigest()
        old_fingerprint = hashlib.sha256(b"old-leaf").hexdigest()
        self.accepted()
        previous = self.root / "bundles" / old_fingerprint
        previous.mkdir(mode=0o700)
        old_leaf = b"-----BEGIN CERTIFICATE-----\nb2xkLWxlYWY=\n-----END CERTIFICATE-----\n"
        for name in acme.FILES:
            self.put(str(previous.relative_to(self.root) / name), old_leaf if name == "cert.pem" else b"private old bytes")
        self.save("previous.json", {"sha256": old_fingerprint})
        with self.mock_validation(), patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "live", return_value=fingerprint):
            self.runner.verified(self.runner.bundle("issued"), fingerprint)
            self.assertTrue(previous.exists())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.runner.review_bindings()
            self.assertEqual(json.loads(output.getvalue())["current_sha256"], hashlib.sha256(acme.canonical(accepted_map()).encode()).hexdigest())

    def test_reload_timeout_does_not_mark_verification_success(self):
        self.accepted()
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), patch.object(self.runner, "import_bundle"), \
                patch.object(self.runner, "live", side_effect=acme.Failure("old leaf")), \
                patch.object(acme.time, "monotonic", side_effect=[0, 61]), patch.object(self.runner, "verified") as verified, \
                self.assertRaisesRegex(acme.Failure, "within 60 seconds"):
            self.runner.replace(self.runner.bundle("issued"), NEW)
        verified.assert_not_called()

    def test_accept_archives_and_unchanged_run_verifies_real_success_state(self):
        fingerprint = hashlib.sha256(b"leaf").hexdigest()
        self.save("baseline.json", {"mapping": baseline()})
        self.save("candidate.json", {"cert_id": "newDSM", "sha256": fingerprint})
        with self.mock_validation(), patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "live", return_value=fingerprint):
            self.runner.accept()
            self.assertEqual(self.runner.load("good.json"), {"sha256": fingerprint})
            self.assertEqual(self.runner.saved_bundle({"sha256": fingerprint})["key.pem"].read_bytes(), b"private test key")
            extra = self.root / "bundles" / ("c" * 64)
            extra.mkdir(mode=0o700)
            with patch.object(self.runner, "acme", return_value=self.runner.bundle("issued")), patch.object(self.runner, "import_bundle") as imported:
                self.runner.run()
            imported.assert_not_called()
            self.assertFalse(extra.exists())
            self.assertEqual(self.runner.load("last-verified-success.json")["sha256"], fingerprint)

    def test_rebaseline_requires_reviewed_digest_and_preserves_renewal_marker(self):
        self.accepted()
        changed = accepted_map()
        changed[acme.ORIGINAL_ID]["services"].append({"owner": "root", "subscriber": "new", "service": "new"})
        digest = hashlib.sha256(acme.canonical(changed).encode()).hexdigest()
        marker = self.runner.read(self.root / "last-verified-success.json")
        with patch.object(self.runner, "snapshot", return_value=changed), patch.object(self.runner, "live"), \
                patch.object(self.runner, "saved_bundle"), patch.object(self.runner, "acme") as client:
            with self.assertRaises(acme.Failure):
                self.runner.rebaseline("0" * 64)
            self.runner.rebaseline(digest)
            self.assertEqual(self.runner.load("accepted.json")["mapping"], changed)
            self.assertEqual(self.runner.read(self.root / "last-verified-success.json"), marker)
            client.assert_not_called()
            changed["newDSM"]["is_default"] = False
            with self.assertRaises(acme.Failure):
                self.runner.rebaseline(digest)

    def test_recovery_uses_one_day_floor_and_waits_for_async_reload(self):
        self.accepted()
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "saved_bundle", return_value=self.runner.bundle("issued")) as saved, \
                patch.object(self.runner, "import_bundle"), patch.object(self.runner, "verified") as verified, \
                patch.object(self.runner, "live", side_effect=[acme.Failure("old leaf"), OLD]), patch.object(acme.time, "sleep"):
            self.runner.reimport()
            saved.assert_called_once_with({"sha256": OLD}, minimum=86400)
            verified.assert_called_once_with(self.runner.bundle("issued"), OLD, minimum=86400)

    def test_real_command_separates_stderr_and_kills_timed_out_group(self):
        code, output = acme.Runner.command(self.runner, [sys.executable, "-c", 'import sys; print(\'{"success":true}\'); print("param={} preamble",file=sys.stderr)'])
        self.assertEqual(code, 0)
        self.assertTrue(acme.parse_api(output)["success"])
        self.assertIn(b"param={}", self.runner.command_stderr)
        with patch.object(acme.os, "killpg", wraps=acme.os.killpg) as kill, self.assertRaisesRegex(acme.Failure, "timed out"):
            acme.Runner.command(self.runner, [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.02)
        self.assertEqual(kill.call_count, 1)

    def test_termination_signals_reap_child_before_releasing_operation_lock(self):
        original_spawn, original_kill, original_fstat = acme.subprocess.Popen, os.killpg, os.fstat
        def root_fstat(descriptor):
            values = list(original_fstat(descriptor))
            values[4] = 0
            return os.stat_result(values)
        for requested in (acme.signal.SIGTERM, acme.signal.SIGHUP, acme.signal.SIGINT):
            children = []
            previous = {sig: acme.signal.getsignal(sig) for sig in (acme.signal.SIGTERM, acme.signal.SIGHUP, acme.signal.SIGINT)}
            def spawn(*args, **kwargs):
                child = original_spawn(*args, **kwargs)
                children.append(child)
                # Signal during Popen, before command() receives the child PID.
                os.kill(os.getpid(), requested)
                return child
            def kill_group(pid, sig):
                with (self.root / "lock").open("rb") as competing:
                    with self.assertRaises(BlockingIOError):
                        acme.fcntl.flock(competing.fileno(), acme.fcntl.LOCK_EX | acme.fcntl.LOCK_NB)
                original_kill(pid, sig)
            with self.subTest(signal=requested), patch.object(acme.os, "fstat", side_effect=root_fstat):
                with self.runner.lock(), patch.object(acme.subprocess, "Popen", side_effect=spawn), \
                        patch.object(acme.os, "killpg", side_effect=kill_group) as killed, \
                        self.assertRaisesRegex(acme.Failure, "termination signal"):
                    acme.Runner.command(self.runner, [sys.executable, "-c", "import time; time.sleep(5)"])
                self.assertIsNotNone(children[0].poll())
                killed.assert_called_once_with(children[0].pid, acme.signal.SIGKILL)
                self.assertEqual({sig: acme.signal.getsignal(sig) for sig in previous}, previous)

    def test_archive_directory_sync_failure_does_not_publish_good_or_success(self):
        self.accepted()
        marker = self.runner.read(self.root / "last-verified-success.json")
        original_sync = self.runner.sync_directory
        def fail_archive_parent(path):
            if path == self.root / "bundles":
                raise OSError("simulated archive directory sync failure")
            original_sync(path)
        with self.mock_validation(), patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "live"), patch.object(self.runner, "sync_directory", side_effect=fail_archive_parent), \
                self.assertRaises(OSError):
            self.runner.verified(self.runner.bundle("issued"), hashlib.sha256(b"leaf").hexdigest())
        self.assertEqual(self.runner.load("good.json"), {"sha256": OLD})
        self.assertEqual(self.runner.read(self.root / "last-verified-success.json"), marker)

    def test_pointer_directory_is_synced_after_atomic_replacement(self):
        original_replace = acme.os.replace
        events = []
        def replace(source, destination):
            original_replace(source, destination)
            events.append("replace")
        original_sync = self.runner.sync_directory
        def sync(path):
            original_sync(path)
            events.append("sync")
        with patch.object(acme.os, "replace", side_effect=replace), patch.object(self.runner, "sync_directory", side_effect=sync):
            self.runner.save("good.json", {"sha256": NEW})
        self.assertEqual(events, ["replace", "sync"])

    def test_retry_client_failure_keeps_rollback_and_does_not_import_again(self):
        self.accepted()
        marker = self.runner.read(self.root / "last-verified-success.json")
        with patch.object(self.runner, "snapshot", return_value=accepted_map()), \
                patch.object(self.runner, "acme", side_effect=[self.runner.bundle("issued"), acme.Failure("retry client failed")]), \
                patch.object(self.runner, "validate", return_value=NEW), patch.object(self.runner, "saved_bundle"), \
                patch.object(self.runner, "live", return_value=OLD), \
                patch.object(self.runner, "import_bundle", side_effect=acme.Failure("first import failed")) as imported:
            with self.assertRaises(acme.Failure):
                self.runner.run()
            previous = self.runner.read(self.root / "previous.json")
            with self.assertRaisesRegex(acme.Failure, "retry client"):
                self.runner.run()
            self.assertEqual(imported.call_count, 1)
            self.assertEqual(self.runner.read(self.root / "previous.json"), previous)
            self.assertEqual(self.runner.read(self.root / "last-verified-success.json"), marker)

    def test_execute_audits_and_locks_mutation(self):
        original_fstat = os.fstat
        def root_fstat(descriptor):
            values = list(original_fstat(descriptor))
            values[4] = 0
            return os.stat_result(values)
        with patch.object(acme.os, "fstat", side_effect=root_fstat), patch.object(self.runner, "issue") as issue:
            self.runner.execute("issue")
            issue.assert_called_once_with()
        with patch.object(self.runner, "snapshot", return_value=baseline()), contextlib.redirect_stdout(io.StringIO()):
            self.runner.execute("inspect")


if __name__ == "__main__":
    unittest.main()
