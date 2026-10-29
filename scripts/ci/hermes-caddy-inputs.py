#!/usr/bin/env python3
"""Small shared input scope and artifact checks for the Caddy image workflow."""

import argparse
import json
import re
import subprocess
from pathlib import Path

GUEST = Path("proxmox/guests/hermes-1")
# Only image inputs trigger automatic publication or invalidate its promotion.
# Workflow/qualification changes can be deliberately republished by main dispatch.
PUBLISH_INPUTS = (str(GUEST / "Dockerfile.caddy"), str(GUEST / ".dockerignore"))
# PR qualification is intentionally broader than automatic image publication.
INPUTS = (
    ".github/workflows/hermes-caddy-image.yaml",
    ".github/workflows/repository-validation.yaml",
    ".renovaterc.json5", ".mise.toml", ".shellcheckrc",
    *PUBLISH_INPUTS,
    str(GUEST / "Caddyfile"), str(GUEST / "compose.yaml"),
    str(GUEST / "compose.caddy.yaml"), str(GUEST / ".doco-cd.yml"),
    str(GUEST / "doco-cd"),
    str(GUEST / "caddy"),
    "scripts/ci/hermes-caddy-inputs.py", "scripts/ci/hermes-caddy-promote.sh",
    "scripts/ci/test-hermes-caddy.py", "scripts/ci/test-hermes-caddy-promotion.sh",
    "scripts/ci/test-renovate-caddy.mjs",
)


def relevant(path, publish=False):
    paths = PUBLISH_INPUTS if publish else INPUTS
    return any(path == item or path.startswith(item + "/") for item in paths)


def changed(base, head, publish=False):
    # --no-renames includes both old and new paths for a rename across scope.
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", base, head, "--"],
        check=True, capture_output=True,
    )
    return any(relevant(path, publish) for path in result.stdout.decode().split("\0") if path)


def expected(text):
    bases = re.findall(r"^FROM (caddy:(\d+\.\d+\.\d+)-(?:builder-)?alpine@sha256:[a-f0-9]{64})(?: AS builder)?$", text, re.M)
    if len(bases) != 2 or bases[0][1] != bases[1][1]:
        raise ValueError("builder/runtime must pin the same Caddy release and exact digests")
    plugin = re.findall(r"^ARG CLOUDFLARE_VERSION=(v\d+\.\d+\.\d+)$", text, re.M)
    if len(plugin) != 1:
        raise ValueError("one exact Cloudflare version is required")
    return dict(builder=bases[0][0], runtime=bases[1][0], core="v" + bases[0][1], plugin=plugin[0])


def verify_artifact(expect, report):
    info = (report / "build-info.txt").read_text()
    # A replacement can change linked code while retaining the expected dep line.
    if re.search(r"^\s*=>", info, re.M):
        raise ValueError("untracked Go module replacement")
    deps = dict(re.findall(r"^dep\s+(\S+)\s+(\S+)", info, re.M))
    for module, version in (("github.com/caddyserver/caddy/v2", expect["core"]),
                            ("github.com/caddy-dns/cloudflare", expect["plugin"])):
        if deps.get(module) != version:
            raise ValueError(f"linked module mismatch: {module}")
    go = re.search(r"^go\s+(go\S+)", info, re.M)
    compiler = re.search(r"^build\s+-compiler=(\S+)", info, re.M)
    if not go or not compiler or compiler[1] != "gc":
        raise ValueError("missing Go/compiler build information")
    if (report / "version.txt").read_text().split()[0] != expect["core"]:
        raise ValueError("artifact Caddy CLI version mismatch")
    modules = set(re.findall(r"^[a-z][a-z0-9_.]+$", (report / "modules.txt").read_text(), re.M))
    if modules != {"dns.providers.cloudflare"}:
        raise ValueError("unexpected nonstandard Caddy modules")
    for kind in ("builder", "runtime"):
        base = json.loads((report / f"{kind}.json").read_text())[0]
        env = dict(item.split("=", 1) for item in base["Config"]["Env"])
        if env.get("CADDY_VERSION") != expect["core"]:
            raise ValueError(f"{kind} pinned payload release mismatch")
        if base["Os"] != "linux" or base["Architecture"] != "amd64":
            raise ValueError(f"{kind} platform mismatch")
    if (report / "runtime-version.txt").read_text().split()[0] != expect["core"]:
        raise ValueError("runtime base binary release mismatch")
    if (report / "go-version.txt").read_text().split()[2] != go[1]:
        raise ValueError("artifact compiler differs from pinned builder")
    return dict(core=expect["core"], cloudflare=expect["plugin"], go=go[1], compiler=compiler[1], modules=deps)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scope = sub.add_parser("scope")
    scope.add_argument("base")
    scope.add_argument("head")
    scope.add_argument("--publish", action="store_true")
    paths = sub.add_parser("paths")
    paths.add_argument("--publish", action="store_true")
    sub.add_parser("expected")
    artifact = sub.add_parser("verify")
    artifact.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.command == "paths":
        print("\n".join(PUBLISH_INPUTS if args.publish else INPUTS))
    elif args.command == "scope":
        # Missing history (including a branch-creation push) selects qualification.
        try:
            selected = changed(args.base, args.head, args.publish)
        except subprocess.CalledProcessError:
            selected = True
        print(str(selected).lower())
    else:
        expect = expected((GUEST / "Dockerfile.caddy").read_text())
        if args.command == "expected":
            print("\n".join(f"{key}={value}" for key, value in expect.items()))
        else:
            print(json.dumps(verify_artifact(expect, args.report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
