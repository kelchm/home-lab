"""CLI contract: arguments, exit codes, and offline import from a metadata document."""

from __future__ import annotations

import json
import os

import pytest
from conftest import COMMIT_A, COMMIT_B, metadata, sha256

from hf_archive.cli import main
from hf_archive.store import Store

WEIGHT = b"weights" * 3000
CONFIG = b"{}"
FILES = {"config.json": (CONFIG, False), "model.safetensors": (WEIGHT, True)}


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """A metadata document plus a source directory, and an SDK that must not be reached."""
    monkeypatch.setattr("hf_archive.cli._hub", lambda *args: pytest.fail("offline import must not touch the Hub"))
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(metadata("org/model", COMMIT_A, FILES).to_hub_json()))
    source = tmp_path / "source"
    source.mkdir()
    (source / "renamed.safetensors").write_bytes(WEIGHT)
    return tmp_path / "archive", meta, source


def run(*argv) -> int:
    return main([str(arg) for arg in argv])


def test_offline_import_with_mapping_then_list_and_verify(offline, capsys):
    root, meta, source = offline
    code = run(
        "import", "org/model", "--root", root, "--source", source, "--metadata", meta,
        "--map", "model.safetensors=renamed.safetensors", "--json",
    )  # fmt: skip
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["commit"] == COMMIT_A and result["refs"] == ["refs/heads/main"]
    assert result["refs_retained"] == {}
    assert result["files"] == {"model.safetensors": {"blob": sha256(WEIGHT), "size": len(WEIGHT), "via": "import"}}
    assert (result["acquired_total"], result["tree_total"]) == (1, 2)

    assert run("list", "--root", root, "--json") == 0
    listing = json.loads(capsys.readouterr().out)["repos"][0]
    assert listing["refs"] == {"refs/heads/main": COMMIT_A}
    assert listing["revisions"][0]["files_acquired"] == 1

    assert run("verify", "--root", root, "--json") == 0
    assert json.loads(capsys.readouterr().out) == {"checked": 1, "problems": []}


def test_verify_exits_3_and_names_the_corrupt_file(offline, capsys):
    root, meta, source = offline
    run(
        "import",
        "org/model",
        "--root",
        root,
        "--source",
        source,
        "--metadata",
        meta,
        "--map",
        "model.safetensors=renamed.safetensors",
    )
    blob = Store(root).blob_path(sha256(WEIGHT))
    os.chmod(blob, 0o644)
    blob.write_bytes(bytes(len(WEIGHT)))
    capsys.readouterr()
    assert run("verify", "--root", root, "--json") == 3
    assert json.loads(capsys.readouterr().out)["problems"][0]["path"] == "model.safetensors"


def test_corrupt_source_exits_3_and_private_metadata_exits_4(offline, tmp_path, capsys):
    root, meta, source = offline
    (source / "model.safetensors").write_bytes(bytes(len(WEIGHT)))
    assert run("import", "org/model", "--root", root, "--source", source, "--metadata", meta) == 3
    assert "expected LFS sha256" in capsys.readouterr().err

    doc = json.loads(meta.read_text())
    doc["info"]["private"] = True
    meta.write_text(json.dumps(doc))
    assert run("import", "org/model", "--root", root, "--source", source, "--metadata", meta) == 4
    assert Store(root).list_repos() == []


def test_metadata_for_another_repo_or_commit_is_refused(offline):
    root, meta, source = offline
    assert run("import", "org/other", "--root", root, "--source", source, "--metadata", meta) == 1
    assert (
        run("import", "org/model", "--root", root, "--source", source, "--metadata", meta, "--revision", "b" * 40) == 1
    )


def test_missing_root_and_usage_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HF_ARCHIVE_ROOT", raising=False)
    assert run("list") == 1
    assert run("serve", "--root", tmp_path / "not-an-archive") == 1
    with pytest.raises(SystemExit) as usage:
        run("pull")
    assert usage.value.code == 2


