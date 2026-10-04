"""Read-only HF-compatible HTTP server over an archive.

It answers only from manifests, refs and blobs on disk. It has no HTTP client, holds no token, takes no
locks and writes nothing, so the archive can be mounted read-only. Routes:

    GET        /healthz
    GET        /api/models/{repo}                       model info at `main`
    GET        /api/models/{repo}/revision/{revision}   model info
    GET        /api/models/{repo}/refs
    GET        /api/models/{repo}/tree/{revision}[/{path}]
    POST       /api/models/{repo}/paths-info/{revision}
    GET, HEAD  /{repo}/resolve/{revision}/{path}

Responses never carry a Xet hash or Xet headers: a client that saw one would fetch from upstream directly.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .errors import ArchiveError, InvalidDocument
from .model import Manifest, TreeFile, validate_repo_id, validate_repo_path
from .store import Digests, Store, hash_stream, matches

log = logging.getLogger("hf_archive.server")

MAX_BODY = 1024 * 1024
# How long a request waits for a first-use blob hash before answering 503 with Retry-After.
VERIFY_WAIT = 2.0
RETRY_AFTER = 2
# Pause between background verification passes over the archive.
WARM_INTERVAL = 30.0
_API_ACTIONS = ("revision", "refs", "tree", "paths-info")
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


class HubError(Exception):
    """An error response in the shape `huggingface_hub` maps to its own exception types."""

    def __init__(self, status: int, code: str, message: str, commit: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.commit = status, code, message, commit


@dataclass
class Response:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    blob: BinaryIO | None = None
    offset: int = 0
    length: int = 0


def _json(doc: Any, status: int = 200) -> Response:
    return Response(status, {"Content-Type": "application/json"}, json.dumps(doc).encode())


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return the inclusive byte range to send, or None to send everything. Raises 416 if unsatisfiable."""
    match = _RANGE_RE.fullmatch(header.strip()) if header else None
    if match is None or match.groups() == ("", ""):
        return None
    first, last = match.groups()
    if size == 0:
        raise HubError(416, "RangeNotSatisfiable", f"range {header} of an empty file")
    if first == "":
        if int(last) == 0:
            raise HubError(416, "RangeNotSatisfiable", f"range {header} of a {size}-byte file")
        return max(size - int(last), 0), size - 1
    start = int(first)
    if start >= size:
        raise HubError(416, "RangeNotSatisfiable", f"range {header} of a {size}-byte file")
    end = min(int(last), size - 1) if last else size - 1
    return (start, end) if end >= start else None


