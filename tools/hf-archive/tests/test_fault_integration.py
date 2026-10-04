"""Process-level archive publication failures against actual filesystem state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_sdk_integration import (
    COMMIT,
    CONFIG,
    NEXT_COMMIT,
    WEIGHT,
    FakeHub,
    HubFile,
    clean_env,
    cli_args,
    ordinary_objects,
    pull,
    served,
    sha256,
)

FAULT_RUNNER = """
import errno, os, pathlib, runpy, signal, sys
root = pathlib.Path(sys.argv[1]).resolve()
stage, mode = sys.argv[2:4]
marker = pathlib.Path(sys.argv[4])
original_replace = os.replace
def classify(destination):
    try:
        path = pathlib.Path(destination).resolve().relative_to(root)
    except (ValueError, TypeError):
        return None
    if "refs" in path.parts:
        return "ref"
    if "revisions" in path.parts:
        return "manifest"
    if "blobs" in path.parts or "objects" in path.parts:
        return "blob"
    return None
def replace(source, destination, *args, **kwargs):
    if classify(destination) == stage:
        marker.write_text(str(destination))
        if mode == "enospc":
            raise OSError(errno.ENOSPC, "injected filesystem allocation failure", str(destination))
        if mode == "kill_before":
            os.kill(os.getpid(), signal.SIGKILL)
        if mode == "kill_after":
            original_replace(source, destination, *args, **kwargs)
            os.kill(os.getpid(), signal.SIGKILL)
    return original_replace(source, destination, *args, **kwargs)
os.replace = replace
sys.argv = ["hf_archive", *sys.argv[5:]]
runpy.run_module("hf_archive.cli", run_name="__main__")
"""


def assert_published_manifests_are_sound(root: Path):
    """Check persisted public references, not the writer's own success report."""
    manifest_paths = list(root.glob("repos/*/revisions/*.json"))
    assert manifest_paths, "no persisted manifests were found"
    for path in manifest_paths:
        manifest = json.loads(path.read_text())
        entries = manifest["files"]
        entries = entries.values() if isinstance(entries, dict) else entries
        for entry in entries:
            digest = entry.get("blob")
            if digest is None:
                continue
            objects = ordinary_objects(root, digest)
            assert len(objects) == 1, f"{path} points to an absent or ambiguous blob {digest}"
            data = objects[0].read_bytes()
            assert sha256(data) == digest, f"{path} points to corrupt retained bytes"
            assert len(data) == entry["size"]


@pytest.mark.parametrize(
    "stage,mode",
    [
        ("blob", "enospc"),
        ("manifest", "enospc"),
        ("ref", "enospc"),
        ("blob", "kill_before"),
        ("manifest", "kill_before"),
        ("manifest", "kill_after"),
        ("ref", "kill_before"),
    ],
)
def test_publication_failure_keeps_old_ref_and_only_valid_manifests(tmp_path, stage, mode):
    root = tmp_path / "archive"
    env = clean_env(tmp_path)
    replacement = HubFile(WEIGHT.path, b"NEW-selected-weights\0" * 32768, True)
    with FakeHub() as hub:
        pull(root, hub, env)
        old_refs = {str(p.relative_to(root)): p.read_bytes() for p in root.glob("repos/*/refs/*.json")}
        assert old_refs, "published pull did not create durable refs"
        hub.repos[("acme/tiny", NEXT_COMMIT)] = [CONFIG, replacement]
        hub.refs[("acme/tiny", "main")] = NEXT_COMMIT
        marker = tmp_path / "fault-observed"
        args = cli_args(
            root,
            "pull",
            "acme/tiny",
            "--revision",
            "main",
            "--endpoint",
            hub.endpoint,
            "--include",
            CONFIG.path,
            "--include",
            WEIGHT.path,
        )
        result = subprocess.run(
            [sys.executable, "-c", FAULT_RUNNER, str(root), stage, mode, str(marker), *args],
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert marker.exists(), f"fault did not reach {stage}: {result.stdout}\n{result.stderr}"
        assert result.returncode != 0, "publication reported success despite the injected failure"
        if mode.startswith("kill"):
            assert result.returncode == -9, f"process was not killed at publication boundary: {result.stderr}"
        else:
            assert result.returncode == 5, result.stderr
            assert "[Errno 28]" in result.stderr, result.stderr
        assert {str(p.relative_to(root)): p.read_bytes() for p in root.glob("repos/*/refs/*.json")} == old_refs
        assert_published_manifests_are_sound(root)
        # Readers can restart immediately after failure, before another writer repairs abandoned staging.
        from urllib.request import urlopen

        failure_server = tmp_path / "failure-server"
        failure_server.mkdir()
        with (
            served(root, failure_server) as endpoint,
            urlopen(endpoint + f"/acme/tiny/resolve/main/{WEIGHT.path}") as response,
        ):
            assert response.headers["X-Repo-Commit"] == COMMIT
            assert response.read() == WEIGHT.data
        # A new process must recover stale staging/locks and finish the requested update.
        pull(root, hub, env)
        assert not list((root / "staging").iterdir()), "crashed acquisition left unrecovered staging"
        assert_published_manifests_are_sound(root)
    with (
        served(root, tmp_path) as endpoint,
        urlopen(endpoint + f"/acme/tiny/resolve/main/{WEIGHT.path}") as response,
    ):
        assert response.headers["X-Repo-Commit"] == NEXT_COMMIT
        assert response.read() == replacement.data


@pytest.mark.parametrize("after_first_read", [False, True])
def test_corrupted_retained_blob_is_refused_before_range_bytes_escape(tmp_path, after_first_read):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    root = tmp_path / "archive"
    with FakeHub() as hub:
        pull(root, hub, clean_env(tmp_path), patterns=[WEIGHT.path])
    blob = ordinary_objects(root, sha256(WEIGHT.data))[0]

    def corrupt():
        os.chmod(blob, 0o644)
        blob.write_bytes(b"X" * len(WEIGHT.data))

    if not after_first_read:
        corrupt()
    with served(root, tmp_path) as endpoint:
        url = endpoint + f"/acme/tiny/resolve/main/{WEIGHT.path}"
        if after_first_read:
            with urlopen(Request(url, headers={"Range": "bytes=0-99"})) as response:
                assert response.status == 206
                assert response.read() == WEIGHT.data[:100]
            corrupt()
        for method in ["HEAD", "GET"]:
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url, headers={"Range": "bytes=0-99"}, method=method))
            assert error.value.code == 500
            assert error.value.headers["X-Error-Code"] == "ArchiveInconsistent"
            assert error.value.read() != b"X" * 100
