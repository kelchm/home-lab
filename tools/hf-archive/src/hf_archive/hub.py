"""Hub access through `huggingface_hub`, used only by acquisition commands.

The SDK is imported lazily after its environment is pointed at a scratch home, so its own caches live in
the archive's staging area and disappear with it. The archive, not the SDK cache, is the retained copy.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .errors import ArchiveError
from .model import COMMIT_RE, METADATA_SCHEMA, SCHEMA_VERSION, RevisionMetadata, TreeFile

DEFAULT_ENDPOINT = "https://huggingface.co"


def _timestamp(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z") if value is not None else None


class SdkHub:
    def __init__(self, home: Path, *, token: str | None = None, endpoint: str = DEFAULT_ENDPOINT):
        """`home` is a scratch directory on the archive filesystem that becomes the SDK's HF_HOME."""
        self._home = Path(home)
        # Never fall back to a token stored on this machine, and never to HF_ENDPOINT, which on a client
        # of the archive points back at the archive itself.
        self._token: str | bool = token or False
        self._endpoint = endpoint.rstrip("/")
        self.source = self._endpoint

        os.environ["HF_HOME"] = str(self._home)
        os.environ["HF_HUB_CACHE"] = str(self._home / "hub")
        os.environ["HF_XET_CACHE"] = str(self._home / "xet")
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ.setdefault("HF_XET_CHUNK_CACHE_SIZE_BYTES", "0")
        import huggingface_hub
        import huggingface_hub.errors
        import huggingface_hub.hf_api

        self._sdk = huggingface_hub
        self._api = huggingface_hub.HfApi(
            endpoint=self._endpoint, token=self._token, library_name="hf-archive", library_version=__version__
        )

    def fetch_metadata(self, repo_id: str, revision: str) -> RevisionMetadata:
        errors = self._sdk.errors
        try:
            info = self._api.model_info(repo_id, revision=revision)
            if info.sha is None:
                raise ArchiveError(f"{self.source} returned no commit for {repo_id}@{revision}")
            tree = list(self._api.list_repo_tree(repo_id, recursive=True, revision=info.sha, repo_type="model"))
            refs = self._refs_naming(repo_id, revision, info.sha)
        except errors.RepositoryNotFoundError as e:
            raise ArchiveError(f"{repo_id} was not found at {self.source}, or is not public") from e
        except errors.RevisionNotFoundError as e:
            raise ArchiveError(f"revision {revision!r} of {repo_id} was not found at {self.source}") from e
        except ArchiveError:
            raise
        except Exception as e:
            raise ArchiveError(f"could not read metadata of {repo_id}@{revision}: {type(e).__name__}: {e}") from e

        info_doc: dict[str, Any] = {
            "id": repo_id,
            "sha": info.sha,
            "private": info.private,
            "gated": info.gated,
            "author": info.author,
            "disabled": info.disabled,
            "lastModified": _timestamp(info.last_modified),
            "createdAt": _timestamp(info.created_at),
            "tags": info.tags,
            "pipeline_tag": info.pipeline_tag,
            "library_name": info.library_name,
            "upstream_id": info.id if info.id != repo_id else None,
        }
        entries: list[dict[str, Any]] = []
        for item in tree:
            if isinstance(item, self._sdk.hf_api.RepoFolder):
                entries.append({"type": "directory", "path": item.path, "oid": item.tree_id})
                continue
            entry: dict[str, Any] = {"type": "file", "path": item.path, "oid": item.blob_id, "size": item.size}
            if item.lfs is not None:
                entry["lfs"] = {"oid": item.lfs.sha256, "size": item.lfs.size, "pointerSize": item.lfs.pointer_size}
            if item.xet_hash is not None:
                entry["xetHash"] = item.xet_hash
            entries.append(entry)
        return RevisionMetadata.from_hub_json(
            {
                "schema": METADATA_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "info": info_doc,
                "tree": entries,
                "refs": refs,
            }
        )

    def _refs_naming(self, repo_id: str, revision: str, commit: str) -> list[str]:
        """Full names of the upstream refs the requested revision denotes, all confirmed to point at `commit`."""
        if COMMIT_RE.fullmatch(revision):
            return []
        refs = self._api.list_repo_refs(
            repo_id, repo_type="model", include_pull_requests=revision.startswith("refs/pr/")
        )
        named = [
            ref
            for ref in [*refs.branches, *refs.tags, *refs.converts, *(refs.pull_requests or [])]
            if revision in (ref.name, ref.ref)
        ]
        if any(ref.target_commit != commit for ref in named):
            raise ArchiveError(f"{repo_id}@{revision} moved while its metadata was being read; retry")
        return [ref.ref for ref in named]

    def download(self, meta: RevisionMetadata, file: TreeFile, dest: Path) -> Path:
        path = Path(
            self._sdk.hf_hub_download(
                meta.repo_id,
                filename=file.path,
                revision=meta.commit,
                local_dir=dest,
                cache_dir=self._home / "hub",
                token=self._token,
                endpoint=self._endpoint,
            )
        )
        if not path.resolve().is_relative_to(dest.resolve()):
            raise ArchiveError(f"download of {file.path} landed outside its staging directory: {path}")
        return path
