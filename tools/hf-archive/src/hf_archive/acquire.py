"""Acquisition: pull explicitly selected files from the Hub, or import files already on disk.

Both paths end the same way: bytes are staged on the archive filesystem, verified against the identity
upstream declares, published into the content store, and only then recorded in the revision manifest.
A failed acquisition records nothing; blobs it already verified stay in the store and are reused by a retry.
"""

from __future__ import annotations

import errno
import shutil
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import __version__
from .errors import ArchiveError, IntegrityError, InvalidDocument, NoSpaceError, ScopeError
from .model import RevisionMetadata, TreeFile, now, select_paths, validate_repo_path
from .store import CHUNK, Store

DEFAULT_RESERVE_BYTES = 1024**3


class Hub(Protocol):
    """Where metadata and payloads come from. `SdkHub` is the real one; tests substitute their own."""

    source: str

    def fetch_metadata(self, repo_id: str, revision: str) -> RevisionMetadata: ...

    def download(self, meta: RevisionMetadata, file: TreeFile, dest: Path) -> Path:
        """Download one file into the empty scratch directory `dest` and return its path."""
        ...


@dataclass
class Result:
    repo_id: str
    commit: str
    refs: list[str]
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    acquired_total: int = 0
    tree_total: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "commit": self.commit,
            "refs": self.refs,
            "files": self.files,
            "acquired_total": self.acquired_total,
            "tree_total": self.tree_total,
        }


def require_public(meta: RevisionMetadata) -> None:
    """Refuse anything not positively known to be public and ungated: the server has no access control."""
    if meta.private is not False:
        raise ScopeError(
            f"{meta.repo_id} is private or its visibility is unknown; only public repositories are archived"
        )
    if meta.gated is not False:
        raise ScopeError(f"{meta.repo_id} is gated or its gating is unknown; only ungated repositories are archived")


@contextmanager
def _io_errors() -> Iterator[None]:
    try:
        yield
    except OSError as e:
        if e.errno in (errno.ENOSPC, errno.EDQUOT):
            raise NoSpaceError(f"archive filesystem is full: {e}") from e
        raise ArchiveError(f"filesystem error: {e}") from e


def _require_space(store: Store, files: Sequence[TreeFile], reserve_bytes: int) -> None:
    """Fail before any payload moves if the contents not yet held cannot fit. Presence is judged cheaply here."""
    missing = [f for f in files if not (f.lfs_sha256 and store.has_blob(f.lfs_sha256, f.size))]
    needed = sum({file.content_key: file.size for file in missing}.values())
    free = store.free_bytes()
    if needed and needed + reserve_bytes > free:
        raise NoSpaceError(
            f"need {needed} bytes plus a {reserve_bytes}-byte reserve, but {free} bytes are free under {store.root}"
        )


def _event(action: str, source: str, paths: Sequence[str], **extra: Any) -> dict[str, Any]:
    return {
        "at": now(),
        "action": action,
        "tool": f"hf-archive {__version__}",
        "source": source,
        "paths": list(paths),
    } | extra


def _result(store: Store, meta: RevisionMetadata, acquired: Mapping[str, tuple[str, dict[str, Any]]], event) -> Result:
    manifest = store.record_revision(meta, dict(acquired), event)
    return Result(
        repo_id=meta.repo_id,
        commit=meta.commit,
        refs=list(meta.refs),
        files={
            path: {"blob": sha256, "size": meta.files[path].size, "via": provenance["via"]}
            for path, (sha256, provenance) in acquired.items()
        },
        acquired_total=manifest.acquired_count,
        tree_total=len(manifest.files),
    )


