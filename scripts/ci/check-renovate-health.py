#!/usr/bin/env python3
"""Fail when the Renovate Dashboard lists a repository problem or stops being written.

Renovate rewrites the dashboard only when its body changes, so the newest edit
by its bot proves it was running then, while a quiet dashboard proves nothing
by itself. Issue updated_at moves for human edits and comments, so it is not
used. docs/runbooks/dependency-updates.md#renovate-health covers the response.

Exits 0 when healthy, 1 on a finding, and 2 when the state could not be read.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

OWNER, NAME, DASHBOARD = "kelchm", "home-lab", 2
STALE_HOURS = 72
DRILL_BODY = Path(__file__).parent / "fixtures" / "renovate-dashboard-problem.txt"

QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      state
      body
      userContentEdits(first: 100) {
        nodes { editedAt editor { __typename login } }
      }
    }
  }
}
"""

# Renovate always renders this heading, with "None detected" when empty.
DASHBOARD_ANCHOR = re.compile(r"^## Detected Dependencies[ \t]*$", re.M)
PROBLEMS = re.compile(r"^## Repository Problems[ \t]*$(.*?)(?=^## |\Z)", re.M | re.S)


class Unreadable(Exception):
    """The dashboard's state is unknown, which says nothing about Renovate."""


def fetch():
    command = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"owner={OWNER}",
               "-f", f"name={NAME}", "-F", f"number={DASHBOARD}"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unreadable(f"could not run gh: {error}") from error
    if result.returncode != 0:
        raise Unreadable(f"GitHub API request failed: {result.stderr.strip() or result.returncode}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise Unreadable(f"GitHub API returned invalid JSON: {error}") from error


def drill_payload(now):
    """Return a problem dashboard, written just now, to test failure delivery."""
    edit = {"editedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "editor": {"__typename": "Bot", "login": "renovate"}}
    issue = {"state": "OPEN", "body": DRILL_BODY.read_text(), "userContentEdits": {"nodes": [edit]}}
    return {"data": {"repository": {"issue": issue}}}


def renovate_writes(edits):
    writes = []
    for edit in edits:
        editor = edit.get("editor") or {}
        if editor.get("__typename") == "Bot" and editor.get("login") == "renovate":
            writes.append(datetime.fromisoformat(edit["editedAt"].replace("Z", "+00:00")))
    return writes


def inspect(payload, now):
    """Return (problems, last Renovate write or None); raise Unreadable."""
    try:
        issue = payload["data"]["repository"]["issue"]
        state, body = issue["state"], issue["body"].replace("\r\n", "\n")
        writes = renovate_writes(issue["userContentEdits"]["nodes"])
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise Unreadable(f"unexpected dashboard response: {error!r}") from error
    if state != "OPEN":
        raise Unreadable(f"dashboard #{DASHBOARD} is {state}")
    if not DASHBOARD_ANCHOR.search(body):
        raise Unreadable(f"issue #{DASHBOARD} has no Detected Dependencies section")
    problems = []
    for section in PROBLEMS.findall(body):
        problems += re.findall(r"^\s*- (.+)$", section, re.M) or ["section has no listed problems"]
    return problems, max(writes, default=None)


def annotate(title, message):
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title={title}::{escaped}")


def main(argv=None):
    drill = "--drill" in (sys.argv[1:] if argv is None else argv)
    now = datetime.now(timezone.utc)
    try:
        problems, last_write = inspect(drill_payload(now) if drill else fetch(), now)
    except Unreadable as error:
        annotate("Renovate health unknown", f"{error}. This does not show that Renovate is healthy or failing.")
        return 2

    quiet = None
    if last_write is None:
        quiet = f"No Renovate write in the retained edit history of #{DASHBOARD}."
    elif (hours := (now - last_write).total_seconds() / 3600) > STALE_HOURS:
        quiet = f"No Renovate dashboard write since {last_write.isoformat()} ({hours:.0f}h, limit {STALE_HOURS}h)."

    for problem in problems:
        annotate("Renovate repository problem", problem)
    if quiet:
        annotate("Renovate dashboard quiet", f"{quiet} Renovate skips unchanged writes, so confirm in the Mend job list.")
    if problems or quiet:
        print("Procedure: docs/runbooks/dependency-updates.md#renovate-health")
        return 1
    print(f"Renovate healthy: no repository problems, last dashboard write {last_write.isoformat()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
