#!/usr/bin/env python3
"""Verify the frozen, self-contained Mia TensorFold mod before transfer or use."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compatibility alias; both modes only verify files")
    parser.parse_args()
    here = Path(__file__).resolve().parent
    manifest = here / "upstream.lock.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Missing or linked Mia TensorFold mod manifest")
    lock = json.loads(manifest.read_text())
    files = lock["mod_files"]
    if set(files) != {"prepare.py", "run.sh", "serve.py", "site_fixes.py", "readiness.py"}:
        raise ValueError("Incomplete Mia TensorFold mod manifest")
    for name, digest in files.items():
        path = here / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Missing or changed frozen mod file: {name}")
    print(f"Verified Mia TensorFold mod for {lock['kit_revision']}")


if __name__ == "__main__":
    main()
