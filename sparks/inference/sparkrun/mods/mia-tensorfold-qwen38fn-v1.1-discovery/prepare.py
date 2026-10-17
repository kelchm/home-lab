#!/usr/bin/env python3
"""Verify the frozen Mia TensorFold mod and its rebuilt tensorfold-native; install it over the image's binary."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
LOCK = HERE / "upstream.lock.json"
FROZEN = {"prepare.py", "run.sh", "serve.py", "readiness.py", "build/Dockerfile", "build/build.sh"}
BINARY = "out/tensorfold-native"
IMAGE_BINARY = "opt/tensorfold/native/bin/tensorfold-native"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check(lock, require_binary):
    files = lock["mod_files"]
    patches = {patch["file"] for patch in lock["server_patches"]}
    if set(files) != FROZEN | patches or {str(p.relative_to(HERE)) for p in (HERE / "patches").iterdir()} != patches:
        raise ValueError("Incomplete Mia TensorFold mod manifest")
    for name, digest in files.items():
        path = HERE / name
        if path.is_symlink() or not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Missing or changed frozen mod file: {name}")
    for patch in lock["server_patches"]:
        check_patch_paths(patch, (HERE / patch["file"]).read_bytes())
    binary = HERE / BINARY
    if not binary.exists() and not binary.is_symlink():
        if require_binary:
            raise ValueError(f"Missing rebuilt binary: {BINARY}; run build/build.sh")
        return
    if binary.is_symlink() or not binary.is_file() or sha256(binary) != lock["binary_sha256"]:
        raise ValueError(f"Rebuilt binary does not match the lock: {BINARY}")


def install(lock, root):
    """Replace the image's binary only when it is the published one; an already replaced binary is left alone."""
    target = Path(root) / IMAGE_BINARY
    found = sha256(target)
    if found == lock["binary_sha256"]:
        return
    if found != lock["build"]["published_binary_sha256"]:
        raise ValueError(f"Unexpected tensorfold-native in the image: {found}")
    staged = target.with_name(target.name + ".site")
    shutil.copyfile(HERE / BINARY, staged)
    os.chmod(staged, 0o755)
    os.replace(staged, target)


def check_patch_paths(patch, diff):
    paths = patch["paths"]
    changed = re.findall(rb"^diff --git a/(\S+) b/\1$", diff, re.MULTILINE)
    if not paths or len(set(paths)) != len(paths) or sorted(p.encode() for p in paths) != sorted(changed):
        raise ValueError(f"Patch {patch['file']} must change exactly {paths!r}")


def refresh(lock, checkout):
    """Regenerate each server patch from its pinned commit; the binary must then be rebuilt and recorded."""
    for patch in lock["server_patches"]:
        diff = subprocess.run(["git", "-C", checkout, "diff", patch["base"], patch["commit"], "--", *patch["paths"]],
                              check=True, capture_output=True).stdout
        check_patch_paths(patch, diff)
        (HERE / patch["file"]).write_bytes(diff)
        lock["mod_files"][patch["file"]] = hashlib.sha256(diff).hexdigest()
    lock["binary_sha256"] = None
    LOCK.write_text(json.dumps(lock, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the frozen files, and the binary when present")
    parser.add_argument("--require-binary", action="store_true", help="Fail when the rebuilt binary is absent")
    parser.add_argument("--install", metavar="ROOT", help="Verify, then install the binary under this image root")
    parser.add_argument("--refresh", metavar="CHECKOUT", help="Rewrite the server patches from a TensorFold checkout")
    parser.add_argument("--record-binary", action="store_true", help="Write the built binary's sha256 into the lock")
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text())
    if args.refresh:
        refresh(lock, args.refresh)
    if args.record_binary:
        lock["binary_sha256"] = sha256(HERE / BINARY)
        LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    check(lock, args.require_binary or bool(args.install))
    if args.install:
        install(lock, args.install)
    print(f"Verified Mia TensorFold mod for {lock['kit_revision']} with {len(lock['server_patches'])} server patches")


if __name__ == "__main__":
    main()
