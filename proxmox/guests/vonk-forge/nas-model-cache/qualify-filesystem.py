#!/usr/bin/env python3
"""Run inside a disposable UID10001 container with the NFS volume at /cache."""
import errno
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time


def contender(path, connection):
    with open(path, 'a+b') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            connection.send(error.errno in (errno.EACCES, errno.EAGAIN))
        else:
            connection.send(False)
    connection.close()


def main():
    if os.getuid() != 10001 or os.getgid() != 10001:
        raise SystemExit('Run as UID/GID10001')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='.acceptance-', dir='/cache/quarantine') as name:
        root = Path(name)
        lock = root/'lock'
        with lock.open('a+b') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            ctx = multiprocessing.get_context('spawn')
            parent, child = ctx.Pipe()
            process = ctx.Process(target=contender, args=(str(lock), child))
            process.start()
            child.close()
            if not parent.poll(15) or parent.recv() is not True:
                raise RuntimeError('Independent process did not respect exclusive lock')
            process.join(15)
            if process.exitcode != 0:
                raise RuntimeError('Lock contender did not terminate successfully')
            fcntl.flock(owner, fcntl.LOCK_UN)
        data = b'vonk-nas-acceptance\n' * 65536
        first = root/'object'
        replacement = root/'replacement'
        first.write_bytes(b'old')
        with first.open('rb') as reader:
            with replacement.open('wb') as writer:
                writer.write(data)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(replacement, first)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            assert reader.read() == b'old'
            assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(data).digest()
        assert first.stat().st_uid == 10001
        capacity = os.statvfs(root)
    print(json.dumps({'passed': True, 'cross_process_flock': True,
                      'file_and_directory_fsync': True, 'atomic_replace_open_reader': True,
                      'sha256_verified': True, 'uid': os.getuid(),
                      'available_bytes': capacity.f_bavail*capacity.f_frsize,
                      'elapsed_seconds': time.monotonic()-started,
                      'scope': 'small physical filesystem probe; not throughput or outage acceptance'}))

if __name__ == '__main__':
    main()