class Archive:
    """Read side of a store, with the per-process memory a server needs."""

    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.Lock()
        self._manifests: dict[Path, tuple[tuple[int, ...], Manifest]] = {}
        # Outcome of the last hash of each blob, keyed to the file it was taken from. None: it could not be read.
        self._digests: dict[str, tuple[tuple[int, ...], Digests | None]] = {}
        self._jobs: dict[str, threading.Event] = {}
        self._hash_slots = threading.Semaphore(2)
        # "off" until a warm-up is requested, then "warming", then "ready".
        self.warm_state = "off"
        self.warm_failures = 0

    @staticmethod
    def _signature(st: os.stat_result) -> tuple[int, ...]:
        return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

    def manifest(self, repo_id: str, commit: str) -> Manifest | None:
        path = self.store._manifest_path(repo_id, commit)
        try:
            signature = self._signature(os.stat(path))
        except FileNotFoundError:
            return None
        with self._lock:
            cached = self._manifests.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        manifest = self.store.read_manifest(repo_id, commit)
        if manifest is not None:
            with self._lock:
                if len(self._manifests) >= 512:
                    self._manifests.clear()
                self._manifests[path] = (signature, manifest)
        return manifest

    def open_verified(self, sha256: str, file: TreeFile, wait: float | None = VERIFY_WAIT) -> BinaryIO:
        """Open a blob only if its bytes are the content upstream declares for `file`.

        Each blob is hashed once per process and again whenever the file on disk changes, so a blob that
        was corrupted or swapped after publication is refused rather than served. Hashing runs in the
        background: a request waits at most `wait` seconds for it, then gets 503 with Retry-After instead
        of hanging past client timeouts. `wait=None` waits for the result.

        A hash that fails is an outcome too: a blob that could not be read is refused with 500, without
        another attempt, until the file on disk changes. 503 means no outcome is known yet for the file
        this request opened: its hash is still running, or the file was replaced while it ran.
        """
        try:
            f = open(self.store.blob_path(sha256), "rb")  # noqa: SIM115
        except FileNotFoundError:
            raise HubError(500, "ArchiveInconsistent", f"blob for {file.path} is missing from the archive") from None
        except OSError as e:
            log.error("could not open blob %s: %s", sha256, e)
            raise HubError(500, "ArchiveInconsistent", f"blob for {file.path} cannot be read") from None
        try:
            signature = self._signature(os.fstat(f.fileno()))
            with self._lock:
                verified = self._digests.get(sha256)
                job = None
                if verified is None or verified[0] != signature:
                    job = self._jobs.get(sha256)
                    if job is None:
                        job = self._jobs[sha256] = threading.Event()
                        worker = threading.Thread(target=self._hash, args=(sha256, signature, job), name="verify")
                        worker.daemon = True
                        worker.start()
            if job is not None:
                finished = job.wait(wait)
                with self._lock:
                    verified = self._digests.get(sha256)
                if not finished or verified is None or verified[0] != signature:
                    raise HubError(
                        503, "VerificationPending", f"{file.path} is being verified before first use; retry shortly"
                    )
            if verified[1] is None or matches(verified[1], file) != sha256:
                raise HubError(500, "ArchiveInconsistent", f"blob for {file.path} failed verification")
            return f
        except BaseException:
            f.close()
            raise

    def _hash(self, sha256: str, requested: tuple[int, ...], job: threading.Event) -> None:
        """Hash the blob and record the outcome against the file it was read from.

        `requested` is the file the request that started this saw. A failure before this thread has its
        own view of the file is recorded against that one, so the same request does not start over.
        """
        signature, digests = requested, None
        try:
            with self._hash_slots, open(self.store.blob_path(sha256), "rb") as f:
                signature = self._signature(os.fstat(f.fileno()))
                digests = hash_stream(f)
        except Exception as e:
            log.error("could not verify blob %s: %s", sha256, e)
        finally:
            with self._lock:
                self._digests[sha256] = (signature, digests)
                del self._jobs[sha256]
            job.set()

    def _needs_hash(self, sha256: str) -> bool:
        """Whether checking the blob means hashing it: it is readable and this process has no outcome for it."""
        path = self.store.blob_path(sha256)
        try:
            signature = self._signature(os.stat(path))
        except OSError:
            return False
        with self._lock:
            verified = self._digests.get(sha256)
        return (verified is None or verified[0] != signature) and os.access(path, os.R_OK)

    def warm(self) -> None:
        """Check every referenced blob, hashing those not yet hashed, so requests do not wait for it.

        While a pass has work to do `/healthz` answers 503, and a request for a blob not yet reached
        answers 503 with Retry-After until that blob's hash is known. `warm_failures` is the number of
        references the finished pass found unservable; a pass with failures still ends "ready".
        """
        failures = 0
        for repo_id in self.store.list_repos():
            for commit in self.store.list_commits(repo_id):
                try:
                    manifest = self.manifest(repo_id, commit)
                except ArchiveError as e:
                    failures += 1
                    log.error("manifest of %s@%s is unusable: %s", repo_id, commit, e)
                    continue
                for path, entry in manifest.files.items() if manifest else ():
                    if not entry.blob:
                        continue
                    if self._needs_hash(entry.blob):
                        self.warm_state = "warming"
                    try:
                        # Cheap once an outcome is known, and it still checks the identity, so failures
                        # persist across passes without hashing again.
                        self.open_verified(entry.blob, entry.file, wait=None).close()
                    except (HubError, OSError) as e:
                        failures += 1
                        log.error("verification of %s@%s %s failed: %s", repo_id, commit, path, e)
        if self.warm_state != "ready" or failures != self.warm_failures:
            log.info("verification pass finished with %d failures", failures)
        self.warm_failures = failures
        self.warm_state = "ready"

    def warm_forever(self, interval: float) -> None:
        """Keep verifying: content acquired while the server runs is picked up by the next pass."""
        while True:
            try:
                self.warm()
            except Exception:
                log.exception("verification pass failed")
            time.sleep(interval)


