#!/usr/bin/python3
"""Explicit bootstrap only: bounded official release downloads, never invoked by sync."""
import hashlib
from pathlib import Path
import shutil
import signal
import sys
import tarfile
import time
import urllib.request

NAME = 'vmutils-linux-amd64-v1.152.0'
URL = 'https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v1.152.0/'
ARCHIVE_SHA = '8eee4a98ff1665c60682475e8a8b292b8d718b63a2f023124384dd2f6a220c79'
BINARY_SHA = 'a9fa98b7447b94f6bcf93b1c43fd3192b8f0cb2e8c54940af144692274bf4711'
root = Path(sys.argv[1])
ATTEMPT_SECONDS = 180


def deadline_expired(_signum, _frame):
    raise TimeoutError("download exceeded per-attempt deadline")


signal.signal(signal.SIGALRM, deadline_expired)


def download(name, limit):
    dest = root / name
    for attempt in range(3):
        try:
            signal.setitimer(signal.ITIMER_REAL, ATTEMPT_SECONDS)
            try:
                with urllib.request.urlopen(URL + name, timeout=20) as response, dest.open('wb') as out:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > limit:
                            raise ValueError('download exceeded size bound')
                        out.write(chunk)
                return dest
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
        except Exception:
            dest.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2)


manifest = dict(line.split()[::-1] for line in download(NAME + '_checksums.txt', 4096).read_text().splitlines())
assert manifest[NAME + '.tar.gz'] == ARCHIVE_SHA and manifest['vmagent-prod'] == BINARY_SHA, 'official checksums differ from pin'
archive = download(NAME + '.tar.gz', 160 * 1024 * 1024)
assert hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA, 'archive checksum mismatch'
with tarfile.open(archive) as tar:
    member = tar.getmember('vmagent-prod')
    assert member.isfile() and member.size < 64 * 1024 * 1024, 'invalid binary member'
    with tar.extractfile(member) as source, (root / 'vmagent-prod').open('wb') as out:
        shutil.copyfileobj(source, out)
assert hashlib.sha256((root / 'vmagent-prod').read_bytes()).hexdigest() == BINARY_SHA, 'binary checksum mismatch'
(root / 'vmagent.sha256').write_text(BINARY_SHA + '\n')
