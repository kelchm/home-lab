#!/usr/bin/env python3
"""Offline Renovate problem check cases; the fixtures are trimmed real dashboard bodies."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("check", Path(__file__).parent / "check-renovate-problems.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

FIXTURES = Path(__file__).parent / "fixtures"
HEALTHY = (FIXTURES / "renovate-dashboard-healthy.txt").read_text()
PROBLEM = (FIXTURES / "renovate-dashboard-problem.txt").read_text()
NO_BRANCHES = "This repository currently has no open or pending branches.\n\n## Detected Dependencies\n\nNone detected\n\n"


def issue(body=HEALTHY, state="open"):
    return {"state": state, "body": body}


def gh(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def run_main(response, argv=()):
    output = io.StringIO()
    with patch.object(check.subprocess, "run", **response) as run, contextlib.redirect_stdout(output):
        code = check.main(list(argv))
    return code, output.getvalue(), run


class Dashboard(unittest.TestCase):
    def test_clean_dashboards_list_no_problems(self):
        for body in (HEALTHY, NO_BRANCHES):
            self.assertEqual(run_main({"return_value": gh(json.dumps(issue(body)))})[0], 0)

    def test_problem_dashboard_fails_with_each_problem(self):
        code, output, _ = run_main({"return_value": gh(json.dumps(issue(PROBLEM)))})
        self.assertEqual(code, 1)
        self.assertIn("title=Renovate repository problem::⚠️ WARN: Error obtaining docker token", output)
        self.assertIn("title=Renovate repository problem::⚠️ WARN: Package lookup failures", output)
        self.assertIn("docs/runbooks/dependency-updates.md#renovate-problems", output)

    def test_section_is_found_without_relying_on_emoji_subtitle_or_a_list(self):
        listed = "## Repository Problems\n\nCustom subtitle.\n\n - FATAL: Repository is broken\n\n" + NO_BRANCHES
        self.assertEqual(check.problems_in(issue(listed)), ["FATAL: Repository is broken"])
        unlisted = "## Repository Problems\n\nCustom subtitle.\n\n" + NO_BRANCHES
        self.assertEqual(check.problems_in(issue(unlisted)), ["section has no listed problems"])

    def test_heading_text_elsewhere_is_not_a_problem(self):
        body = HEALTHY.replace("## Open\n", "## Open\n\n - [ ] fix: handle ## Repository Problems heading\n", 1)
        self.assertNotEqual(body, HEALTHY)
        self.assertEqual(check.problems_in(issue(body)), [])


class Unreadable(unittest.TestCase):
    def assert_unreadable(self, response):
        code, output, _ = run_main(response)
        self.assertEqual(code, 2)
        self.assertIn("title=Renovate dashboard unreadable", output)
        self.assertNotIn("lists no repository problems", output)
        return output

    def test_unusable_dashboards_are_not_clean(self):
        cases = {
            "empty body": issue(""),
            "null body": issue(None),
            "other content": issue("Some other issue."),
            "closed": issue(state="closed"),
            "missing fields": {},
            "not an object": [],
        }
        for name, response in cases.items():
            with self.subTest(name):
                self.assert_unreadable({"return_value": gh(json.dumps(response))})

    def test_read_failures_keep_their_reason(self):
        for reason in ("gh: API rate limit exceeded (HTTP 403)", "gh: Bad credentials (HTTP 401)", "gh: Not Found (HTTP 404)"):
            with self.subTest(reason):
                self.assertIn(reason, self.assert_unreadable({"return_value": gh(stderr=f"{reason}\n", returncode=1)}))

    def test_invalid_json_missing_gh_and_timeout_are_unreadable(self):
        self.assert_unreadable({"return_value": gh("<html>")})
        self.assert_unreadable({"side_effect": FileNotFoundError("gh")})
        self.assert_unreadable({"side_effect": subprocess.TimeoutExpired("gh", 60)})

    def test_multiline_error_stays_one_annotation(self):
        output = self.assert_unreadable({"return_value": gh(stderr="first\nsecond\n", returncode=1)})
        self.assertEqual(output.count("\n"), 1)
        self.assertIn("first%0Asecond", output)


class Drill(unittest.TestCase):
    def test_drill_takes_the_problem_path_without_reading_github(self):
        code, output, run = run_main({"return_value": gh()}, ["--drill"])
        self.assertEqual(code, 1)
        self.assertIn("title=Renovate repository problem", output)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