def _split_repo(segments: list[str], archive: Archive, marker_ok) -> tuple[str, list[str]]:
    """Split leading path segments into an archived repo id and the rest. Two-segment ids are tried first."""
    for width in (2, 1):
        if len(segments) >= width and marker_ok(segments[width:]):
            repo_id = "/".join(segments[:width])
            try:
                validate_repo_id(repo_id)
            except InvalidDocument:
                continue
            if archive.store.repo_exists(repo_id):
                return repo_id, segments[width:]
    raise HubError(404, "RepoNotFound", "repository is not in this archive")


def _revision(archive: Archive, repo_id: str, revision: str) -> Manifest:
    commit = archive.store.resolve_revision(repo_id, revision)
    manifest = archive.manifest(repo_id, commit) if commit else None
    if manifest is None:
        raise HubError(404, "RevisionNotFound", f"revision {revision!r} of {repo_id} is not in this archive")
    return manifest


def _file_json(file: TreeFile) -> dict[str, Any]:
    return file.to_hub_json(with_xet=False)


def _model_info(manifest: Manifest, query: dict[str, list[str]]) -> Response:
    with_blobs = query.get("blobs", [""])[0].lower() in ("1", "true")
    siblings = []
    for path in sorted(manifest.files):
        file = manifest.files[path].file
        sibling: dict[str, Any] = {"rfilename": path}
        if with_blobs:
            sibling |= {"size": file.size, "blobId": file.git_oid}
            if file.lfs_sha256:
                sibling["lfs"] = {"sha256": file.lfs_sha256, "size": file.size, "pointerSize": file.lfs_pointer_size}
        siblings.append(sibling)
    return _json(
        manifest.info
        | {
            "id": manifest.repo_id,
            "modelId": manifest.repo_id,
            "sha": manifest.commit,
            "private": False,
            "gated": False,
            "siblings": siblings,
        }
    )


def _refs(archive: Archive, repo_id: str) -> Response:
    groups: dict[str, list[dict[str, str]]] = {"branches": [], "tags": [], "converts": [], "pullRequests": []}
    for ref in archive.store.list_refs(repo_id):
        if archive.manifest(repo_id, ref.commit) is None:
            continue
        if ref.ref.startswith("refs/heads/"):
            group = "branches"
        elif ref.ref.startswith("refs/tags/"):
            group = "tags"
        elif ref.ref.startswith("refs/convert/"):
            group = "converts"
        else:
            group = "pullRequests"
        name = ref.ref.removeprefix("refs/convert/") if group == "converts" else ref.short_name
        groups[group].append({"name": name, "ref": ref.ref, "targetCommit": ref.commit})
    return _json(groups)


def _tree(manifest: Manifest, prefix: str, query: dict[str, list[str]]) -> Response:
    recursive = query.get("recursive", [""])[0].lower() in ("1", "true")
    if prefix:
        validate_repo_path(prefix)
    under = prefix + "/" if prefix else ""

    def listed(path: str) -> bool:
        return path.startswith(under) and (recursive or "/" not in path[len(under) :])

    entries = [
        {"type": "directory", "oid": oid, "size": 0, "path": path}
        for path, oid in sorted(manifest.directories.items())
        if listed(path)
    ]
    entries += [_file_json(manifest.files[path].file) for path in sorted(manifest.files) if listed(path)]
    if prefix and not any(path.startswith(under) for path in manifest.files):
        raise HubError(404, "EntryNotFound", f"{prefix} is not a directory of this revision", manifest.commit)
    return _json(entries)


