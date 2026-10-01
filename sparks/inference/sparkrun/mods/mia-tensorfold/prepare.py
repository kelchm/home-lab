#!/usr/bin/env python3
"""Stage the shared readiness gate for SparkRun's self-contained Mia mod."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    lock = json.loads((here / "upstream.lock.json").read_text())
    target = here / "upstream/readiness.py"
    if not args.check:
        source = here.parent / "tensorfold/readiness.py"
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != lock["readiness_sha256"]:
            raise ValueError("Shared readiness gate changed; review and update its pin")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != lock["readiness_sha256"]:
        raise ValueError("Missing or changed readiness gate; run prepare.py")
    print(f"Verified Mia TensorFold mod for {lock['kit_revision']}")


if __name__ == "__main__":
    main()
