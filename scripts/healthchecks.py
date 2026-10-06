#!/usr/bin/env python3
"""Preview or apply healthchecks/checks.json against the healthchecks.io project.

Apply upserts each declared check by slug and never deletes. The API key comes
from HC_API_KEY_FILE or `op read "$HC_API_KEY_REF"` and is held only in memory.
Output is limited to managed settings, statuses and HTTP status codes: no ping
URLs, UUIDs, response bodies or exception text.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path

API = "https://healthchecks.io/api/v3/"
DECLARATION = Path(__file__).resolve().parent.parent / "healthchecks" / "checks.json"
TEXT = ("name", "slug", "tags", "desc")
TIMING = ("timeout", "schedule", "tz", "grace")
# Apply must find these up, never creates them and never changes their timing.
PROTECTED = {"k8s-prod-watchdog"}
SECRETS = set()


def safe(text):
    for secret in sorted(SECRETS, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return text


def report(text):
    print(safe(text))


class Refusal(Exception):
    """Carries a message that is safe to print."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # A followed redirect would resend the key to another host.
    def redirect_request(self, *_args, **_kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def load_declaration():
    try:
        checks = json.loads(DECLARATION.read_text())
    except (OSError, ValueError) as error:
        raise Refusal(f"{DECLARATION.name}: {error}") from None
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        raise Refusal(f"{DECLARATION.name}: expected a list of checks")
    seen = set()
    for check in checks:
        slug = check.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9_-]+", slug):
            raise Refusal(f"{DECLARATION.name}: invalid slug")
        if slug in seen:
            raise Refusal(f"{DECLARATION.name}: duplicate slug {slug}")
        seen.add(slug)
        timing = ("timeout",) if "timeout" in check else ("schedule", "tz")
        if set(check) != {*TEXT, *timing, "grace", "channels"}:
            raise Refusal(f"{slug}: fields must be {', '.join(TEXT)}, timeout or schedule with tz, grace, channels")
        numbers = [k for k in TIMING if k in ("timeout", "grace") and k in check]
        if any(type(check[k]) is not int for k in numbers):
            raise Refusal(f"{slug}: timeout and grace are whole seconds")
        if any(not 60 <= check[k] <= 31536000 for k in numbers):
            raise Refusal(f"{slug}: timeout and grace must be between 60 and 31536000 seconds")
        if any(not isinstance(v, str) for k, v in check.items() if k not in numbers):
            raise Refusal(f"{slug}: every field other than timeout and grace is a string")
        if check["channels"] != "*":
            raise Refusal(f'{slug}: channels must be "*"')
    return checks


def api_key():
    try:
        if path := os.environ.get("HC_API_KEY_FILE"):
            key = Path(path).read_text().strip()
        elif ref := os.environ.get("HC_API_KEY_REF"):
            result = subprocess.run(["op", "read", ref], capture_output=True, text=True)
            if result.returncode:
                raise Refusal(f"op read exited with status {result.returncode}")
            key = result.stdout.strip()
        else:
            raise Refusal("set HC_API_KEY_REF to the 1Password reference of the read-write API key")
    except OSError:
        raise Refusal("could not read the API key") from None
    if not key:
        raise Refusal("the API key is empty")
    SECRETS.add(key)
    return key


