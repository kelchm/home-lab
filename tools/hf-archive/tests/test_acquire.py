"""Pull and import: selection, reuse, concurrency, scope, and failure behaviour."""

from __future__ import annotations

import os
import threading

import pytest
from conftest import COMMIT_A, COMMIT_B, metadata, sha256

from hf_archive.acquire import import_directory, pull
from hf_archive.errors import ArchiveError, IntegrityError, NoSpaceError, ScopeError

WEIGHT = b"\x01weight-q4" * 5000
OTHER_QUANT = b"\x02weight-q8" * 9000
CONFIG = b'{"model_type": "tiny"}'
FILES = {"config.json": (CONFIG, False), "q4.gguf": (WEIGHT, True), "q8.gguf": (OTHER_QUANT, True)}


def blob_count(store) -> int:
    return sum(1 for p in store.blobs_dir.rglob("*") if p.is_file())


def test_pull_requires_explicit_selection(store, hub):
    hub.add("org/model", COMMIT_A, FILES)
    with pytest.raises(ArchiveError):
        pull(store, hub, "org/model")
    assert hub.downloads == []


def test_pull_takes_only_selected_files_and_marks_the_rest_not_acquired(store, hub):
    hub.add("org/model", COMMIT_A, FILES)
    result = pull(store, hub, "org/model", include=["*.json", "q4*"])

    assert sorted(hub.downloads) == [("org/model", "config.json"), ("org/model", "q4.gguf")]
    manifest = store.read_manifest("org/model", COMMIT_A)
    assert manifest.files["q8.gguf"].blob is None
    assert manifest.files["q4.gguf"].blob == sha256(WEIGHT)
    assert (result.acquired_total, result.tree_total) == (2, 3)
    assert store.resolve_revision("org/model", "main") == COMMIT_A


def test_corrupt_download_publishes_no_manifest_and_no_ref(store, hub):
    hub.add("org/model", COMMIT_A, FILES)
    hub.content[("org/model", "q4.gguf")] = bytes(len(WEIGHT))
    with pytest.raises(IntegrityError):
        pull(store, hub, "org/model", include=["*.json", "q4*"])

    assert store.read_manifest("org/model", COMMIT_A) is None
    assert store.resolve_revision("org/model", "main") is None
    assert list(store.staging_dir.iterdir()) == []

    # The retry needs only the file that failed: the verified config is already held.
    hub.content[("org/model", "q4.gguf")] = WEIGHT
    hub.downloads.clear()
    pull(store, hub, "org/model", include=["*.json", "q4*"])
    assert hub.downloads == [("org/model", "q4.gguf")]


def test_failed_update_keeps_old_revision_served_by_the_ref(store, hub):
    hub.add("org/model", COMMIT_A, FILES)
    pull(store, hub, "org/model", select_all=True)
    hub.add("org/model", COMMIT_B, {"config.json": (CONFIG, False), "q4.gguf": (b"new" * 999, True)})
    hub.content[("org/model", "q4.gguf")] = b"bad" * 999
    with pytest.raises(IntegrityError):
        pull(store, hub, "org/model", select_all=True)

    assert store.resolve_revision("org/model", "main") == COMMIT_A
    assert store.read_manifest("org/model", COMMIT_A).acquired_count == 3
    assert store.read_manifest("org/model", COMMIT_B) is None


def test_download_error_is_reported_and_publishes_nothing(store, hub):
    hub.add("org/model", COMMIT_A, FILES)

    def explode(file):
        raise RuntimeError("connection reset")

    hub.before_download = explode
    with pytest.raises(ArchiveError, match="connection reset"):
        pull(store, hub, "org/model", select_all=True)
    assert store.list_repos() == []


def test_content_is_shared_across_repos_revisions_and_renames(store, hub):
    hub.add("org/model", COMMIT_A, FILES)
    pull(store, hub, "org/model", include=["q4*"])
    hub.downloads.clear()

    # Another repository carries the same weight under a different name, and a new revision keeps it.
    hub.add("mirror/renamed", COMMIT_B, {"weights/model-Q4.gguf": (WEIGHT, True), "config.json": (CONFIG, False)})
    result = pull(store, hub, "mirror/renamed", select_all=True)

    assert hub.downloads == [("mirror/renamed", "config.json")]
    assert result.files["weights/model-Q4.gguf"]["via"] == "reuse"
    assert blob_count(store) == 2


