#!/usr/bin/env python3
"""Materialize the experiment's pinned recipe Git objects as read-only API JSON."""

import argparse
import base64
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath


COMMIT = "5bbb0be4e604768499ccbcf82bbea181575c31d6"
REPOSITORY = "CarstVaartjes/vonk-forge-recipes"


def git(repository: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repository), *arguments])


def object_sha(kind: str, content: bytes) -> str:
    return hashlib.sha1(
        f"{kind} {len(content)}\0".encode() + content, usedforsecurity=False
    ).hexdigest()


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError(f"Unsafe catalog path: {value!r}")
    return value


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True, help="Local Git checkout")
    parser.add_argument("--output", type=Path, required=True, help="New cache directory")
    parser.add_argument("--commit", default=COMMIT, help="Must match the experiment pin")
    args = parser.parse_args()
    if args.commit != COMMIT:
        parser.error(f"This controller requires the validated commit {COMMIT}")
    if args.output.exists():
        parser.error("Output already exists; generate into a new directory")

    commit_bytes = git(args.repository, "cat-file", "commit", COMMIT)
    if object_sha("commit", commit_bytes) != COMMIT:
        raise ValueError("Git commit identity does not match the experiment pin")
    tree = {}
    for entry in git(args.repository, "ls-tree", "-rz", "--full-tree", COMMIT).split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            mode, kind, sha = metadata.decode().split()
            tree[path.decode()] = (mode, kind, sha)

    blobs = {}

    def read_blob(path: str, expected_sha: str | None = None, expected_size: int | None = None) -> bytes:
        mode, kind, sha = tree[relative_path(path)]
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Catalog source is not a regular Git file: {path}")
        if expected_sha is not None and sha != expected_sha:
            raise ValueError(f"Index and Git tree disagree on blob identity: {path}")
        if sha not in blobs:
            data = git(args.repository, "cat-file", "blob", sha)
            if object_sha("blob", data) != sha:
                raise ValueError(f"Git blob identity mismatch: {path}")
            blobs[sha] = data
        data = blobs[sha]
        if expected_size is not None and len(data) != expected_size:
            raise ValueError(f"Index and Git blob disagree on byte count: {path}")
        return data

    index_bytes = read_blob("catalog-index.json")
    index_sha = object_sha("blob", index_bytes)
    index = json.loads(index_bytes)
    if index["schema_version"] != 2 or index["repository"] != REPOSITORY:
        raise ValueError("Unexpected catalog index identity")
    for context in index["source_contexts"]:
        context_path = relative_path(context["context_path"])
        for source in context["files"]:
            path = f"{context_path}/{relative_path(source['path'])}"
            read_blob(path, source["blob_sha"], source["size"])

    # Verify every referenced object before creating output. Working-tree files
    # are never read, and only objects in the pinned commit's tree are served.
    api_root = args.output / "repos" / REPOSITORY
    payloads = {
        f"commits/{COMMIT}": {"sha": COMMIT},
        "contents/catalog-index.json": {
            "type": "file", "sha": index_sha, "size": len(index_bytes),
            "encoding": "none", "content": "",
        },
    }
    payloads.update({
        f"git/blobs/{sha}": {
            "sha": sha, "size": len(data), "encoding": "base64",
            "content": base64.b64encode(data).decode("ascii"),
        }
        for sha, data in sorted(blobs.items())
    })
    total_bytes = 0
    digest = hashlib.sha256()
    for relative, payload in sorted(payloads.items()):
        path = api_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json_bytes(payload)
        path.write_bytes(encoded)
        total_bytes += len(encoded)
        digest.update(relative.encode() + b"\0" + encoded)
    print(json.dumps({
        "repository": REPOSITORY, "commit": COMMIT, "index_blob_sha": index_sha,
        "recipes": len(index["recipes"]), "catalog_entities": len(index["catalog_entities"]),
        "source_contexts": len(index["source_contexts"]), "unique_blobs": len(blobs),
        "files": len(payloads), "bytes": total_bytes,
        "cache_sha256": digest.hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