def test_serve_drops_inherited_token(offline, monkeypatch):
    root, meta, source = offline
    run(
        "import",
        "org/model",
        "--root",
        root,
        "--source",
        source,
        "--metadata",
        meta,
        "--map",
        "model.safetensors=renamed.safetensors",
    )
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    monkeypatch.setattr("hf_archive.server.ArchiveServer.serve_forever", lambda self: None)
    assert run("serve", "--root", root, "--host", "127.0.0.1", "--port", "0") == 0
    assert "HF_TOKEN" not in os.environ


NEWER = b"newer weights" * 2000


class LiveHub:
    """Stands in for the SDK: metadata fetched now, naming `main` at whichever commit it currently holds."""

    def __init__(self, meta):
        self.meta = meta

    def fetch_metadata(self, repo_id, revision):
        return self.meta


@pytest.fixture
def archived_b(offline, capsys):
    """An archive whose `main` was last observed at B, plus a saved metadata document for A naming `main`."""
    root, meta, source = offline
    current = source.parent / "current"
    current.mkdir()
    (current / "model.safetensors").write_bytes(NEWER)
    current_meta = source.parent / "current.json"
    newer = metadata("org/model", COMMIT_B, {"model.safetensors": (NEWER, True)})
    current_meta.write_text(json.dumps(newer.to_hub_json()))
    assert run("import", "org/model", "--root", root, "--source", current, "--metadata", current_meta) == 0
    (source / "model.safetensors").write_bytes(WEIGHT)
    capsys.readouterr()
    return root, meta, source


def refs_of(root) -> dict[str, str]:
    return {ref.ref: ref.commit for ref in Store(root).list_refs("org/model")}


def test_offline_import_creates_a_missing_ref(offline, capsys):
    root, meta, source = offline
    (source / "model.safetensors").write_bytes(WEIGHT)
    assert run("import", "org/model", "--root", root, "--source", source, "--metadata", meta) == 0
    out = capsys.readouterr().out
    assert "refs refs/heads/main" in out and "left at" not in out
    assert refs_of(root) == {"refs/heads/main": COMMIT_A}


def test_offline_import_of_saved_metadata_does_not_roll_back_a_recorded_ref(archived_b, capsys):
    root, meta, source = archived_b
    assert run("import", "org/model", "--root", root, "--source", source, "--metadata", meta, "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["commit"], result["refs"]) == (COMMIT_A, [])
    assert result["refs_retained"] == {"refs/heads/main": COMMIT_B}
    assert refs_of(root) == {"refs/heads/main": COMMIT_B}

    # Both commits are published and intact; only the ref was left alone.
    assert run("list", "--root", root, "--json") == 0
    listing = json.loads(capsys.readouterr().out)["repos"][0]
    assert sorted(r["commit"] for r in listing["revisions"]) == [COMMIT_A, COMMIT_B]
    assert run("verify", "--root", root, "--json") == 0
    assert json.loads(capsys.readouterr().out) == {"checked": 2, "problems": []}

    assert run("import", "org/model", "--root", root, "--source", source, "--metadata", meta) == 0
    text = capsys.readouterr().out
    assert f"refs/heads/main left at {COMMIT_B}" in text and "--move-refs" in text
    assert ", refs " not in text, "the summary must not claim a ref it did not move"


def test_offline_import_with_move_refs_replaces_the_recorded_ref(archived_b, capsys):
    root, meta, source = archived_b
    args = ("import", "org/model", "--root", root, "--source", source, "--metadata", meta, "--move-refs")
    assert run(*args, "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["refs"], result["refs_retained"]) == (["refs/heads/main"], {})
    assert refs_of(root) == {"refs/heads/main": COMMIT_A}
    ref = Store(root).read_ref("org/model", "refs/heads/main")
    assert [p["commit"] for p in ref.previous] == [COMMIT_B]
    assert Store(root).resolve_revision("org/model", COMMIT_B) == COMMIT_B


def test_import_with_metadata_fetched_live_moves_the_ref_it_observed(archived_b, monkeypatch, capsys):
    root, _, source = archived_b
    live = LiveHub(metadata("org/model", COMMIT_A, FILES))
    monkeypatch.setattr("hf_archive.cli._hub", lambda *args: live)
    assert run("import", "org/model", "--root", root, "--source", source, "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["refs"], result["refs_retained"]) == (["refs/heads/main"], {})
    assert refs_of(root) == {"refs/heads/main": COMMIT_A}