def test_regular_git_file_is_reused_through_its_alias_without_download(store, hub):
    hub.add("org/a", COMMIT_A, {"config.json": (CONFIG, False)})
    hub.add("org/b", COMMIT_B, {"conf/renamed.json": (CONFIG, False)})
    pull(store, hub, "org/a", select_all=True)
    pull(store, hub, "org/b", select_all=True)
    assert hub.downloads == [("org/a", "config.json")]


def test_concurrent_pulls_of_the_same_content_download_it_once(store, hub):
    hub.add("org/a", COMMIT_A, {"a.gguf": (WEIGHT, True)})
    hub.add("org/b", COMMIT_B, {"b.gguf": (WEIGHT, True)})
    barrier, release = threading.Barrier(2, timeout=5), threading.Event()
    hub.before_download = lambda file: release.wait(5)
    errors = []

    def run(repo_id):
        try:
            barrier.wait()
            pull(store, hub, repo_id, select_all=True)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run, args=(repo,)) for repo in ("org/a", "org/b")]
    for thread in threads:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(10)

    assert errors == []
    assert len(hub.downloads) == 1
    assert store.read_manifest("org/a", COMMIT_A).files["a.gguf"].blob == sha256(WEIGHT)
    assert store.read_manifest("org/b", COMMIT_B).files["b.gguf"].blob == sha256(WEIGHT)


def test_corrupted_stored_blob_is_downloaded_again_rather_than_reused(store, hub):
    hub.add("org/model", COMMIT_A, FILES)
    pull(store, hub, "org/model", include=["q4*"])
    blob = store.blob_path(sha256(WEIGHT))
    os.chmod(blob, 0o644)
    blob.write_bytes(bytes(len(WEIGHT)))

    hub.add("org/other", COMMIT_B, {"w.gguf": (WEIGHT, True)})
    hub.downloads.clear()
    result = pull(store, hub, "org/other", select_all=True)

    assert hub.downloads == [("org/other", "w.gguf")]
    assert result.files["w.gguf"]["via"] == "pull"
    assert blob.read_bytes() == WEIGHT


@pytest.mark.parametrize("flags", [{"private": True}, {"gated": "auto"}, {"gated": True}, {"private": None}])
def test_private_gated_or_unknown_visibility_is_refused_before_any_download(store, hub, flags):
    meta = hub.add("org/secret", COMMIT_A, FILES)
    for name, value in flags.items():
        setattr(meta, name, value)
    with pytest.raises(ScopeError):
        pull(store, hub, "org/secret", select_all=True)
    with pytest.raises(ScopeError):
        import_directory(store, meta, store.root)
    assert hub.downloads == []
    assert store.list_repos() == []


def test_insufficient_space_fails_before_downloading(store, hub, monkeypatch):
    hub.add("org/model", COMMIT_A, FILES)
    monkeypatch.setattr(store, "free_bytes", lambda: len(WEIGHT) + 10)
    with pytest.raises(NoSpaceError):
        pull(store, hub, "org/model", include=["q4*"], reserve_bytes=1000)
    assert hub.downloads == []


def test_disk_full_during_download_is_reported_as_no_space_and_cleans_staging(store, hub):
    hub.add("org/model", COMMIT_A, FILES)

    def full(file):
        raise OSError(28, "No space left on device")

    hub.before_download = full
    with pytest.raises(NoSpaceError):
        pull(store, hub, "org/model", select_all=True, reserve_bytes=0)
    assert list(store.staging_dir.iterdir()) == []
    assert store.list_repos() == []


# --- import ------------------------------------------------------------------------------------------


def native_cache(root, commit, files):
    """Lay files out like the HF cache: snapshots/<commit>/<path> symlinked to blobs/<etag>."""
    repo = root / "models--org--model"
    (repo / "blobs").mkdir(parents=True)
    for path, (data, _) in files.items():
        blob = repo / "blobs" / sha256(data)
        blob.write_bytes(data)
        link = repo / "snapshots" / commit / path
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(blob, link.parent))
    return repo


