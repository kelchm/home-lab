"""Validated identifiers and the JSON documents the archive persists.

Every string that later becomes part of a filesystem path is validated here, both when it arrives from
upstream metadata and when a persisted document is read back.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .errors import InvalidDocument

SCHEMA_VERSION = 1
REVISION_SCHEMA = "hf-archive.revision"
REF_SCHEMA = "hf-archive.ref"
METADATA_SCHEMA = "hf-archive.metadata"

COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA1_RE = COMMIT_RE
SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REPO_SEGMENT_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,95}")
_REF_RE = re.compile(r"refs/[A-Za-z0-9._/-]{1,200}")

# Upstream model-info fields kept and served back. Counters and anything file-related are left out.
# `upstream_id` is ours: the canonical id when the archived repo id is one upstream redirects.
INFO_FIELDS = (
    "author",
    "lastModified",
    "createdAt",
    "disabled",
    "tags",
    "pipeline_tag",
    "library_name",
    "upstream_id",
)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_repo_id(repo_id: Any) -> str:
    if not isinstance(repo_id, str):
        raise InvalidDocument(f"repo id must be a string, got {type(repo_id).__name__}")
    segments = repo_id.split("/")
    if not 1 <= len(segments) <= 2:
        raise InvalidDocument(f"invalid repo id: {repo_id!r}")
    for segment in segments:
        if not _REPO_SEGMENT_RE.fullmatch(segment) or "--" in segment or ".." in segment:
            raise InvalidDocument(f"invalid repo id: {repo_id!r}")
    return repo_id


def validate_commit(commit: Any) -> str:
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise InvalidDocument(f"invalid commit hash: {commit!r}")
    return commit


def validate_sha256(value: Any) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise InvalidDocument(f"invalid sha256: {value!r}")
    return value


def validate_ref(ref: Any) -> str:
    """Validate a full git ref name such as `refs/heads/main` or `refs/pr/3`."""
    if not isinstance(ref, str) or not _REF_RE.fullmatch(ref):
        raise InvalidDocument(f"invalid ref: {ref!r}")
    if any(part in ("", ".", "..") for part in ref.split("/")):
        raise InvalidDocument(f"invalid ref: {ref!r}")
    return ref


def validate_repo_path(path: Any) -> str:
    """Validate a file path inside a repository: relative, '/'-separated, no traversal."""
    if not isinstance(path, str) or not path or len(path) > 1024:
        raise InvalidDocument(f"invalid repository path: {path!r}")
    if "\\" in path or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        raise InvalidDocument(f"invalid repository path: {path!r}")
    if any(segment in ("", ".", "..") for segment in path.split("/")):
        raise InvalidDocument(f"invalid repository path: {path!r}")
    return path


def _size(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidDocument(f"invalid {what}: {value!r}")
    return value


def _mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidDocument(f"{what} must be an object")
    return value


def _check_schema(doc: dict[str, Any], schema: str) -> None:
    if doc.get("schema") != schema:
        raise InvalidDocument(f"expected schema {schema!r}, found {doc.get('schema')!r}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise InvalidDocument(f"unsupported {schema} version: {doc.get('schema_version')!r}")


@dataclass(frozen=True)
class TreeFile:
    """Upstream identity of one file at one commit.

    `size` is the size of the file content. For an LFS file the content is identified by `lfs_sha256` and
    `git_oid` names the pointer file; otherwise `git_oid` is the Git blob id of the content itself.
    `xet_hash` is a third, unrelated identifier and is never comparable to either.
    """

    path: str
    size: int
    git_oid: str
    lfs_sha256: str | None = None
    lfs_pointer_size: int | None = None
    xet_hash: str | None = None

    @property
    def content_key(self) -> str:
        """Identity the content is expected to have; equal keys mean equal bytes."""
        return f"sha256-{self.lfs_sha256}" if self.lfs_sha256 else f"git-{self.git_oid}"

    @property
    def etag(self) -> str:
        """ETag huggingface.co reports for this file."""
        return self.lfs_sha256 or self.git_oid

    def to_json(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"size": self.size, "git_oid": self.git_oid}
        if self.lfs_sha256:
            doc["lfs"] = {"sha256": self.lfs_sha256, "pointer_size": self.lfs_pointer_size}
        if self.xet_hash:
            doc["xet_hash"] = self.xet_hash
        return doc

    @classmethod
    def from_json(cls, path: str, doc: Any) -> TreeFile:
        doc = _mapping(doc, f"file {path!r}")
        git_oid = doc.get("git_oid")
        if not isinstance(git_oid, str) or not SHA1_RE.fullmatch(git_oid):
            raise InvalidDocument(f"invalid git oid for {path!r}: {git_oid!r}")
        lfs = doc.get("lfs")
        xet_hash = doc.get("xet_hash")
        if xet_hash is not None and not (isinstance(xet_hash, str) and SHA256_RE.fullmatch(xet_hash)):
            raise InvalidDocument(f"invalid xet hash for {path!r}: {xet_hash!r}")
        pointer_size = None
        if lfs is not None:
            pointer_size = _mapping(lfs, "lfs").get("pointer_size")
            if pointer_size is not None:
                _size(pointer_size, "lfs pointer size")
        return cls(
            path=validate_repo_path(path),
            size=_size(doc.get("size"), f"size of {path!r}"),
            git_oid=git_oid,
            lfs_sha256=validate_sha256(lfs.get("sha256")) if lfs is not None else None,
            lfs_pointer_size=pointer_size,
            xet_hash=xet_hash,
        )

    @classmethod
    def from_hub_json(cls, entry: Any) -> TreeFile:
        """Parse one `type: file` entry of the Hub's `/tree` listing."""
        entry = _mapping(entry, "tree entry")
        path = validate_repo_path(entry.get("path"))
        lfs = entry.get("lfs")
        doc: dict[str, Any] = {"size": entry.get("size"), "git_oid": entry.get("oid")}
        if lfs is not None:
            lfs = _mapping(lfs, "lfs")
            if lfs.get("size") != entry.get("size"):
                raise InvalidDocument(f"lfs size disagrees with file size for {path!r}")
            doc["lfs"] = {"sha256": lfs.get("oid"), "pointer_size": lfs.get("pointerSize")}
        if entry.get("xetHash") is not None:
            doc["xet_hash"] = entry["xetHash"]
        return cls.from_json(path, doc)

    def to_hub_json(self, *, with_xet: bool) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": "file", "oid": self.git_oid, "size": self.size, "path": self.path}
        if self.lfs_sha256:
            entry["lfs"] = {"oid": self.lfs_sha256, "size": self.size, "pointerSize": self.lfs_pointer_size}
        if with_xet and self.xet_hash:
            entry["xetHash"] = self.xet_hash
        return entry


