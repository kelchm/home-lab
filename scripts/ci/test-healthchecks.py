#!/usr/bin/env python3
"""Offline safety checks; never reads a real credential or contacts Healthchecks."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("healthchecks", Path(__file__).parents[1] / "healthchecks.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        declared = {c["slug"]: c for c in hc.load_declaration()}
        self.declaration = [declared[slug] for slug in ("k8s-prod-watchdog", "athena-offsite-backup")]
        self.channels = {"email", "pushover"}
        self.live = []
        for index, want in enumerate(self.declaration):
            self.live.append({**want, "channels": "email,pushover", "status": "up",
                              "uuid": f"check-{index}", "ping_url": f"https://hc-ping.com/check-{index}"})
        self.live.append({**self.live[1], "slug": "live-only", "name": "Live only"})
        self.sent = []
        self.output = io.StringIO()

    def call(self, key, method, path, body=None):
        self.sent.append((method, path, json.loads(body)))
        return 200, copy.deepcopy(next(c for c in self.live if c["slug"] == json.loads(body)["slug"]))

    def apply(self, confirm=None):
        def fetch(_key):
            return copy.deepcopy(self.live), self.channels
        with patch.object(hc, "fetch", fetch), patch.object(hc, "call", self.call), \
                patch("builtins.input", side_effect=confirm or (lambda _: "apply")), \
                contextlib.redirect_stdout(self.output):
            hc.apply("fake-key", self.declaration)

    def test_upserts_preserve_live_only_check(self):
        self.apply()
        self.assertEqual([c["slug"] for c in self.live], [c["slug"] for c in self.declaration] + ["live-only"])
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(method == "POST" for method, _, _ in self.sent))
        self.assertEqual(self.sent[0][1], "checks/check-0")
        self.assertEqual(self.sent[1][1], "checks/")
        self.assertEqual(self.sent[1][2]["unique"], ["slug"])
        self.assertIn("Live only", self.output.getvalue())
        self.assertNotIn("hc-ping.com", self.output.getvalue())

    def test_duplicate_live_slug_sends_nothing(self):
        self.live.append(copy.deepcopy(self.live[0]))
        with self.assertRaises(hc.Refusal):
            self.apply()
        self.assertEqual(self.sent, [])

    def test_new_check_is_created_then_updated_without_duplicate(self):
        want = {"name": "Test", "slug": "test-job", "tags": "test", "desc": "Test check",
                "timeout": 300, "grace": 300, "channels": "*"}
        self.declaration.append(want)
        def upsert(key, method, path, body=None):
            payload = json.loads(body)
            self.sent.append((method, path, payload))
            if payload["slug"] != want["slug"]:
                return 200, next(c for c in self.live if c["slug"] == payload["slug"])
            existing = next((c for c in self.live if c["slug"] == want["slug"]), None)
            if existing:
                return 200, copy.deepcopy(existing)
            created = {**want, "channels": "email,pushover", "status": "new", "uuid": "test-id",
                       "ping_url": "https://hc-ping.com/test-id"}
            self.live.append(created)
            return 201, copy.deepcopy(created)
        with patch.object(self, "call", upsert):
            self.apply()
            self.apply()
        self.assertEqual(len([c for c in self.live if c["slug"] == want["slug"]]), 1)
        self.assertIn("test-job: created", self.output.getvalue())
        self.assertIn("test-job: updated", self.output.getvalue())
        self.assertTrue(all(body["unique"] == ["slug"] for _, _, body in self.sent if body["slug"] == want["slug"]))

    def test_watchdog_missing_down_or_timing_drift_sends_nothing(self):
        original = copy.deepcopy(self.live)
        for change in (lambda: self.live.pop(0), lambda: self.live[0].update(status="down"),
                       lambda: self.live[0].update(timeout=60)):
            with self.subTest(change=change):
                self.live = copy.deepcopy(original)
                change()
                with self.assertRaises(hc.Refusal):
                    self.apply()
                self.assertEqual(self.sent, [])

    def test_cancel_sends_nothing(self):
        with self.assertRaises(hc.Refusal):
            self.apply(lambda _: "no")
        self.assertEqual(self.sent, [])

    def test_settings_change_during_confirmation_sends_nothing(self):
        def confirm(_):
            self.live[1]["desc"] = "changed by another operator"
            return "apply"
        with self.assertRaises(hc.Refusal):
            self.apply(confirm)
        self.assertEqual(self.sent, [])

    def test_replacement_during_confirmation_sends_nothing(self):
        def confirm(_):
            self.live[0].update(uuid="replacement", ping_url="replacement")
            return "apply"
        with self.assertRaises(hc.Refusal):
            self.apply(confirm)
        self.assertEqual(self.sent, [])

    def test_credentials_in_live_descriptions_are_redacted(self):
        key = "fake-credential"
        self.live[0]["desc"] = f"{key} {self.live[0]['ping_url']} {self.live[0]['uuid']}"
        def call(_key, _method, path, body=None):
            if path == "checks/":
                return 200, {"checks": self.live}
            return 200, {"channels": [{"id": c} for c in self.channels]}
        with patch.dict(os.environ, {"HC_API_KEY_REF": "op://test/item/key"}, clear=True), \
                patch.object(hc.subprocess, "run", return_value=Mock(returncode=0, stdout=key)), \
                patch.object(hc, "call", call), contextlib.redirect_stdout(self.output):
            hc.inspect(hc.api_key())
        for secret in (key, self.live[0]["ping_url"], self.live[0]["uuid"]):
            self.assertNotIn(secret, self.output.getvalue())

    def test_invalid_declaration_is_rejected_before_network_access(self):
        for checks in ([self.declaration[0], self.declaration[0]],
                       [{**self.declaration[0], "timeout": 0}],
                       [{**self.declaration[0], "ping_url": "credential"}]):
            with self.subTest(checks=checks), patch.object(Path, "read_text", return_value=json.dumps(checks)):
                with self.assertRaises(hc.Refusal):
                    hc.load_declaration()

    def test_verification_detects_ping_url_status_and_channel_changes(self):
        before = {c["slug"]: c for c in self.live}
        for changes in ({"ping_url": "replacement"}, {"status": "down"}, {"channels": "email"}):
            with self.subTest(changes=changes):
                after = copy.deepcopy(before)
                after["k8s-prod-watchdog"].update(changes)
                self.assertTrue(hc.verify(self.declaration, before, after, self.channels))

    def test_redirects_are_not_followed(self):
        self.assertIsNone(hc.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.org"))


if __name__ == "__main__":
    unittest.main()
