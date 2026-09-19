#!/usr/bin/env python3
"""Stream exactly one regular file object over binary stdin/stdout.

send writes source bytes then a fixed success trailer only after identity and
SHA-256 verification. receive consumes SIZE bytes plus that trailer into
ROOT/objects/<sha[:2]>/<sha> via a private temp and an atomic no-overwrite
hardlink. Paths and {sha256, bytes} are CLI metadata; this utility does not
speak SSH, listen, or write DB receipts.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import glob
import hmac
import json
import math
import os
import re
import signal
import stat
import sys
import tempfile
import time

try:
    from typing import BinaryIO, Dict, Iterable, List, Optional, Tuple
except ImportError:  # pragma: no cover
    BinaryIO = object  # type: ignore
    Dict = dict  # type: ignore
    Iterable = object  # type: ignore
    List = list  # type: ignore
    Optional = object  # type: ignore
    Tuple = tuple  # type: ignore


SUCCESS_TRAILER = b"\n--STREAM-OBJECT-SUCCESS-v1--\n"
CHUNK_SIZE = 256 * 1024
DEFAULT_MAX_MBPS = 20.0
DECIMAL_BYTES_PER_MB = 1000000.0
RECEIVE_UID = 10001
RECEIVE_GID = 10001
OBJECT_MODE = 0o640
DIR_MODE = 0o750
TEMP_PREFIX = ".import-"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StreamObjectError(Exception):
    """Expected failure: no success trailer / no JSON result, non-zero CLI exit."""


class _RateLimiter(object):
    """Average throttle using decimal MB/s (1 MB = 1e6 bytes), not MiB/s."""

    def __init__(self, max_mbps):
        # type: (float) -> None
        self.max_bps = max_mbps * DECIMAL_BYTES_PER_MB
        self.start = time.monotonic()
        self.sent = 0

    def note(self, n):
        # type: (int) -> None
        self.sent += n
        if self.max_bps <= 0:
            return
        required = self.sent / self.max_bps
        delay = required - (time.monotonic() - self.start)
        if delay > 0:
            time.sleep(delay)


def _file_ident(st):
    # type: (os.stat_result) -> Tuple
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _is_within(path, root):
    # type: (str, str) -> bool
    path = os.path.abspath(path)
    root = os.path.abspath(root)
    try:
        common = os.path.commonpath([path, root])
    except ValueError:
        return False
    return common == root


def _validate_sha256(value):
    # type: (object) -> str
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise StreamObjectError("sha256 must be 64 lowercase hex characters")
    return value


def _validate_size(size):
    # type: (object) -> int
    if isinstance(size, bool):
        raise StreamObjectError("bytes must be a non-negative integer")
    try:
        n = int(size)
    except (TypeError, ValueError):
        raise StreamObjectError("bytes must be a non-negative integer")
    if isinstance(size, float) and n != size:
        raise StreamObjectError("bytes must be a non-negative integer")
    if n < 0:
        raise StreamObjectError("bytes must be a non-negative integer")
    return n


def _validate_max_mbps(value):
    # type: (object) -> float
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise StreamObjectError("max-mbps must be a positive finite number")
    if not math.isfinite(n) or n <= 0:
        raise StreamObjectError("max-mbps must be a positive finite number")
    return n


def _require_abs_path(path, label):
    # type: (str, str) -> str
    if not isinstance(path, str) or not path or "\x00" in path:
        raise StreamObjectError("{} is unsafe".format(label))
    if not os.path.isabs(path):
        raise StreamObjectError("{} must be an absolute path".format(label))
    return os.path.abspath(os.path.normpath(path))


def _open_flags(*flags):
    # type: (*int) -> int
    extra = 0
    if hasattr(os, "O_CLOEXEC"):
        extra |= os.O_CLOEXEC
    for flag in flags:
        extra |= flag
    return extra


def _require_existing_dir_not_symlink(path, label):
    # type: (str, str) -> os.stat_result
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise StreamObjectError("{} does not exist".format(label)) from exc
    if stat.S_ISLNK(st.st_mode):
        raise StreamObjectError("{} is a symlink".format(label))
    if not stat.S_ISDIR(st.st_mode):
        raise StreamObjectError("{} is not a directory".format(label))
    return st


def _ensure_dir_not_symlink(path, label, create_mode=None):
    # type: (str, str, Optional[int]) -> None
    if os.path.lexists(path):
        st = _require_existing_dir_not_symlink(path, label)
        if create_mode is not None and stat.S_IMODE(st.st_mode) & 0o777 != create_mode:
            raise StreamObjectError("existing directory has unexpected permissions")
        return
    if create_mode is None:
        raise StreamObjectError("{} does not exist".format(label))
    try:
        os.mkdir(path, create_mode)
    except FileExistsError:
        st = _require_existing_dir_not_symlink(path, label)
        if create_mode is not None and stat.S_IMODE(st.st_mode) & 0o777 != create_mode:
            raise StreamObjectError("concurrent directory has unexpected permissions")
        return
    except OSError as exc:
        raise StreamObjectError("failed to create {}: {}".format(label, exc)) from exc
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise StreamObjectError("{} is a symlink".format(label))
    if not stat.S_ISDIR(st.st_mode):
        raise StreamObjectError("{} is not a directory".format(label))
    # mkdir inherits the NAS parent setgid bit. chmod(0750) would strip it,
    # breaking native administrators-group inheritance on later descendants.
    if stat.S_IMODE(st.st_mode) & 0o777 != create_mode:
        try:
            os.rmdir(path)  # Only the new empty directory; preserve concurrent contents.
        except OSError:
            pass
        raise StreamObjectError("umask prevented required directory permissions")


def _fsync_dir(path):
    # type: (str) -> None
    flags = _open_flags(os.O_RDONLY)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_fd(fd, data):
    # type: (int, bytes) -> None
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise StreamObjectError("short write to temporary file")
        offset += written


def _write_stream(stream, data):
    # type: (BinaryIO, bytes) -> None
    remaining = data
    while remaining:
        written = stream.write(remaining)
        if not written:
            raise StreamObjectError("short write to stdout")
        remaining = remaining[written:]


def _read_exactly(stream, n):
    # type: (BinaryIO, int) -> bytes
    chunks = []
    got = 0
    while got < n:
        chunk = stream.read(n - got)
        if not chunk:
            break
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def _resolve_source(source, source_roots):
    # type: (str, Iterable[str]) -> str
    roots = list(source_roots or [])
    if not roots:
        raise StreamObjectError("at least one --source-root is required")
    source = _require_abs_path(source, "source")
    try:
        st = os.lstat(source)
    except OSError as exc:
        raise StreamObjectError("cannot stat source") from exc
    if stat.S_ISLNK(st.st_mode):
        raise StreamObjectError("source is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise StreamObjectError("source is not a regular file")
    parent = os.path.dirname(source)
    try:
        physical = os.path.join(os.path.realpath(parent), os.path.basename(source))
    except OSError as exc:
        raise StreamObjectError("cannot resolve source parent") from exc
    resolved_roots = []
    for root in roots:
        root_abs = _require_abs_path(root, "source-root")
        if not os.path.isdir(root_abs):
            raise StreamObjectError("source-root is not a directory")
        resolved_roots.append(os.path.realpath(root_abs))
    if not any(_is_within(physical, root) for root in resolved_roots):
        raise StreamObjectError("source is outside allowed roots")
    return physical


def _prepare_destination(destination_root, sha256):
    # type: (str, str) -> Tuple[str, str]
    root = _require_abs_path(destination_root, "destination-root")
    _require_existing_dir_not_symlink(root, "destination-root")
    objects_path = os.path.join(root, "objects")
    _ensure_dir_not_symlink(objects_path, "objects", create_mode=DIR_MODE)
    orphans = glob.glob(os.path.join(objects_path, "*", ".import-*"))
    orphans += glob.glob(os.path.join(objects_path, "*", ".stream-import-*"))
    if orphans:
        raise StreamObjectError("interrupted import files require inspection before resuming")
    prefix_path = os.path.join(objects_path, sha256[:2])
    _ensure_dir_not_symlink(prefix_path, "object prefix", create_mode=DIR_MODE)
    dest_path = os.path.join(prefix_path, sha256)
    if not _is_within(os.path.abspath(dest_path), root):
        raise StreamObjectError("destination path escapes destination-root")
    if not _is_within(os.path.abspath(prefix_path), root):
        raise StreamObjectError("prefix path escapes destination-root")
    if os.path.lexists(dest_path) and stat.S_ISLNK(os.lstat(dest_path).st_mode):
        raise StreamObjectError("destination is a symlink")
    return dest_path, prefix_path


def _verify_existing_object(path, sha256, size):
    # type: (str, str, int) -> None
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise StreamObjectError("existing destination is unreadable") from exc
    if stat.S_ISLNK(st.st_mode):
        raise StreamObjectError("existing destination is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise StreamObjectError("existing destination is not a regular file")
    if st.st_size != size:
        raise StreamObjectError("existing destination size mismatch")
    fd = os.open(path, _open_flags(os.O_RDONLY, os.O_NOFOLLOW))
    try:
        hasher = hashlib.sha256()
        remaining = size
        while remaining > 0:
            chunk = os.read(fd, min(CHUNK_SIZE, remaining))
            if not chunk:
                raise StreamObjectError("existing destination truncated")
            hasher.update(chunk)
            remaining -= len(chunk)
        extra = os.read(fd, 1)
        if extra:
            raise StreamObjectError("existing destination larger than declared size")
        digest = hasher.hexdigest()
        if not hmac.compare_digest(digest, sha256):
            raise StreamObjectError("existing destination sha256 mismatch")
    finally:
        os.close(fd)


def _require_receive_identity():
    # type: () -> None
    if os.getuid() != RECEIVE_UID or os.getgid() != RECEIVE_GID:
        raise StreamObjectError(
            "receive must run as uid {} gid {}".format(RECEIVE_UID, RECEIVE_GID)
        )


def send_object(source, sha256, size, stdout, source_roots, max_mbps=DEFAULT_MAX_MBPS):
    # type: (str, str, int, BinaryIO, Iterable[str], float) -> None
    """Write source bytes then SUCCESS_TRAILER to stdout. No trailer on error."""
    sha256 = _validate_sha256(sha256)
    size = _validate_size(size)
    max_mbps = _validate_max_mbps(max_mbps)
    physical = _resolve_source(source, source_roots)
    try:
        fd = os.open(physical, _open_flags(os.O_RDONLY, os.O_NOFOLLOW))
    except OSError as exc:
        raise StreamObjectError("failed to open source") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise StreamObjectError("source is not a regular file")
        if st.st_size != size:
            raise StreamObjectError("source size mismatch")
        ident = _file_ident(st)
        hasher = hashlib.sha256()
        limiter = _RateLimiter(max_mbps)
        remaining = size
        while remaining > 0:
            chunk = os.read(fd, min(CHUNK_SIZE, remaining))
            if not chunk:
                raise StreamObjectError("source truncated while reading")
            hasher.update(chunk)
            _write_stream(stdout, chunk)
            limiter.note(len(chunk))
            remaining -= len(chunk)
        if hasattr(stdout, "flush"):
            stdout.flush()
        digest = hasher.hexdigest()
        if not hmac.compare_digest(digest, sha256):
            raise StreamObjectError("source sha256 mismatch")
        if _file_ident(os.fstat(fd)) != ident:
            raise StreamObjectError("source changed during read (fstat)")
        path_st = os.lstat(physical)
        if stat.S_ISLNK(path_st.st_mode) or not stat.S_ISREG(path_st.st_mode):
            raise StreamObjectError("source is not a regular file")
        if _file_ident(path_st) != ident:
            raise StreamObjectError("source changed during read (pathstat)")
        _write_stream(stdout, SUCCESS_TRAILER)
        if hasattr(stdout, "flush"):
            stdout.flush()
    finally:
        os.close(fd)


def receive_object(destination_root, sha256, size, stdin, stdout):
    # type: (str, str, int, BinaryIO, BinaryIO) -> Dict
    """Consume SIZE bytes + trailer from stdin and publish under destination-root.

    Does not enforce uid/gid 10001; the CLI wrapper does. Returns the JSON result
    dict after writing it to stdout.
    """
    sha256 = _validate_sha256(sha256)
    size = _validate_size(size)
    dest_path, prefix_path = _prepare_destination(destination_root, sha256)
    started = time.monotonic()
    tmp_fd = None  # type: Optional[int]
    tmp_path = None  # type: Optional[str]
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix="{}{}-".format(TEMP_PREFIX, sha256),
            dir=prefix_path,
        )
        os.fchmod(tmp_fd, OBJECT_MODE)
        hasher = hashlib.sha256()
        remaining = size
        while remaining > 0:
            chunk = stdin.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                raise StreamObjectError("truncated stream")
            hasher.update(chunk)
            _write_fd(tmp_fd, chunk)
            remaining -= len(chunk)
        trailer = _read_exactly(stdin, len(SUCCESS_TRAILER))
        if trailer != SUCCESS_TRAILER:
            raise StreamObjectError("missing or invalid success trailer")
        extra = stdin.read(1)
        if extra:
            raise StreamObjectError("trailing bytes after success trailer")
        digest = hasher.hexdigest()
        if not hmac.compare_digest(digest, sha256):
            raise StreamObjectError("received sha256 mismatch")
        st_fd = os.fstat(tmp_fd)
        if not stat.S_ISREG(st_fd.st_mode):
            raise StreamObjectError("temporary file is not a regular file")
        os.fsync(tmp_fd)
        st_path = os.lstat(tmp_path)
        if stat.S_ISLNK(st_path.st_mode) or not stat.S_ISREG(st_path.st_mode):
            raise StreamObjectError("temporary file is not a regular file")
        if _file_ident(st_fd)[:3] != (st_path.st_dev, st_path.st_ino, st_path.st_size):
            raise StreamObjectError("temporary file was replaced")
        os.close(tmp_fd)
        tmp_fd = None
        try:
            os.link(tmp_path, dest_path)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise StreamObjectError("failed to publish object") from exc
            _verify_existing_object(dest_path, sha256, size)
        _fsync_dir(prefix_path)
        os.unlink(tmp_path)
        tmp_path = None
        _fsync_dir(prefix_path)
        result = {
            "sha256": sha256,
            "bytes": size,
            "time": time.monotonic() - started,
            "verified": True,
        }
        _write_stream(
            stdout,
            (json.dumps(result, separators=(",", ":")) + "\n").encode("ascii"),
        )
        if hasattr(stdout, "flush"):
            stdout.flush()
        return result
    except StreamObjectError:
        raise
    except OSError as exc:
        raise StreamObjectError("receive failed") from exc
    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _arg_sha256(value):
    # type: (str) -> str
    if not _SHA256_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("must be 64 lowercase hex characters")
    return value


def _arg_nonneg_int(value):
    # type: (str) -> int
    try:
        n = int(value, 10)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    if n < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return n


def _arg_positive_float(value):
    # type: (str) -> float
    try:
        n = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    if not math.isfinite(n) or n <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return n


def _build_parser():
    # type: () -> argparse.ArgumentParser
    parser = argparse.ArgumentParser(
        prog="stream-object.py",
        description="Stream one regular object over stdin/stdout with a hashed trailer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    send_p = sub.add_parser("send", help="emit one source object then a success trailer")
    send_p.add_argument("--source", required=True, help="absolute physical source file")
    send_p.add_argument("--sha256", required=True, type=_arg_sha256)
    send_p.add_argument("--bytes", required=True, type=_arg_nonneg_int, dest="nbytes")
    send_p.add_argument(
        "--max-mbps",
        type=_arg_positive_float,
        default=DEFAULT_MAX_MBPS,
        help="average throttle in decimal MB/s (default 20)",
    )
    send_p.add_argument(
        "--source-root",
        required=True,
        action="append",
        dest="source_roots",
        help="allowed absolute root (repeatable); source must resolve inside one",
    )

    recv_p = sub.add_parser("receive", help="consume one object into destination-root")
    recv_p.add_argument("--destination-root", required=True)
    recv_p.add_argument("--sha256", required=True, type=_arg_sha256)
    recv_p.add_argument("--bytes", required=True, type=_arg_nonneg_int, dest="nbytes")
    return parser


def install_signal_handlers():
    def interrupted(signum, frame):
        raise StreamObjectError("interrupted by signal {}".format(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    parser = _build_parser()
    args = parser.parse_args(argv)
    install_signal_handlers()
    try:
        if args.command == "send":
            send_object(
                source=args.source,
                sha256=args.sha256,
                size=args.nbytes,
                stdout=sys.stdout.buffer,
                source_roots=args.source_roots,
                max_mbps=args.max_mbps,
            )
            return 0
        if args.command == "receive":
            _require_receive_identity()
            receive_object(
                destination_root=args.destination_root,
                sha256=args.sha256,
                size=args.nbytes,
                stdin=sys.stdin.buffer,
                stdout=sys.stdout.buffer,
            )
            return 0
        raise StreamObjectError("unknown command")
    except StreamObjectError as exc:
        sys.stderr.write("stream-object: {}\n".format(exc))
        return 1
    except BrokenPipeError:
        try:
            sys.stderr.write("stream-object: broken pipe\n")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
