#!/usr/bin/env python3
"""Copy a reviewed object plan between mounted caches; never mint DB receipts.

Run as the cache user with the source mounted read-only. A preliminary copy can
run while services are up: source changes fail verification. Quiesce writers
and revalidate the complete object inventory before the actual cutover.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

CHUNK = 8 * 1024 * 1024


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while data := f.read(CHUNK):
            h.update(data)
    return h.hexdigest()


def copy_object(source, destination, sha, size, verify_destination=True):
    if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
        raise RuntimeError('Source must be a regular, non-symlink file')
    before = source.stat()
    if before.st_size != size:
        raise RuntimeError('Source size mismatch')
    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if destination.is_symlink():
        raise RuntimeError('Destination symlink rejected')
    if destination.exists():
        if destination.stat().st_size != size or (verify_destination and digest(destination) != sha):
            raise RuntimeError('Existing destination is corrupt; preserve for inspection')
        return 'verified_existing' if verify_destination else 'existing_pending_server_verification'
    fd, temporary = tempfile.mkstemp(prefix=f'.import-{sha}-', dir=destination.parent)
    tmp = Path(temporary)
    try:
        h = hashlib.sha256()
        with os.fdopen(fd, 'wb') as out, source.open('rb') as inp:
            os.fchmod(out.fileno(), 0o640)
            while data := inp.read(CHUNK):
                out.write(data)
                h.update(data)
            out.flush()
            os.fsync(out.fileno())
        after = source.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise RuntimeError('Source changed during copy')
        if h.hexdigest() != sha or tmp.stat().st_size != size or (verify_destination and digest(tmp) != sha):
            raise RuntimeError('Source or copied bytes failed canonical hash verification')
        # Atomic, no overwrite: another writer may have published this object.
        try:
            os.link(tmp, destination)
        except FileExistsError:
            if destination.is_symlink() or destination.stat().st_size != size or (verify_destination and digest(destination) != sha):
                raise RuntimeError('Concurrent destination failed verification')
        tmp.unlink()
        dfd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        return 'copied_verified' if verify_destination else 'copied_pending_server_verification'
    finally:
        tmp.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--jobs', type=int, choices=range(1, 5), default=1)
    p.add_argument('--defer-destination-hash', action='store_true',
                   help='Require a separate complete server-side hash pass before use; copy output is not acceptance')
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--destination-root', type=Path, required=True)
    args = p.parse_args()
    if (os.getuid(), os.getgid()) != (10001, 10001):
        raise SystemExit('Run as cache UID/GID10001')
    items = json.loads(args.plan.read_text())['objects']
    seen = set()
    for item in items:
        if set(item) != {'sha256', 'bytes'}:
            raise SystemExit('This tool accepts only CAS objects, not path-based imports or checkpoints')
        sha, size = item['sha256'], item['bytes']
        if not re.fullmatch('[0-9a-f]{64}', sha) or type(size) is not int or size < 0 or sha in seen:
            raise SystemExit('Invalid or duplicate object in plan')
        seen.add(sha)
    orphans = list((args.destination_root / 'objects').glob('*/.import-*'))
    if orphans:
        raise SystemExit(f'{len(orphans)} interrupted import files require inspection before resuming')
    start = time.monotonic()
    total = 0
    def copy_one(item):
        sha, size = item['sha256'], item['bytes']
        relative = Path('objects') / sha[:2] / sha
        t = time.monotonic()
        result = copy_object(args.source_root / relative, args.destination_root / relative, sha, size,
                             verify_destination=not args.defer_destination_hash)
        return {'sha256': sha, 'bytes': size, 'result': result, 'seconds': time.monotonic() - t}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = [pool.submit(copy_one, item) for item in items]
        for i, completed in enumerate(as_completed(pending)):
            result = completed.result()
            total += result['bytes']
            print(json.dumps(dict(result, index=i + 1, count=len(items), total_bytes=total,
                                  elapsed_seconds=time.monotonic() - start)), flush=True)



if __name__ == '__main__':
    main()
