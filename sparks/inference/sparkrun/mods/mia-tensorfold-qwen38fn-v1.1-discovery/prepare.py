#!/usr/bin/env python3
"""Verify the frozen Mia TensorFold mod and its rebuilt tensorfold-native, which run.sh installs beside the image's."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

# Not resolved: a linked prepare.py must check the bundle it was run from.
HERE = Path(__file__).absolute().parent
LOCK = HERE / "upstream.lock.json"
FROZEN = {"prepare.py", "run.sh", "serve.py", "build/Dockerfile", "build/build.sh"}
BINARY = "out/tensorfold-native"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check(lock, require_binary):
    files = lock["mod_files"]
    patches = {patch["file"] for patch in lock["server_patches"] + lock["site_patches"]}
    if set(files) != FROZEN | patches or {str(p.relative_to(HERE)) for p in (HERE / "patches").iterdir()} != patches:
        raise ValueError("Incomplete Mia TensorFold mod manifest")
    for patch in lock["site_patches"]:
        if files[patch["file"]] != patch["sha256"]:
            raise ValueError(f"{patch['file']} is not the recorded copy of {patch['source']}")
    for name, digest in files.items():
        path = HERE / name
        if path.is_symlink() or not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Missing or changed frozen mod file: {name}")
    binary = HERE / BINARY
    if not binary.exists() and not binary.is_symlink():
        if require_binary:
            raise ValueError(f"Missing rebuilt binary: {BINARY}; run build/build.sh")
        return
    if binary.is_symlink() or not binary.is_file() or sha256(binary) != lock["binary_sha256"]:
        raise ValueError(f"Rebuilt binary does not match the lock: {BINARY}")


def refresh(lock, checkout):
    """Regenerate each server patch from its pinned commit; the binary must then be rebuilt and recorded."""
    for patch in lock["server_patches"]:
        diff = subprocess.run(["git", "-C", checkout, "diff", patch["base"], patch["commit"], "--", *patch["paths"]],
                              check=True, capture_output=True).stdout
        if not diff:
            raise ValueError(f"No change to {', '.join(patch['paths'])} in {patch['commit']}")
        (HERE / patch["file"]).write_bytes(diff)
        lock["mod_files"][patch["file"]] = hashlib.sha256(diff).hexdigest()
    lock["binary_sha256"] = None
    LOCK.write_text(json.dumps(lock, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the frozen files, and the binary when present")
    parser.add_argument("--require-binary", action="store_true", help="Fail when the rebuilt binary is absent")
    parser.add_argument("--refresh", metavar="CHECKOUT", help="Rewrite the server patches from a TensorFold checkout")
    parser.add_argument("--record-binary", action="store_true", help="Write the built binary's sha256 into the lock")
    args = parser.parse_args()
    if args.refresh and args.record_binary:
        parser.error("--record-binary records a built binary: rebuild with build/build.sh --record after --refresh")
    if LOCK.is_symlink() or not LOCK.is_file():
        raise ValueError("Missing or linked upstream.lock.json")
    lock = json.loads(LOCK.read_text())
    if args.refresh:
        refresh(lock, args.refresh)
    if args.record_binary:
        lock["binary_sha256"] = sha256(HERE / BINARY)
        LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    check(lock, args.require_binary)
    print(f"Verified Mia TensorFold mod for {lock['kit_revision']} with "
          f"{len(lock['server_patches']) + len(lock['site_patches'])} server patches")


if __name__ == "__main__":
    main()