def call(key, method, path, body=None):
    request = urllib.request.Request(
        API + path,
        method=method,
        data=None if body is None else body.encode(),
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
    try:
        with OPENER.open(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        raise Refusal(f"{method} failed: HTTP {error.code}") from None
    except (OSError, ValueError):
        raise Refusal(f"{method} failed: no usable response") from None


def fetch(key):
    checks = call(key, "GET", "checks/")[1]["checks"]
    for check in checks:
        SECRETS.update(check[k] for k in ("uuid", "ping_url") if check.get(k))
    channels = {channel["id"] for channel in call(key, "GET", "channels/")[1]["channels"]}
    return checks, channels


def managed(check, channels):
    """The declared fields of a live check, in the declaration's shape."""
    timing = ("timeout",) if "timeout" in check else ("schedule", "tz")
    assigned = set(filter(None, check["channels"].split(",")))
    everything = "*" if assigned == channels else f"{len(assigned)} of {len(channels)} project channels"
    return {**{k: check[k] for k in (*TEXT, *timing, "grace")}, "channels": everything}


def survey(key, declaration):
    """Live checks for the declared slugs, the project's channels, and the live-only checks."""
    checks, channels = fetch(key)
    slugs = [check["slug"] for check in checks]
    live = {}
    for want in declaration:
        slug = want["slug"]
        if slugs.count(slug) > 1:
            raise Refusal(f"{slug}: more than one live check has this slug, so an upsert could update either")
        live.update({slug: check for check in checks if check["slug"] == slug})
    return live, channels, [check for check in checks if check["slug"] not in live]


def body(want):
    return json.dumps(want if want["slug"] in PROTECTED else {**want, "unique": ["slug"]})


def refusals(declaration, live):
    problems = []
    for want in declaration:
        slug = want["slug"]
        if slug not in PROTECTED:
            continue
        have = live.get(slug)
        if have is None:
            problems.append(f"{slug}: missing from the project, and apply never creates it")
            continue
        if have["status"] != "up":
            problems.append(f"{slug}: status is {have['status']}, not up")
        if any(have.get(k) != want.get(k) for k in TIMING):
            problems.append(f"{slug}: declared timing differs from live timing")
    return problems


def show_plan(declaration, live, channels, live_only):
    for want in declaration:
        have = live.get(want["slug"])
        if have is None:
            report(f"\n{want['slug']}: CREATE")
        else:
            current = managed(have, channels)
            report(f"\n{want['slug']}: update, status {have['status']}")
            report(f"  live {json.dumps(current)}")
            for k in dict.fromkeys((*current, *want)):
                if current.get(k) != want.get(k):
                    report(f"  change {k}: {json.dumps(current.get(k))} -> {json.dumps(want.get(k))}")
            if current == want:
                report("  settings already match")
        endpoint = "checks/<existing UUID>" if want["slug"] in PROTECTED else "checks/"
        report(f"  POST /api/v3/{endpoint} {body(want)}")
    report("\nLive checks not in the file, left alone:")
    for check in live_only:
        report(f"  {check['slug']} ({check['name']})")
    if not live_only:
        report("  none")


def verify(declaration, before, after, channels):
    failures = []
    for want in declaration:
        slug = want["slug"]
        was, now = before.get(slug), after.get(slug)
        if now is None:
            failures.append(f"{slug}: missing after apply")
            continue
        if managed(now, channels) != want:
            failures.append(f"{slug}: live settings do not match the file")
        if was and (now["ping_url"] != was["ping_url"] or now["uuid"] != was["uuid"]):
            failures.append(f"{slug}: ping URL changed")
        if was and now["status"] != was["status"]:
            failures.append(f"{slug}: status changed from {was['status']} to {now['status']}")
        if slug in PROTECTED and now["status"] != "up":
            failures.append(f"{slug}: status is {now['status']}, not up")
    return failures


def preview(key, declaration):
    live, channels, live_only = survey(key, declaration)
    show_plan(declaration, live, channels, live_only)
    for problem in refusals(declaration, live):
        report(f"\napply would refuse: {problem}")


def apply(key, declaration):
    planned, channels, live_only = survey(key, declaration)
    show_plan(declaration, planned, channels, live_only)
    if problems := refusals(declaration, planned):
        raise Refusal("nothing sent\n  " + "\n  ".join(problems))
    if input("\nType apply to send these requests: ") != "apply":
        raise Refusal("nothing sent")

    before, channels_now, _ = survey(key, declaration)
    settings = lambda live, all_channels: {
        s: (managed(c, all_channels), c["uuid"], c["ping_url"], c["status"]) for s, c in live.items()
    }
    if settings(before, channels_now) != settings(planned, channels) or refusals(declaration, before):
        raise Refusal("live checks changed since the plan was shown; nothing sent")

    for want in declaration:
        slug = want["slug"]
        # Address Watchdog directly: if removed concurrently, fail instead of recreating it.
        path = f"checks/{before[slug]['uuid']}" if slug in PROTECTED else "checks/"
        status, _ = call(key, "POST", path, body(want))
        expected = 200 if slug in before else 201
        if status != expected:
            raise Refusal(f"{slug}: expected HTTP {expected} but got {status}; stopped without verifying")
        report(f"{slug}: {'updated' if status == 200 else 'created'}")

    after, channels, _ = survey(key, declaration)
    if failures := verify(declaration, before, after, channels):
        raise Refusal("verification failed\n  " + "\n  ".join(failures))
    report("Verified: settings match the file, and existing checks kept their ping URL and status.")


def inspect(key):
    checks, channels = fetch(key)
    report(json.dumps([managed(check, channels) for check in checks], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", nargs="?", default="preview", choices=("preview", "inspect", "apply"))
    mode = parser.parse_args().mode
    if mode == "inspect":
        inspect(api_key())
    else:
        declaration = load_declaration()
        {"preview": preview, "apply": apply}[mode](api_key(), declaration)


if __name__ == "__main__":
    try:
        main()
    except Refusal as refusal:
        sys.exit(safe(f"healthchecks: {refusal}"))
    except (EOFError, KeyboardInterrupt):
        sys.exit("\nhealthchecks: interrupted")
    except Exception as error:
        # The message could quote a response, so only the location is printed.
        frame = traceback.extract_tb(error.__traceback__)[-1]
        sys.exit(f"healthchecks: unexpected {type(error).__name__} at {Path(frame.filename).name}:{frame.lineno}")
