#!/usr/bin/env python3
"""Verify the frozen dsv41 TensorFold mod against upstream.lock.json; --record rewrites its file checksums."""
import argparse
import hashlib
import json
from pathlib import Path

# Not resolved: a linked prepare.py must check the bundle it was run from.
HERE = Path(__file__).absolute().parent
LOCK = HERE / "upstream.lock.json"


def files():
    return sorted(str(p.relative_to(HERE)) for p in HERE.rglob("*")
                  if (p.is_file() or p.is_symlink()) and p != LOCK and "__pycache__" not in p.parts)


def digest(name):
    return hashlib.sha256((HERE / name).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only, the default")
    parser.add_argument("--record", action="store_true", help="record the checksums of the files present")
    args = parser.parse_args()
    if LOCK.is_symlink() or not LOCK.exists():
        raise SystemExit("Missing or linked dsv41 mod lock")
    lock = json.loads(LOCK.read_text())
    if args.record:
        if any((HERE / name).is_symlink() for name in files()):
            raise SystemExit("Cannot record a dsv41 mod containing linked files")
        lock["mod_files"] = {name: digest(name) for name in files()}
        LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    if "mod_files" not in lock:
        raise SystemExit("The dsv41 mod lock records no mod files")
    if set(files()) != set(lock["mod_files"]):
        raise SystemExit(f"Mod files differ from the lock: {sorted(set(files()) ^ set(lock['mod_files']))}")
    for name, expected in lock["mod_files"].items():
        if (HERE / name).is_symlink() or digest(name) != expected:
            raise SystemExit(f"Missing or changed frozen mod file: {name}")
    print(f"Verified dsv41 TensorFold mod for engine {lock['engine_revision'][:12]}, image {lock['image']}")


if __name__ == "__main__":
    main()