def test_import_of_partial_native_snapshot_follows_symlinks_and_keeps_source(store, tmp_path):
    meta = metadata("org/model", COMMIT_A, FILES | {"sub/tokenizer.json": (b"{}", False)})
    cached = {"config.json": FILES["config.json"], "q4.gguf": FILES["q4.gguf"]}
    repo = native_cache(tmp_path / "hub", COMMIT_A, cached)

    result = import_directory(store, meta, repo)

    assert sorted(result.files) == ["config.json", "q4.gguf"]
    manifest = store.read_manifest("org/model", COMMIT_A)
    assert manifest.files["q8.gguf"].blob is None and manifest.files["sub/tokenizer.json"].blob is None
    assert manifest.files["q4.gguf"].acquired["via"] == "import"
    assert manifest.files["q4.gguf"].acquired["resolved"].endswith(sha256(WEIGHT))
    # Copied, not linked: the source is intact and shares no inode with the archive.
    source_blob = repo / "blobs" / sha256(WEIGHT)
    assert source_blob.read_bytes() == WEIGHT
    assert os.stat(source_blob).st_ino != os.stat(store.blob_path(sha256(WEIGHT))).st_ino
    assert os.stat(source_blob).st_nlink == 1


def test_import_plain_directory_with_renamed_weight(store, tmp_path):
    meta = metadata("org/model", COMMIT_A, FILES)
    source = tmp_path / "plain"
    source.mkdir()
    (source / "my-local-name.gguf").write_bytes(WEIGHT)

    result = import_directory(store, meta, source, mapping={"q4.gguf": "my-local-name.gguf"})
    assert list(result.files) == ["q4.gguf"]
    assert store.read_manifest("org/model", COMMIT_A).files["config.json"].blob is None


def test_import_rejects_same_size_corrupt_source_and_publishes_nothing(store, tmp_path):
    meta = metadata("org/model", COMMIT_A, FILES)
    source = tmp_path / "plain"
    source.mkdir()
    (source / "config.json").write_bytes(CONFIG)
    (source / "q4.gguf").write_bytes(bytes(len(WEIGHT)))

    with pytest.raises(IntegrityError):
        import_directory(store, meta, source)
    assert store.read_manifest("org/model", COMMIT_A) is None
    assert store.resolve_revision("org/model", "main") is None
    assert (source / "q4.gguf").read_bytes() == bytes(len(WEIGHT))


def test_import_into_second_repo_reuses_blobs_without_copying(store, tmp_path, monkeypatch):
    meta = metadata("org/model", COMMIT_A, FILES)
    source = tmp_path / "plain"
    source.mkdir()
    (source / "q4.gguf").write_bytes(WEIGHT)
    import_directory(store, meta, source)

    other = metadata("mirror/model", COMMIT_B, {"weights.gguf": (WEIGHT, True)})
    result = import_directory(store, other, source, mapping={"weights.gguf": "q4.gguf"})
    assert result.files["weights.gguf"]["via"] == "reuse"
    assert blob_count(store) == 1


def test_import_errors_on_missing_mapped_source_bad_mapping_and_empty_match(store, tmp_path):
    meta = metadata("org/model", COMMIT_A, FILES)
    source = tmp_path / "plain"
    source.mkdir()
    (source / "unrelated.txt").write_text("x")
    with pytest.raises(ArchiveError, match="does not exist"):
        import_directory(store, meta, source, mapping={"q4.gguf": "absent.gguf"})
    with pytest.raises(ArchiveError, match="relative path"):
        import_directory(store, meta, source, mapping={"q4.gguf": "../outside.gguf"})
    with pytest.raises(ArchiveError, match="not a file of"):
        import_directory(store, meta, source, mapping={"nope.gguf": "unrelated.txt"})
    with pytest.raises(ArchiveError, match="no file of"):
        import_directory(store, meta, source)
    assert store.list_repos() == []


def test_source_modified_after_import_does_not_change_archive(store, tmp_path):
    meta = metadata("org/model", COMMIT_A, FILES)
    source = tmp_path / "plain"
    source.mkdir()
    (source / "q4.gguf").write_bytes(WEIGHT)
    import_directory(store, meta, source)
    (source / "q4.gguf").write_bytes(bytes(len(WEIGHT)))
    assert store.blob_path(sha256(WEIGHT)).read_bytes() == WEIGHT
