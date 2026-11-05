#!/usr/bin/env python3
"""Verify this dated GLM bundle; --record updates local file checksums during review."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).absolute().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    lock_path = HERE / 'upstream.lock.json'
    if lock_path.is_symlink() or not lock_path.is_file():
        raise SystemExit('Missing or linked mod lock')
    paths = [p for p in HERE.rglob('*') if '__pycache__' not in p.parts and p != lock_path]
    if any(p.is_symlink() for p in paths):
        raise SystemExit('Linked files or directories are not allowed in a frozen mod')
    hashes = {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(paths) if p.is_file()}
    lock = json.loads(lock_path.read_text())
    if args.record:
        lock['mod_files'] = hashes
        lock_path.write_text(json.dumps(lock, indent=2) + '\n')
    if hashes != lock.get('mod_files'):
        raise SystemExit('Mod files differ from upstream.lock.json')
    print(f"Verified GLM kit {lock['kit_date']} at {lock['kit_revision'][:12]}")


if __name__ == '__main__':
    main()
