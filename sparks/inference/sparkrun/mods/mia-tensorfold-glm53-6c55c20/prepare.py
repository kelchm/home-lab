#!/usr/bin/env python3
"""Verify the frozen native GLM bundle before installation."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.parse_args()
    here = Path(__file__).absolute().parent
    manifest = here / "upstream.lock.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Missing or linked native TensorFold mod manifest")
    lock = json.loads(manifest.read_text())
    names = {"LICENSE", "NOTICE.md", "build.sh", "prepare.py", "run.sh", "serve.py", "chat_template.jinja"}
    if set(lock["mod_files"]) != names:
        raise ValueError("Incomplete native TensorFold mod manifest")
    for name, digest in lock["mod_files"].items():
        path = here / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Missing or changed frozen mod file: {name}")
    print(f"Verified native TensorFold mod for {lock['kit_revision']}")


if __name__ == "__main__":
    main()
