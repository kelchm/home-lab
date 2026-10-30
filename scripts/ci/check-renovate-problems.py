#!/usr/bin/env python3
"""Fail when the Renovate Dashboard lists a repository problem.

The result covers the dashboard's current text and does not show that Renovate
is running. docs/runbooks/dependency-updates.md#renovate-problems covers the
response.

Exits 0 when no problem is listed, 1 when one is, and 2 when the dashboard
could not be read.
"""

import json
from pathlib import Path
import re
import subprocess
import sys

DASHBOARD = "repos/kelchm/home-lab/issues/2"
DRILL_BODY = Path(__file__).parent / "fixtures" / "renovate-dashboard-problem.txt"

# Renovate always renders this heading, with "None detected" when empty.
DASHBOARD_ANCHOR = re.compile(r"^## Detected Dependencies[ \t]*$", re.M)
PROBLEMS = re.compile(r"^## Repository Problems[ \t]*$(.*?)(?=^## |\Z)", re.M | re.S)


class Unreadable(Exception):
    """The dashboard's content is unknown, so no problem result exists."""


def fetch():
    try:
        result = subprocess.run(["gh", "api", DASHBOARD], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unreadable(f"could not run gh: {error}") from error
    if result.returncode != 0:
        raise Unreadable(f"GitHub API request failed: {result.stderr.strip() or result.returncode}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise Unreadable(f"GitHub API returned invalid JSON: {error}") from error


def problems_in(issue):
    try:
        state, body = issue["state"], issue["body"].replace("\r\n", "\n")
    except (AttributeError, KeyError, TypeError) as error:
        raise Unreadable(f"unexpected dashboard response: {error!r}") from error
    if state != "open":
        raise Unreadable(f"dashboard is {state}")
    if not DASHBOARD_ANCHOR.search(body):
        raise Unreadable("issue has no Detected Dependencies section")
    problems = []
    for section in PROBLEMS.findall(body):
        problems += re.findall(r"^\s*- (.+)$", section, re.M) or ["section has no listed problems"]
    return problems


def annotate(title, message):
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title={title}::{escaped}")


def main(argv=None):
    drill = "--drill" in (sys.argv[1:] if argv is None else argv)
    try:
        problems = problems_in({"state": "open", "body": DRILL_BODY.read_text()} if drill else fetch())
    except Unreadable as error:
        annotate("Renovate dashboard unreadable", f"{error}. No problem result is available.")
        return 2
    if not problems:
        print("The Renovate Dashboard lists no repository problems.")
        return 0
    for problem in problems:
        annotate("Renovate repository problem", problem)
    print("Procedure: docs/runbooks/dependency-updates.md#renovate-problems")
    return 1


if __name__ == "__main__":
    sys.exit(main())
