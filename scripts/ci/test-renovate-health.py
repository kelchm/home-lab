#!/usr/bin/env python3
"""Offline Renovate health cases; the fixtures are trimmed real dashboard bodies."""

import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("check", Path(__file__).parent / "check-renovate-health.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

FIXTURES = Path(__file__).parent / "fixtures"
HEALTHY = (FIXTURES / "renovate-dashboard-healthy.txt").read_text()
PROBLEM = (FIXTURES / "renovate-dashboard-problem.txt").read_text()
NO_BRANCHES = "This repository currently has no open or pending branches.\n\n## Detected Dependencies\n\nNone detected\n\n"
NOW = datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc)
RENOVATE = {"__typename": "Bot", "login": "renovate"}


def edit(hours_ago, editor=RENOVATE):
    edited = NOW - timedelta(hours=hours_ago)
    return {"editedAt": edited.strftime("%Y-%m-%dT%H:%M:%SZ"), "editor": editor}


def payload(body=HEALTHY, edits=None, state="OPEN"):
    edits = [edit(1)] if edits is None else edits
    return {"data": {"repository": {"issue": {"state": state, "body": body, "userContentEdits": {"nodes": edits}}}}}


def gh(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def run_main(response, argv=()):
    output = io.StringIO()
    with patch.object(check.subprocess, "run", **response) as run, patch.object(check, "datetime", wraps=datetime) as clock, \
            contextlib.redirect_stdout(output):
        clock.now.return_value = NOW
        code = check.main(list(argv))
    return code, output.getvalue(), run


class Dashboard(unittest.TestCase):
    def test_real_clean_dashboard_has_no_problems(self):
        problems, last_write = check.inspect(payload(), NOW)
        self.assertEqual(problems, [])
        self.assertEqual(last_write, NOW - timedelta(hours=1))

    def test_real_problem_dashboard_lists_each_problem(self):
        problems, _ = check.inspect(payload(PROBLEM), NOW)
        self.assertEqual(problems, ["⚠️ WARN: Error obtaining docker token", "⚠️ WARN: Package lookup failures"])

    def test_dashboard_with_no_branches_or_dependencies_is_healthy(self):
        self.assertEqual(check.inspect(payload(NO_BRANCHES), NOW)[0], [])

    def test_section_is_found_without_relying_on_emoji_or_subtitle(self):
        body = "## Repository Problems\n\nCustom subtitle.\n\n - FATAL: Repository is broken\n\n" + NO_BRANCHES
        self.assertEqual(check.inspect(payload(body), NOW)[0], ["FATAL: Repository is broken"])

    def test_section_without_list_still_counts(self):
        body = "## Repository Problems\n\nCustom subtitle.\n\n" + NO_BRANCHES
        self.assertEqual(len(check.inspect(payload(body), NOW)[0]), 1)

    def test_crlf_section_is_found(self):
        body = (PROBLEM).replace("\n", "\r\n")
        self.assertEqual(len(check.inspect(payload(body), NOW)[0]), 2)

    def test_the_words_elsewhere_are_not_a_problem(self):
        body = HEALTHY.replace("## Open\n", "## Open\n\n - [ ] fix: handle ## Repository Problems heading\n", 1)
        self.assertNotEqual(body, HEALTHY)
        self.assertEqual(check.inspect(payload(body), NOW)[0], [])


class Quiet(unittest.TestCase):
    def main(self, edits):
        return run_main({"return_value": gh(json.dumps(payload(edits=edits)))})

    def test_write_inside_the_limit_is_healthy(self):
        code, output, _ = self.main([edit(71)])
        self.assertEqual(code, 0)
        self.assertIn("Renovate healthy", output)

    def test_write_past_the_limit_is_quiet_not_a_proven_miss(self):
        code, output, _ = self.main([edit(73)])
        self.assertEqual(code, 1)
        self.assertIn("title=Renovate dashboard quiet", output)
        self.assertIn("Mend job list", output)

    def test_human_checkbox_edit_is_not_a_renovate_run(self):
        human = {"__typename": "User", "login": "kelchm"}
        self.assertEqual(self.main([edit(1, human), edit(100)])[0], 1)

    def test_another_bot_is_not_renovate(self):
        self.assertEqual(self.main([edit(1, {"__typename": "Bot", "login": "coderabbitai"}), edit(100)])[0], 1)

    def test_deleted_editor_is_skipped(self):
        self.assertEqual(self.main([edit(1, None), edit(2)])[0], 0)

    def test_newest_write_wins_regardless_of_order(self):
        self.assertEqual(self.main([edit(100), edit(1)])[0], 0)

    def test_no_renovate_edit_is_quiet(self):
        code, output, _ = self.main([])
        self.assertEqual(code, 1)
        self.assertIn("No Renovate write", output)


class UnreadableState(unittest.TestCase):
    def assert_unknown(self, response):
        code, output, _ = run_main(response)
        self.assertEqual(code, 2)
        self.assertIn("title=Renovate health unknown", output)
        self.assertNotIn("healthy:", output)
        self.assertNotIn("repository problem", output)
        return output

    def test_unusable_dashboards_are_unknown_never_healthy(self):
        cases = {
            "missing issue": {"data": {"repository": {"issue": None}}},
            "no data": {"data": None},
            "empty body": payload(""),
            "null body": payload(None),
            "other content": payload("Some other issue."),
            "closed": payload(state="CLOSED"),
            "null edits": {"data": {"repository": {"issue": {"state": "OPEN", "body": HEALTHY, "userContentEdits": None}}}},
            "bad timestamp": payload(edits=[{"editedAt": "yesterday", "editor": RENOVATE}]),
        }
        for name, response in cases.items():
            with self.subTest(name):
                self.assert_unknown({"return_value": gh(json.dumps(response))})

    def test_api_failures_are_reported_as_unknown(self):
        cases = {
            "API rate limit exceeded": gh(stderr="gh: API rate limit exceeded\n", returncode=1),
            "Bad credentials": gh(stderr="HTTP 401: Bad credentials\n", returncode=1),
        }
        for reason, response in cases.items():
            with self.subTest(reason):
                self.assertIn(reason, self.assert_unknown({"return_value": response}))

    def test_invalid_json_is_unknown(self):
        self.assert_unknown({"return_value": gh("<html>")})

    def test_missing_gh_and_timeout_are_unknown(self):
        self.assert_unknown({"side_effect": FileNotFoundError("gh")})
        self.assert_unknown({"side_effect": subprocess.TimeoutExpired("gh", 60)})

    def test_multiline_error_stays_one_annotation(self):
        output = self.assert_unknown({"return_value": gh(stderr="first\nsecond\n", returncode=1)})
        self.assertEqual(output.count("\n"), 1)
        self.assertIn("first%0Asecond", output)


class Failure(unittest.TestCase):
    def test_problem_fails_with_procedure(self):
        code, output, _ = run_main({"return_value": gh(json.dumps(payload(PROBLEM)))})
        self.assertEqual(code, 1)
        self.assertEqual(output.count("title=Renovate repository problem"), 2)
        self.assertNotIn("dashboard quiet", output)
        self.assertIn("docs/runbooks/dependency-updates.md#renovate-health", output)

    def test_problem_and_quiet_are_both_reported(self):
        code, output, _ = run_main({"return_value": gh(json.dumps(payload(PROBLEM, [edit(100)])))})
        self.assertEqual(code, 1)
        self.assertIn("repository problem", output)
        self.assertIn("dashboard quiet", output)

    def test_drill_takes_the_problem_path_without_reading_github(self):
        code, output, run = run_main({"return_value": gh()}, ["--drill"])
        self.assertEqual(code, 1)
        self.assertIn("title=Renovate repository problem", output)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