def _paths_info(manifest: Manifest, headers, body: bytes) -> Response:
    if "json" in (headers.get("Content-Type") or ""):
        try:
            paths = json.loads(body).get("paths", [])
        except (ValueError, AttributeError):
            raise HubError(400, "BadRequest", "body is not a JSON object") from None
    else:
        paths = parse_qs(body.decode("utf-8", "replace")).get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise HubError(400, "BadRequest", "paths must be a list of strings")
    entries = []
    for path in paths:
        if path in manifest.files:
            entries.append(_file_json(manifest.files[path].file))
        elif path in manifest.directories:
            entries.append({"type": "directory", "oid": manifest.directories[path], "size": 0, "path": path})
    return _json(entries)


def _resolve(archive: Archive, repo_id: str, rest: list[str], headers) -> Response:
    if len(rest) < 3:
        raise HubError(404, "EntryNotFound", "no file path given")
    manifest = _revision(archive, repo_id, rest[1])
    path = "/".join(rest[2:])
    entry = manifest.files.get(path)
    if entry is None:
        raise HubError(404, "EntryNotFound", f"{path} is not a file of this revision", manifest.commit)
    if entry.blob is None:
        # Deliberately not EntryNotFound: the file exists upstream, and clients cache that answer as absence.
        raise HubError(
            404,
            "EntryNotAcquired",
            f"{path} exists at this revision but has not been acquired into the archive; "
            f"prefetch it with: hf-archive pull {repo_id} --revision {manifest.commit} --include '{path}'",
            manifest.commit,
        )
    file = entry.file
    blob = archive.open_verified(entry.blob, file)
    response = Response(
        headers={
            "Content-Type": "application/octet-stream",
            "ETag": f'"{file.etag}"',
            "X-Repo-Commit": manifest.commit,
            "Accept-Ranges": "bytes",
        },
        blob=blob,
        length=file.size,
    )
    if file.lfs_sha256:
        response.headers |= {"X-Linked-ETag": f'"{file.etag}"', "X-Linked-Size": str(file.size)}
    try:
        if_range = headers.get("If-Range")
        wanted = (
            None if if_range and if_range.strip() != f'"{file.etag}"' else parse_range(headers.get("Range"), file.size)
        )
    except HubError:
        blob.close()
        raise
    if wanted is not None:
        response.status = 206
        response.offset, response.length = wanted[0], wanted[1] - wanted[0] + 1
        response.headers["Content-Range"] = f"bytes {wanted[0]}-{wanted[1]}/{file.size}"
    return response