def pull(
    store: Store,
    hub: Hub,
    repo_id: str,
    revision: str = "main",
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    select_all: bool = False,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
) -> Result:
    """Acquire the selected files of one revision. Selection is explicit: patterns or `select_all`."""
    if not include and not select_all:
        raise ArchiveError("nothing selected: pass include patterns, or select_all to take the whole revision")
    meta = hub.fetch_metadata(repo_id, revision)
    require_public(meta)
    selected = select_paths(sorted(meta.files), list(include), list(exclude))
    if not selected:
        raise ArchiveError(f"selection matches no file of {meta.repo_id}@{meta.commit}")

    acquired: dict[str, tuple[str, dict[str, Any]]] = {}
    with _io_errors(), store.staging() as scratch:
        _require_space(store, [meta.files[p] for p in selected], reserve_bytes)
        for number, path in enumerate(selected):
            file = meta.files[path]
            with store.content_lock(file):
                # Checked under the lock: a concurrent acquisition of the same content may have just published it.
                sha256 = store.find_blob(file)
                via = "reuse"
                if sha256 is None:
                    dest = scratch / str(number)
                    dest.mkdir()
                    try:
                        staged = hub.download(meta, file, dest)
                    except ArchiveError:
                        raise
                    except Exception as e:
                        if isinstance(e, OSError) and e.errno in (errno.ENOSPC, errno.EDQUOT):
                            raise
                        raise ArchiveError(f"download of {path} failed: {type(e).__name__}: {e}") from e
                    sha256 = store.publish_blob(staged, file)
                    via = "pull"
            acquired[path] = (sha256, {"via": via, "at": now()})
        return _result(store, meta, acquired, _event("pull", hub.source, selected, revision=revision))


def import_directory(
    store: Store,
    meta: RevisionMetadata,
    source: Path | str,
    *,
    mapping: Mapping[str, str] | None = None,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
) -> Result:
    """Import files of `meta`'s revision from a directory, copying and verifying each one.

    `source` is a plain directory, a native HF snapshot directory, or a native HF repo cache directory
    holding `snapshots/<commit>`. Symlinks under it are followed. `mapping` names the source-relative
    path of repository files stored under a different name. Tree files with no source stay not acquired.
    """
    require_public(meta)
    mapping = dict(mapping or {})
    source = Path(source)
    if (source / "snapshots" / meta.commit).is_dir():
        source = source / "snapshots" / meta.commit
    if not source.is_dir():
        raise ArchiveError(f"import source is not a directory: {source}")
    for path, relative in mapping.items():
        if path not in meta.files:
            raise ArchiveError(f"mapped path {path!r} is not a file of {meta.repo_id}@{meta.commit}")
        try:
            validate_repo_path(relative)
        except InvalidDocument as e:
            raise ArchiveError(f"mapped source for {path!r} must be a relative path inside the source: {e}") from e

    selected = select_paths(sorted(meta.files), list(include), list(exclude))
    plan: list[tuple[TreeFile, Path, Path]] = []
    for path in selected:
        candidate = source.joinpath(*mapping.get(path, path).split("/"))
        if not candidate.exists():
            if path in mapping:
                raise ArchiveError(f"mapped source for {path!r} does not exist: {candidate}")
            continue
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file():
            raise ArchiveError(f"import source for {path!r} is not a regular file: {candidate}")
        plan.append((meta.files[path], candidate, resolved))
    if unselected := sorted(set(mapping) - set(selected)):
        raise ArchiveError(f"mapped paths are excluded by the selection: {', '.join(unselected)}")
    if not plan:
        raise ArchiveError(f"no file of {meta.repo_id}@{meta.commit} was found under {source}")

    acquired: dict[str, tuple[str, dict[str, Any]]] = {}
    with _io_errors(), store.staging() as scratch:
        _require_space(store, [file for file, _, _ in plan], reserve_bytes)
        for number, (file, candidate, resolved) in enumerate(plan):
            with store.content_lock(file):
                sha256 = store.find_blob(file)
                provenance: dict[str, Any] = {"via": "reuse", "at": now()}
                if sha256 is None:
                    if resolved.stat().st_size != file.size:
                        raise IntegrityError(
                            f"{file.path}: expected {file.size} bytes, source {candidate} has {resolved.stat().st_size}"
                        )
                    # Copied, never linked: the source stays intact and later edits to it cannot reach the archive.
                    staged = scratch / str(number)
                    with open(resolved, "rb") as fin, open(staged, "xb") as fout:
                        shutil.copyfileobj(fin, fout, CHUNK)
                    sha256 = store.publish_blob(staged, file)
                    provenance = {"via": "import", "at": now(), "source": str(candidate), "resolved": str(resolved)}
            acquired[file.path] = (sha256, provenance)
        event = _event("import", str(source), [file.path for file, _, _ in plan])
        return _result(store, meta, acquired, event)