def _validate_directories(value: Any) -> dict[str, str]:
    directories = _mapping(value, "directories")
    for path, oid in directories.items():
        validate_repo_path(path)
        if not isinstance(oid, str) or not SHA1_RE.fullmatch(oid):
            raise InvalidDocument(f"invalid tree oid for directory {path!r}")
    return dict(directories)


def _validate_visibility(private: Any, gated: Any) -> None:
    if private is not None and not isinstance(private, bool):
        raise InvalidDocument(f"invalid private flag: {private!r}")
    if gated is not None and not isinstance(gated, bool) and gated not in ("auto", "manual"):
        raise InvalidDocument(f"invalid gated flag: {gated!r}")


@dataclass
class RevisionMetadata:
    """What upstream says about one commit: visibility, the complete file tree, and refs pointing at it."""

    repo_id: str
    commit: str
    private: bool | None
    gated: bool | str | None
    info: dict[str, Any]
    files: dict[str, TreeFile]
    directories: dict[str, str] = field(default_factory=dict)
    refs: list[str] = field(default_factory=list)

    @classmethod
    def from_hub_json(cls, doc: Any) -> RevisionMetadata:
        """Parse a metadata document: Hub-shaped `info` and `tree`, plus the `refs` naming this commit."""
        doc = _mapping(doc, "metadata")
        _check_schema(doc, METADATA_SCHEMA)
        info = _mapping(doc.get("info"), "info")
        tree = doc.get("tree")
        if not isinstance(tree, list):
            raise InvalidDocument("tree must be a list")
        files: dict[str, TreeFile] = {}
        directories: dict[str, str] = {}
        for entry in tree:
            entry = _mapping(entry, "tree entry")
            if entry.get("type") == "file":
                file = TreeFile.from_hub_json(entry)
                files[file.path] = file
            elif entry.get("type") == "directory":
                directories[entry.get("path")] = entry.get("oid")
            else:
                raise InvalidDocument(f"unknown tree entry type: {entry.get('type')!r}")
        refs = doc.get("refs", [])
        if not isinstance(refs, list):
            raise InvalidDocument("refs must be a list")
        _validate_visibility(info.get("private"), info.get("gated"))
        return cls(
            repo_id=validate_repo_id(info.get("id")),
            commit=validate_commit(info.get("sha")),
            private=info.get("private"),
            gated=info.get("gated"),
            info={key: info[key] for key in INFO_FIELDS if info.get(key) is not None},
            files=files,
            directories=_validate_directories(directories),
            refs=[validate_ref(ref) for ref in refs],
        )

    def to_hub_json(self) -> dict[str, Any]:
        tree: list[dict[str, Any]] = [
            {"type": "directory", "oid": oid, "size": 0, "path": path} for path, oid in sorted(self.directories.items())
        ]
        tree += [self.files[path].to_hub_json(with_xet=True) for path in sorted(self.files)]
        info = {"id": self.repo_id, "sha": self.commit, "private": self.private, "gated": self.gated}
        return {
            "schema": METADATA_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "info": info | self.info,
            "tree": tree,
            "refs": list(self.refs),
        }