def route(archive: Archive, method: str, target: str, headers, body: bytes) -> Response:
    url = urlsplit(target)
    segments = [unquote(segment) for segment in url.path.split("/")[1:]]
    if any(ord(c) < 0x20 or ord(c) == 0x7F for segment in segments for c in segment):
        raise HubError(400, "BadRequest", "request path contains control characters")
    query = parse_qs(url.query)

    if segments == ["healthz"] and method in ("GET", "HEAD"):
        warming = archive.warm_state == "warming"
        return _json(
            {
                "status": "warming" if warming else "ok",
                "version": __version__,
                "repos": len(archive.store.list_repos()),
                "warm": archive.warm_state,
                "warm_failures": archive.warm_failures,
            },
            503 if warming else 200,
        )

    if segments[:2] == ["api", "models"]:
        repo_id, rest = _split_repo(segments[2:], archive, lambda rest: not rest or rest[0] in _API_ACTIONS)
        action = rest[0] if rest else "revision"
        if action == "paths-info" and len(rest) == 2:
            if method != "POST":
                raise HubError(405, "MethodNotAllowed", "paths-info takes POST")
            return _paths_info(_revision(archive, repo_id, rest[1]), headers, body)
        if method not in ("GET", "HEAD"):
            raise HubError(405, "MethodNotAllowed", "this archive is read-only")
        if action == "revision" and len(rest) in (0, 2):
            return _model_info(_revision(archive, repo_id, rest[1] if rest else "main"), query)
        if action == "refs" and len(rest) == 1:
            return _refs(archive, repo_id)
        if action == "tree" and len(rest) >= 2:
            return _tree(_revision(archive, repo_id, rest[1]), "/".join(rest[2:]), query)
        raise HubError(404, "NotFound", "no such route")

    if method not in ("GET", "HEAD"):
        raise HubError(405, "MethodNotAllowed", "this archive is read-only")
    if "resolve" in segments[1:3]:
        repo_id, rest = _split_repo(segments, archive, lambda rest: bool(rest) and rest[0] == "resolve")
        return _resolve(archive, repo_id, rest, headers)
    raise HubError(404, "NotFound", "no such route")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"hf-archive/{__version__}"
    timeout = 300

    def _handle(self) -> None:
        body = b""
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return self._send(self._error(HubError(411, "LengthRequired", "chunked request bodies are not accepted")))
        declared = self.headers.get("Content-Length") or "0"
        if not declared.isascii() or not declared.isdigit() or len(declared) > 12:
            self.close_connection = True
            return self._send(self._error(HubError(400, "BadRequest", "invalid Content-Length")))
        length = int(declared)
        if length > MAX_BODY:
            self.close_connection = True
            return self._send(self._error(HubError(413, "PayloadTooLarge", "request body too large")))
        if length:
            body = self.rfile.read(length)
        try:
            response = route(self.server.archive, self.command, self.path, self.headers, body)
        except HubError as e:
            response = self._error(e)
        except InvalidDocument as e:
            log.error("%s %s: %s", self.command, self.path, e)
            response = self._error(HubError(500, "ArchiveInconsistent", "an archive document is invalid"))
        except Exception:
            log.exception("%s %s failed", self.command, self.path)
            response = self._error(HubError(500, "InternalError", "internal error"))
        self._send(response)

    @staticmethod
    def _error(error: HubError) -> Response:
        response = _json({"error": error.message}, error.status)
        response.headers["X-Error-Code"] = error.code
        # Messages can quote request input: keep header values to printable ASCII.
        response.headers["X-Error-Message"] = "".join(c if " " <= c <= "~" else "?" for c in error.message)
        if error.commit:
            response.headers["X-Repo-Commit"] = error.commit
        if error.status == 503:
            response.headers["Retry-After"] = str(RETRY_AFTER)
        if error.status == 405:
            response.headers["Allow"] = "GET, HEAD"
        return response

    def _send(self, response: Response) -> None:
        try:
            length = response.length if response.blob is not None else len(response.body)
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(length))
            self.end_headers()
            if self.command != "HEAD":
                if response.blob is not None:
                    self.wfile.flush()
                    if length:
                        self.connection.sendfile(response.blob, response.offset, length)
                else:
                    self.wfile.write(response.body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        finally:
            if response.blob is not None:
                response.blob.close()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def log_message(self, format: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), format % args)


class ArchiveServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], store: Store):
        super().__init__(address, Handler)
        self.store = store
        self.archive = Archive(store)

    def warm_in_background(self, interval: float = WARM_INTERVAL) -> None:
        self.archive.warm_state = "warming"
        threading.Thread(target=self.archive.warm_forever, args=(interval,), name="warm", daemon=True).start()


def make_server(root: Path | str, host: str = "127.0.0.1", port: int = 0) -> ArchiveServer:
    """Build a server over the existing archive at `root`. Port 0 picks a free port."""
    return ArchiveServer((host, port), Store(root))
