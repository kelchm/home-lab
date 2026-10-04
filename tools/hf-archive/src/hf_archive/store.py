"""On-disk archive: a global SHA-256 content store plus per-repository revision manifests and refs.

Layout under the archive root:

    archive.json                               format marker
    blobs/sha256/<aa>/<sha256>                 verified content, ordinary read-only files
    aliases/git-sha1/<aa>/<sha1>.json          Git blob id -> sha256, a lookup hint re-proven on every reuse
    aliases/xet/<aa>/<hash>.json               Xet hash -> sha256 as asserted by upstream, never used for lookup
    repos/models--<ns>--<name>/revisions/<commit>.json
    repos/models--<ns>--<name>/refs/<url-quoted full ref>.json
    staging/<id>/                              in-flight work, same filesystem as blobs
    quarantine/                                blobs found not to match their name, moved aside
    locks/                                     publish lock and content-keyed acquisition locks

Readers never lock and never write: every document and blob appears through an atomic rename, and a
manifest is only published once every blob it references is in place.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote, unquote

from .errors import ArchiveError, IntegrityError, InvalidDocument
from .model import (
    COMMIT_RE,
    SHA256_RE,
    Manifest,
    Ref,
    RevisionMetadata,
    TreeFile,
    now,
    validate_commit,
    validate_ref,
    validate_repo_id,
    validate_sha256,
)

FORMAT = {"format": "hf-archive", "format_version": 1}
CHUNK = 1024 * 1024
REF_HISTORY = 20


@dataclass(frozen=True)
class Digests:
    sha256: str
    git_sha1: str
    size: int


def hash_stream(f: BinaryIO) -> Digests:
    """Read an open file from its start and return its SHA-256, its Git blob id, and its size."""
    size = os.fstat(f.fileno()).st_size
    f.seek(0)
    sha256 = hashlib.sha256()
    git_sha1 = hashlib.sha1(b"blob %d\0" % size, usedforsecurity=False)
    seen = 0
    while chunk := f.read(CHUNK):
        sha256.update(chunk)
        git_sha1.update(chunk)
        seen += len(chunk)
    if seen != size:
        raise IntegrityError("file changed size while being read")
    return Digests(sha256.hexdigest(), git_sha1.hexdigest(), size)


def hash_file(path: Path) -> Digests:
    with open(path, "rb") as f:
        return hash_stream(f)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_mkdir(path: Path) -> None:
    """Create `path` and any missing ancestors, persisting each new directory entry in its parent."""
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        with suppress(FileExistsError):
            directory.mkdir()
        _fsync_dir(directory.parent)


def write_json_atomic(path: Path, doc: dict[str, Any]) -> None:
    """Durably replace `path`: readers see the old document or the complete new one."""
    durable_mkdir(path.parent)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:12]}.tmp")
    data = json.dumps(doc, indent=2, ensure_ascii=False).encode() + b"\n"
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def _read_json(path: Path) -> Any | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except ValueError as e:
        raise InvalidDocument(f"{path} is not valid JSON: {e}") from e


def matches(digests: Digests, file: TreeFile) -> str | None:
    """Return the content's sha256 if `digests` are those of the content upstream declares for `file`."""
    if digests.size != file.size:
        return None
    if file.lfs_sha256:
        return digests.sha256 if digests.sha256 == file.lfs_sha256 else None
    return digests.sha256 if digests.git_sha1 == file.git_oid else None


@contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    """Hold an exclusive lock named by `path`, removing the lock file on release.

    The inode check makes removal safe: a waiter that wins the lock on an already-unlinked file
    notices the path no longer names it and starts over.
    """
    durable_mkdir(path.parent)
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        with suppress(FileNotFoundError):
            if os.fstat(fd).st_ino == os.stat(path).st_ino:
                break
        os.close(fd)
    try:
        yield
    finally:
        with suppress(FileNotFoundError):
            os.unlink(path)
        os.close(fd)


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        marker = _read_json(self.root / "archive.json")
        if marker is None:
            raise ArchiveError(
                f"{self.root} is not an hf-archive root (pull and import create one in an empty directory)"
            )
        if marker != FORMAT:
            raise ArchiveError(f"{self.root} has an unsupported archive format: {marker!r}")
        self.blobs_dir = self.root / "blobs" / "sha256"
        self.aliases_dir = self.root / "aliases"
        self.repos_dir = self.root / "repos"
        self.staging_dir = self.root / "staging"
        self.locks_dir = self.root / "locks"
        self.quarantine_dir = self.root / "quarantine"

    @classmethod
    def init(cls, root: Path | str) -> Store:
        """Open the archive at `root`, creating an empty one if the directory holds nothing yet."""
        root = Path(root).resolve()
        durable_mkdir(root)
        if not (root / "archive.json").exists():
            if any(root.iterdir()):
                raise ArchiveError(f"{root} is not empty and is not an hf-archive root")
            write_json_atomic(root / "archive.json", FORMAT)
        store = cls(root)
        for directory in (store.blobs_dir, store.repos_dir, store.staging_dir, store.locks_dir):
            durable_mkdir(directory)
        return store

    # --- blobs -------------------------------------------------------------------------------------

    def blob_path(self, sha256: str) -> Path:
        validate_sha256(sha256)
        return self.blobs_dir / sha256[:2] / sha256

    def has_blob(self, sha256: str, size: int) -> bool:
        try:
            st = os.stat(self.blob_path(sha256), follow_symlinks=False)
        except FileNotFoundError:
            return False
        return stat.S_ISREG(st.st_mode) and st.st_size == size

    def check_blob(self, sha256: str, file: TreeFile) -> bool:
        """Read a held blob and report whether its bytes are exactly the content upstream declares for `file`.

        The blob's name is not trusted: its SHA-256 must equal the name, and it must carry the file's LFS
        SHA-256 or, for a regular Git file, hash to the file's Git blob id.
        """
        if not self.has_blob(sha256, file.size):
            return False
        return matches(hash_file(self.blob_path(sha256)), file) == sha256

    def _alias_path(self, kind: str, identifier: str) -> Path:
        return self.aliases_dir / kind / identifier[:2] / f"{identifier}.json"

    def _write_alias(self, kind: str, identifier: str, digests: Digests) -> None:
        path = self._alias_path(kind, identifier)
        doc = {"kind": kind, "id": identifier, "sha256": digests.sha256, "size": digests.size}
        if _read_json(path) != doc:
            write_json_atomic(path, doc)

    def _quarantine(self, sha256: str) -> None:
        """Move a blob whose bytes do not match its name out of the content store."""
        durable_mkdir(self.quarantine_dir)
        os.replace(self.blob_path(sha256), self.quarantine_dir / f"{sha256}.{uuid.uuid4().hex[:12]}")
        _fsync_dir(self.blob_path(sha256).parent)
        _fsync_dir(self.quarantine_dir)

    def find_blob(self, file: TreeFile) -> str | None:
        """Return the sha256 of a held blob proven to be the content upstream declares for `file`, if any.

        The candidate is named by the LFS SHA-256 or, for a regular Git file, by an alias. Both are only
        hints: the blob is re-read and must match the expected identity. A blob that does not hash to its
        own name is quarantined. A Xet hash never locates content. Call this under the content lock.
        """
        candidate = file.lfs_sha256
        if candidate is None:
            alias = _read_json(self._alias_path("git-sha1", file.git_oid))
            candidate = alias.get("sha256") if isinstance(alias, dict) else None
            if not isinstance(candidate, str) or not SHA256_RE.fullmatch(candidate):
                return None
        with self._blob_lock(candidate):
            if not self.has_blob(candidate, file.size):
                return None
            digests = hash_file(self.blob_path(candidate))
            if digests.sha256 != candidate:
                self._quarantine(candidate)
                return None
        return candidate if matches(digests, file) else None

    def publish_blob(self, staged: Path, file: TreeFile) -> str:
        """Verify a staged file against `file`'s upstream identity and move it into the content store.

        The staged file is consumed either way: published on success, deleted on mismatch. A blob already
        stored under the same name is kept only if its bytes hash to that name; otherwise it is quarantined
        and replaced.
        """
        staged = Path(staged)
        if not staged.resolve().is_relative_to(self.staging_dir) or not stat.S_ISREG(os.lstat(staged).st_mode):
            raise ArchiveError(f"{staged} is not a regular file inside the staging area")
        try:
            digests = hash_file(staged)
            if digests.size != file.size:
                raise IntegrityError(f"{file.path}: expected {file.size} bytes, got {digests.size}")
            if file.lfs_sha256 and digests.sha256 != file.lfs_sha256:
                raise IntegrityError(f"{file.path}: expected LFS sha256 {file.lfs_sha256}, got {digests.sha256}")
            if not file.lfs_sha256 and digests.git_sha1 != file.git_oid:
                raise IntegrityError(f"{file.path}: expected Git blob id {file.git_oid}, got {digests.git_sha1}")

            final = self.blob_path(digests.sha256)
            with self._blob_lock(digests.sha256):
                if final.exists() and hash_file(final).sha256 != digests.sha256:
                    self._quarantine(digests.sha256)
                if not final.exists():
                    durable_mkdir(final.parent)
                    os.chmod(staged, 0o444)
                    _fsync_file(staged)
                    os.replace(staged, final)
                    _fsync_dir(final.parent)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(staged)

        self._write_alias("git-sha1", digests.git_sha1, digests)
        if file.xet_hash:
            self._write_alias("xet", file.xet_hash, digests)
        return digests.sha256

    # --- coordination ------------------------------------------------------------------------------

    def content_lock(self, file: TreeFile):
        """Serialize acquisition of one expected content identity across processes and threads."""
        return _exclusive(self.locks_dir / "content" / f"{file.content_key}.lock")

    def _blob_lock(self, sha256: str):
        """Serialize checking, quarantining and replacing one stored blob.

        Content locks are keyed by expected identity, so the same bytes can be reached under two of them
        (an LFS SHA-256 and a Git blob id). This lock makes a blob's hash and its removal one step.
        """
        return _exclusive(self.locks_dir / "blob" / f"{validate_sha256(sha256)}.lock")

    def _publish_lock(self):
        return _exclusive(self.locks_dir / "publish.lock")

    @contextmanager
    def staging(self) -> Iterator[Path]:
        """Yield a private scratch directory on the archive filesystem, removed on exit."""
        self.sweep_staging()
        while True:
            name = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
            lock = self.staging_dir / f"{name}.lock"
            fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            with suppress(FileNotFoundError):
                if os.fstat(fd).st_ino == os.stat(lock).st_ino:
                    break
            os.close(fd)
        directory = self.staging_dir / name
        try:
            directory.mkdir()
            yield directory
        finally:
            shutil.rmtree(directory, ignore_errors=True)
            with suppress(FileNotFoundError):
                os.unlink(lock)
            os.close(fd)

    def sweep_staging(self) -> list[str]:
        """Remove scratch directories whose owning process is gone. Returns the names removed."""
        removed = []
        for lock in self.staging_dir.glob("*.lock"):
            try:
                fd = os.open(lock, os.O_RDWR)
            except FileNotFoundError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                shutil.rmtree(lock.with_suffix(""), ignore_errors=True)
                with suppress(FileNotFoundError):
                    os.unlink(lock)
                removed.append(lock.stem)
            finally:
                os.close(fd)
        return removed

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.staging_dir).free

    # --- repositories ------------------------------------------------------------------------------

    def _repo_dir(self, repo_id: str) -> Path:
        return self.repos_dir / ("models--" + validate_repo_id(repo_id).replace("/", "--"))

    def _manifest_path(self, repo_id: str, commit: str) -> Path:
        return self._repo_dir(repo_id) / "revisions" / f"{validate_commit(commit)}.json"

    def _ref_path(self, repo_id: str, ref: str) -> Path:
        return self._repo_dir(repo_id) / "refs" / f"{quote(validate_ref(ref), safe='')}.json"

    def list_repos(self) -> list[str]:
        repos = []
        for directory in sorted(self.repos_dir.glob("models--*")):
            repo_id = directory.name.removeprefix("models--").replace("--", "/")
            with suppress(InvalidDocument):
                if self.list_commits(validate_repo_id(repo_id)):
                    repos.append(repo_id)
        return repos

    def list_commits(self, repo_id: str) -> list[str]:
        revisions = self._repo_dir(repo_id) / "revisions"
        return sorted(p.stem for p in revisions.glob("*.json") if COMMIT_RE.fullmatch(p.stem))

    def repo_exists(self, repo_id: str) -> bool:
        return bool(self.list_commits(repo_id))

    def read_manifest(self, repo_id: str, commit: str) -> Manifest | None:
        path = self._manifest_path(repo_id, commit)
        doc = _read_json(path)
        if doc is None:
            return None
        manifest = Manifest.from_json(doc)
        if (manifest.repo_id, manifest.commit) != (repo_id, commit):
            raise InvalidDocument(f"{path} describes {manifest.repo_id}@{manifest.commit}")
        return manifest

    def read_ref(self, repo_id: str, ref: str) -> Ref | None:
        path = self._ref_path(repo_id, ref)
        doc = _read_json(path)
        if doc is None:
            return None
        parsed = Ref.from_json(doc)
        if (parsed.repo_id, parsed.ref) != (repo_id, ref):
            raise InvalidDocument(f"{path} describes {parsed.repo_id} {parsed.ref}")
        return parsed

    def list_refs(self, repo_id: str) -> list[Ref]:
        refs = []
        for path in sorted((self._repo_dir(repo_id) / "refs").glob("*.json")):
            if path.name.startswith("."):
                continue
            ref = self.read_ref(repo_id, unquote(path.stem))
            if ref is not None:
                refs.append(ref)
        return refs

    def resolve_revision(self, repo_id: str, revision: str) -> str | None:
        """Map a commit hash, branch, tag, or full ref name to an archived commit."""
        if COMMIT_RE.fullmatch(revision):
            return revision if self._manifest_path(repo_id, revision).exists() else None
        candidates = [revision] if revision.startswith("refs/") else [f"refs/heads/{revision}", f"refs/tags/{revision}"]
        for candidate in candidates:
            try:
                ref = self.read_ref(repo_id, candidate)
            except InvalidDocument:
                continue
            if ref is not None and self._manifest_path(repo_id, ref.commit).exists():
                return ref.commit
        return None

    def record_revision(
        self, meta: RevisionMetadata, acquired: dict[str, tuple[str, dict[str, Any]]], event: dict[str, Any]
    ) -> Manifest:
        """Publish newly acquired contents of one commit, then move the refs that named it.

        `acquired` maps repository paths to `(blob sha256, provenance)`. The manifest is merged with any
        existing one for the commit, so earlier acquisitions are kept. Nothing is written unless every
        blob the resulting manifest references is present with its exact size.
        """
        with self._publish_lock():
            manifest = self.read_manifest(meta.repo_id, meta.commit)
            if manifest is None:
                manifest = Manifest.from_metadata(meta)
            else:
                _check_same_tree(manifest, meta)
                manifest.private, manifest.gated, manifest.info = meta.private, meta.gated, dict(meta.info)
            for path, (sha256, provenance) in acquired.items():
                entry = manifest.files[path]
                if entry.blob is None:
                    entry.blob, entry.acquired = sha256, provenance
                elif entry.blob != sha256:
                    raise ArchiveError(f"{path} is already recorded with different content")
            for path, entry in manifest.files.items():
                if entry.blob is not None and not self.has_blob(entry.blob, entry.file.size):
                    raise ArchiveError(f"refusing to publish {meta.repo_id}@{meta.commit}: blob for {path} is missing")
            manifest.history.append(event)
            manifest.updated_at = now()
            write_json_atomic(self._manifest_path(meta.repo_id, meta.commit), manifest.to_json())
            for ref in meta.refs:
                self._move_ref(meta.repo_id, ref, meta.commit)
            return manifest

    def _move_ref(self, repo_id: str, name: str, commit: str) -> None:
        ref = self.read_ref(repo_id, name) or Ref(repo_id, name, commit)
        if ref.commit == commit and ref.updated_at:
            return
        if ref.commit != commit:
            ref.previous = (ref.previous + [{"commit": ref.commit, "replaced_at": now()}])[-REF_HISTORY:]
            ref.commit = commit
        ref.updated_at = now()
        write_json_atomic(self._ref_path(repo_id, name), ref.to_json())


def _check_same_tree(manifest: Manifest, meta: RevisionMetadata) -> None:
    """A commit is immutable: metadata for one already archived must describe the same tree."""

    def identity(file: TreeFile) -> tuple[int, str, str | None]:
        return (file.size, file.git_oid, file.lfs_sha256)

    recorded = {path: identity(entry.file) for path, entry in manifest.files.items()}
    offered = {path: identity(file) for path, file in meta.files.items()}
    if recorded != offered:
        raise ArchiveError(
            f"metadata for {meta.repo_id}@{meta.commit} disagrees with the tree already archived for that commit"
        )
