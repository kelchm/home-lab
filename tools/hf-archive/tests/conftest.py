"""Shared fixtures: synthetic upstream metadata and a Hub double that serves bytes from memory."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hf_archive.model import METADATA_SCHEMA, RevisionMetadata, TreeFile
from hf_archive.store import Store

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def git_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_entry(path: str, data: bytes, *, lfs: bool = False, xet: str | None = None) -> dict:
    """A Hub `/tree` entry describing `data` stored at `path`."""
    if not lfs:
        return {"type": "file", "path": path, "oid": git_oid(data), "size": len(data)}
    entry = {
        "type": "file",
        "path": path,
        "oid": git_oid(b"pointer:" + data[:16]),
        "size": len(data),
        "lfs": {"oid": sha256(data), "size": len(data), "pointerSize": 134},
    }
    if xet:
        entry["xetHash"] = xet
    return entry


def metadata(repo_id: str, commit: str, files: dict[str, tuple[bytes, bool]], *, refs=("refs/heads/main",), **info):
    """Metadata for a revision whose files are `{path: (content, is_lfs)}`."""
    tree = [tree_entry(path, data, lfs=lfs) for path, (data, lfs) in files.items()]
    parents = {str(parent) for path in files for parent in Path(path).parents if str(parent) != "."}
    tree += [{"type": "directory", "path": parent, "oid": git_oid(parent.encode())} for parent in sorted(parents)]
    doc = {
        "schema": METADATA_SCHEMA,
        "schema_version": 1,
        "info": {"id": repo_id, "sha": commit, "private": False, "gated": False} | info,
        "tree": tree,
        "refs": list(refs),
    }
    return RevisionMetadata.from_hub_json(doc)


class FakeHub:
    """Upstream double. `content` maps (repo_id, path) to the bytes a download returns."""

    source = "fake-hub"

    def __init__(self):
        self.revisions: dict[tuple[str, str], RevisionMetadata] = {}
        self.content: dict[tuple[str, str], bytes] = {}
        self.downloads: list[tuple[str, str]] = []
        self.before_download = lambda file: None

    def add(self, repo_id: str, commit: str, files: dict[str, tuple[bytes, bool]], revision="main", **kwargs):
        meta = metadata(repo_id, commit, files, **kwargs)
        self.revisions[(repo_id, revision)] = self.revisions[(repo_id, commit)] = meta
        for path, (data, _) in files.items():
            self.content[(repo_id, path)] = data
        return meta

    def fetch_metadata(self, repo_id: str, revision: str) -> RevisionMetadata:
        return self.revisions[(repo_id, revision)]

    def download(self, meta: RevisionMetadata, file: TreeFile, dest: Path) -> Path:
        self.before_download(file)
        self.downloads.append((meta.repo_id, file.path))
        target = dest / "payload"
        target.write_bytes(self.content[(meta.repo_id, file.path)])
        return target


@pytest.fixture
def store(tmp_path) -> Store:
    return Store.init(tmp_path / "archive")


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()