@dataclass
class FileEntry:
    """One tree file in a revision manifest. `blob is None` means the content is not acquired."""

    file: TreeFile
    blob: str | None = None
    acquired: dict[str, Any] | None = None


@dataclass
class Manifest:
    """Durable record of one commit: the complete upstream tree and which contents the archive holds."""

    repo_id: str
    commit: str
    private: bool | None
    gated: bool | str | None
    info: dict[str, Any]
    directories: dict[str, str]
    files: dict[str, FileEntry]
    history: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""

    @classmethod
    def from_metadata(cls, meta: RevisionMetadata) -> Manifest:
        return cls(
            repo_id=meta.repo_id,
            commit=meta.commit,
            private=meta.private,
            gated=meta.gated,
            info=dict(meta.info),
            directories=dict(meta.directories),
            files={path: FileEntry(file) for path, file in meta.files.items()},
        )

    @property
    def acquired_count(self) -> int:
        return sum(1 for entry in self.files.values() if entry.blob)

    def to_json(self) -> dict[str, Any]:
        files = {}
        for path in sorted(self.files):
            entry = self.files[path]
            doc = entry.file.to_json()
            doc["blob"] = entry.blob
            if entry.acquired is not None:
                doc["acquired"] = entry.acquired
            files[path] = doc
        return {
            "schema": REVISION_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "repo_type": "model",
            "repo_id": self.repo_id,
            "commit": self.commit,
            "visibility": {"private": self.private, "gated": self.gated},
            "hub_info": self.info,
            "directories": dict(sorted(self.directories.items())),
            "files": files,
            "history": self.history,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_json(cls, doc: Any) -> Manifest:
        doc = _mapping(doc, "manifest")
        _check_schema(doc, REVISION_SCHEMA)
        if doc.get("repo_type") != "model":
            raise InvalidDocument(f"unsupported repo type: {doc.get('repo_type')!r}")
        visibility = _mapping(doc.get("visibility"), "visibility")
        _validate_visibility(visibility.get("private"), visibility.get("gated"))
        files: dict[str, FileEntry] = {}
        for path, file_doc in _mapping(doc.get("files"), "files").items():
            file = TreeFile.from_json(path, file_doc)
            blob = file_doc.get("blob")
            acquired = file_doc.get("acquired")
            if blob is not None:
                validate_sha256(blob)
                if file.lfs_sha256 and blob != file.lfs_sha256:
                    raise InvalidDocument(f"blob of {path!r} disagrees with its LFS sha256")
            if acquired is not None:
                _mapping(acquired, "acquired")
            files[path] = FileEntry(file, blob, acquired)
        history = doc.get("history", [])
        if not isinstance(history, list):
            raise InvalidDocument("history must be a list")
        return cls(
            repo_id=validate_repo_id(doc.get("repo_id")),
            commit=validate_commit(doc.get("commit")),
            private=visibility.get("private"),
            gated=visibility.get("gated"),
            info=_mapping(doc.get("hub_info"), "hub_info"),
            directories=_validate_directories(doc.get("directories")),
            files=files,
            history=history,
            updated_at=str(doc.get("updated_at", "")),
        )


@dataclass
class Ref:
    """Last known target of an upstream ref, as seen by the most recent successful acquisition."""

    repo_id: str
    ref: str
    commit: str
    updated_at: str = ""
    previous: list[dict[str, str]] = field(default_factory=list)

    @property
    def short_name(self) -> str:
        for prefix in ("refs/heads/", "refs/tags/"):
            if self.ref.startswith(prefix):
                return self.ref[len(prefix) :]
        return self.ref

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": REF_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "repo_type": "model",
            "repo_id": self.repo_id,
            "ref": self.ref,
            "commit": self.commit,
            "updated_at": self.updated_at,
            "previous": self.previous,
        }

    @classmethod
    def from_json(cls, doc: Any) -> Ref:
        doc = _mapping(doc, "ref")
        _check_schema(doc, REF_SCHEMA)
        previous = doc.get("previous", [])
        if not isinstance(previous, list):
            raise InvalidDocument("previous must be a list")
        return cls(
            repo_id=validate_repo_id(doc.get("repo_id")),
            ref=validate_ref(doc.get("ref")),
            commit=validate_commit(doc.get("commit")),
            updated_at=str(doc.get("updated_at", "")),
            previous=previous,
        )


def select_paths(paths: list[str], include: list[str], exclude: list[str]) -> list[str]:
    """Filter paths with the glob semantics `snapshot_download` uses for allow/ignore patterns."""

    def expand(patterns: list[str]) -> list[str]:
        return [pattern + "*" if pattern.endswith("/") else pattern for pattern in patterns]

    include, exclude = expand(include), expand(exclude)
    return [
        path
        for path in paths
        if (not include or any(fnmatch.fnmatch(path, pattern) for pattern in include))
        and not any(fnmatch.fnmatch(path, pattern) for pattern in exclude)
    ]
