#!/usr/bin/env python3
"""Verify the frozen Qwen3.8-Flash-Next TensorFold mod against upstream.lock.json; --record rewrites its file checksums."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / "upstream.lock.json"


def files():
    return sorted(str(p.relative_to(HERE)) for p in HERE.rglob("*")
                  if p.is_file() and p != LOCK and "__pycache__" not in p.parts)


def digest(name):
    return hashlib.sha256((HERE / name).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only, the default")
    parser.add_argument("--record", action="store_true", help="record the checksums of the files present")
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text())
    if args.record:
        lock["mod_files"] = {name: digest(name) for name in files()}
        LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    if set(files()) != set(lock["mod_files"]):
        raise SystemExit(f"Mod files differ from the lock: {sorted(set(files()) ^ set(lock['mod_files']))}")
    for name, expected in lock["mod_files"].items():
        if (HERE / name).is_symlink() or digest(name) != expected:
            raise SystemExit(f"Missing or changed frozen mod file: {name}")
    print(f"Verified Qwen3.8-Flash-Next TensorFold mod for engine {lock['engine_revision'][:12]}, image {lock['image']}")


if __name__ == "__main__":
    main()
