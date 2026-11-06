#!/usr/bin/env python3
"""Apply prefix050 in a fresh copy and run the real Zig checkpoint host target."""

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


def run(argv: list[str], cwd: Path) -> None:
    print(f"+ {shlex.join(argv)}", flush=True)
    subprocess.run(argv, cwd=cwd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Prepared Mia108 plus pinned-download backport; read only")
    parser.add_argument("--zig", type=Path, required=True, help="Zig 0.17 executable")
    parser.add_argument("--work-dir", type=Path, help="New scratch directory; must not already exist")
    args = parser.parse_args()
    source, zig = args.source.resolve(), args.zig.resolve()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / "source-manifest.json").read_text())
    patch = here.parent.parent / "patches" / manifest["patch"]
    if sha256(patch) != manifest["patch_sha256"]:
        raise SystemExit("Checkpoint patch hash differs from source-manifest.json")
    for entry in manifest["files"]:
        path = source / entry["path"]
        expected = entry["before_sha256"]
        if expected is None:
            if path.exists():
                raise SystemExit(f"Expected an unpatched source: {path} already exists")
        elif not path.is_file() or sha256(path) != expected:
            raise SystemExit(f"Prepared base differs at {path}; use the documented base or run Zig on an already composed tree")
    if args.work_dir:
        work = args.work_dir.resolve()
    else:
        parent = Path(tempfile.mkdtemp(prefix="glm-checkpoint050-"))
        work = parent / "engine"
    if work == source or work.is_relative_to(source) or source.is_relative_to(work):
        raise SystemExit("Scratch and source directories must be separate")
    if work.exists():
        raise SystemExit(f"Refusing to overwrite existing scratch directory: {work}")
    print(f"Scratch retained at {work}", flush=True)
    shutil.copytree(source, work, ignore=shutil.ignore_patterns(".git", ".zig-cache", "zig-out", "node_modules"))
    run(["git", "apply", "--check", str(patch)], work)
    run(["git", "apply", str(patch)], work)
    for entry in manifest["files"]:
        if sha256(work / entry["path"]) != entry["after_sha256"]:
            raise SystemExit(f"Patched source differs at {entry['path']}")
    run([str(zig), "fmt", "--check", *[entry["path"] for entry in manifest["files"]]], work)
    run([str(zig), "build", "test-checkpoint-bounded-host", "--summary", "all"], work)


if __name__ == "__main__":
    main()
