#!/usr/bin/env python3
"""Verify prefix080 inputs, apply in a disposable copy, and run native host checkpoint gates."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv: list[str], cwd: Path, log: str | None = None) -> None:
    print(f"+ {shlex.join(argv)}", flush=True)
    result = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(result.stdout, end="", flush=True)
    if log:
        (cwd / log).write_text(result.stdout)
    result.check_returncode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Read-only integrated Mia108 through prefix060")
    parser.add_argument("--zig", type=Path, required=True, help="Zig 0.17 executable")
    parser.add_argument("--work-dir", type=Path, help="New scratch directory, never an existing checkout")
    args = parser.parse_args()
    source, zig = args.source.resolve(), args.zig.resolve()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / "source-manifest.json").read_text())
    patch = here.parent.parent / "patches" / manifest["patch"]
    if sha256(patch) != manifest["patch_sha256"]:
        raise SystemExit("Checkpoint restore patch differs from source-manifest.json")
    for entry in manifest["files"]:
        path, expected = source / entry["path"], entry["before_sha256"]
        if expected is None:
            if path.exists():
                raise SystemExit(f"Expected unpatched input: {path} already exists")
        elif not path.is_file() or sha256(path) != expected:
            raise SystemExit(f"Input differs at {path}; use the documented integrated base")
    for entry in manifest["required_inputs"]:
        path = source / entry["path"]
        if not path.is_file() or sha256(path) != entry["sha256"]:
            raise SystemExit(f"Checkpoint primitive differs at {path}")
    work = args.work_dir.resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="glm-checkpoint080-")) / "engine"
    if work == source or work.is_relative_to(source) or source.is_relative_to(work) or work.exists():
        raise SystemExit("Scratch must be new and separate from the read-only source")
    print(f"Scratch retained at {work}", flush=True)
    shutil.copytree(source, work, ignore=shutil.ignore_patterns(".git", ".zig-cache", "zig-out", "node_modules"))
    run(["git", "apply", "--check", str(patch)], work)
    run(["git", "apply", str(patch)], work)
    for entry in manifest["files"]:
        if sha256(work / entry["path"]) != entry["after_sha256"]:
            raise SystemExit(f"Patched hash differs at {entry['path']}")
    run([str(zig), "fmt", "--check", *[entry["path"] for entry in manifest["files"]]], work)
    run([str(zig), "build", "test-checkpoint-restore-host", "test-checkpoint-bounded-host", "-Dcpu=apple_m1", "--summary", "all"], work, "checkpoint-host.log")
    print(f"Verified patch SHA-256: {manifest['patch_sha256']}")


if __name__ == "__main__":
    main()
