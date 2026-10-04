"""Content store and publication: what may be reused, what gets published, and what survives failure."""

from __future__ import annotations

import json
import os
import threading

import pytest
from conftest import COMMIT_A, COMMIT_B, git_oid, metadata, sha256

from hf_archive.errors import ArchiveError, IntegrityError, InvalidDocument
from hf_archive.model import RevisionMetadata, TreeFile, validate_repo_id, validate_repo_path
from hf_archive.store import Store

GOOD = b"good weights" * 1000
EVIL = b"evil weights" * 1000


def lfs_file(data: bytes, path: str = "model.bin", xet: str | None = None) -> TreeFile:
    return TreeFile(path, len(data), git_oid(b"pointer"), lfs_sha256=sha256(data), lfs_pointer_size=134, xet_hash=xet)


def git_file(data: bytes, path: str = "config.json") -> TreeFile:
    return TreeFile(path, len(data), git_oid(data))


def stage(store: Store, scratch, data: bytes, name: str = "staged"):
    path = scratch / name
    path.write_bytes(data)
    return path


def publish(store: Store, data: bytes, file: TreeFile) -> str:
    with store.staging() as scratch:
        return store.publish_blob(stage(store, scratch, data), file)


def test_publish_rejects_same_size_corruption_and_leaves_nothing(store):
    with store.staging() as scratch:
        staged = stage(store, scratch, EVIL)
        with pytest.raises(IntegrityError):
            store.publish_blob(staged, lfs_file(GOOD))
        assert not staged.exists()
    assert not list(store.blobs_dir.rglob("*"))
    assert store.find_blob(lfs_file(GOOD)) is None


def test_git_file_is_checked_against_git_blob_id_not_sha256(store):
    data = b'{"a": 1}'
    assert publish(store, data, git_file(data)) == sha256(data)
    with pytest.raises(IntegrityError):
        publish(store, b'{"a": 2}', git_file(data))


def test_lfs_pointer_oid_is_not_compared_with_content(store):
    # The Git oid of an LFS file names its pointer; content that hashes to it must not satisfy the LFS identity.
    file = TreeFile("model.bin", len(EVIL), git_oid(EVIL), lfs_sha256=sha256(GOOD), lfs_pointer_size=134)
    with pytest.raises(IntegrityError):
        publish(store, EVIL, file)


def test_published_blob_is_read_only_plain_file_with_identical_bytes(store):
    digest = publish(store, GOOD, lfs_file(GOOD))
    path = store.blob_path(digest)
    assert path.read_bytes() == GOOD
    assert os.stat(path).st_mode & 0o222 == 0
    assert not path.is_symlink()


def test_publish_refuses_files_outside_staging(store, tmp_path):
    outside = tmp_path / "outside.bin"
    outside.write_bytes(GOOD)
    with pytest.raises(ArchiveError):
        store.publish_blob(outside, lfs_file(GOOD))
    assert outside.read_bytes() == GOOD
    with store.staging() as scratch:
        link = scratch / "link"
        link.symlink_to(outside)
        with pytest.raises(ArchiveError):
            store.publish_blob(link, lfs_file(GOOD))


def test_same_size_evil_bytes_under_good_name_are_not_reused(store):
    """A blob file named for GOOD's sha256 but holding other bytes must never count as GOOD."""
    path = store.blob_path(sha256(GOOD))
    path.parent.mkdir(parents=True)
    path.write_bytes(EVIL)

    assert store.find_blob(lfs_file(GOOD)) is None
    assert not path.exists(), "corrupt blob must leave the content store"
    assert [p.read_bytes() for p in store.quarantine_dir.iterdir()] == [EVIL]


def test_publish_replaces_corrupt_existing_blob_instead_of_keeping_it(store):
    path = store.blob_path(sha256(GOOD))
    path.parent.mkdir(parents=True)
    path.write_bytes(EVIL)

    assert publish(store, GOOD, lfs_file(GOOD)) == sha256(GOOD)
    assert path.read_bytes() == GOOD


def test_tampered_git_alias_cannot_substitute_other_valid_content(store):
    """An alias pointing a Git oid at a different, correctly named, same-size blob is only a hint."""
    wanted, other = b"AAAA-config", b"BBBB-config"
    publish(store, other, git_file(other, "other.json"))
    alias = store._alias_path("git-sha1", git_oid(wanted))
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.write_text(json.dumps({"kind": "git-sha1", "id": git_oid(wanted), "sha256": sha256(other), "size": 11}))

    assert store.find_blob(git_file(wanted)) is None
    assert store.find_blob(git_file(other, "other.json")) == sha256(other), "the valid blob must be left alone"


def test_xet_hash_is_recorded_but_never_locates_content(store):
    # Deliberately make the Xet hash equal the SHA-256 of other stored content.
    other = b"other content"
    publish(store, other, lfs_file(other, "other.bin"))
    file = lfs_file(GOOD, xet=sha256(other))
    assert store.find_blob(file) is None
    publish(store, GOOD, file)
    alias = json.loads(store._alias_path("xet", sha256(other)).read_text())
    assert alias["sha256"] == sha256(GOOD)
    assert store.find_blob(lfs_file(other, "other.bin")) == sha256(other)


def test_manifest_is_not_published_when_a_referenced_blob_is_missing(store):
    meta = metadata("org/model", COMMIT_A, {"model.bin": (GOOD, True)})
    with pytest.raises(ArchiveError):
        store.record_revision(meta, {"model.bin": (sha256(GOOD), {"via": "pull"})}, {})
    assert store.read_manifest("org/model", COMMIT_A) is None
    assert store.resolve_revision("org/model", "main") is None


def test_failed_publication_keeps_previous_revision_and_ref(store):
    old = metadata("org/model", COMMIT_A, {"model.bin": (GOOD, True)})
    publish(store, GOOD, old.files["model.bin"])
    store.record_revision(old, {"model.bin": (sha256(GOOD), {"via": "pull"})}, {})

    new = metadata("org/model", COMMIT_B, {"model.bin": (EVIL, True)})
    with pytest.raises(ArchiveError):
        store.record_revision(new, {"model.bin": (sha256(EVIL), {"via": "pull"})}, {})

    assert store.resolve_revision("org/model", "main") == COMMIT_A
    assert store.read_manifest("org/model", COMMIT_A).files["model.bin"].blob == sha256(GOOD)
    assert store.read_manifest("org/model", COMMIT_B) is None


def test_interrupted_manifest_write_leaves_old_document_readable(store, monkeypatch):
    meta = metadata("org/model", COMMIT_A, {"a.bin": (GOOD, True), "b.bin": (EVIL, True)})
    publish(store, GOOD, meta.files["a.bin"])
    publish(store, EVIL, meta.files["b.bin"])
    store.record_revision(meta, {"a.bin": (sha256(GOOD), {"via": "pull"})}, {})

    def no_space(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", no_space)
    with pytest.raises(OSError):
        store.record_revision(meta, {"b.bin": (sha256(EVIL), {"via": "pull"})}, {})
    monkeypatch.undo()

    manifest = store.read_manifest("org/model", COMMIT_A)
    assert manifest.files["a.bin"].blob == sha256(GOOD)
    assert manifest.files["b.bin"].blob is None
    leftovers = [p.name for p in store._manifest_path("org/model", COMMIT_A).parent.iterdir()]
    assert leftovers == [f"{COMMIT_A}.json"]


def test_later_acquisition_merges_and_never_drops_earlier_files(store):
    meta = metadata("org/model", COMMIT_A, {"a.bin": (GOOD, True), "b.bin": (EVIL, True)})
    publish(store, GOOD, meta.files["a.bin"])
    publish(store, EVIL, meta.files["b.bin"])
    store.record_revision(meta, {"a.bin": (sha256(GOOD), {"via": "pull"})}, {"n": 1})
    manifest, _ = store.record_revision(meta, {"b.bin": (sha256(EVIL), {"via": "import"})}, {"n": 2})
    assert {p: e.blob for p, e in manifest.files.items()} == {"a.bin": sha256(GOOD), "b.bin": sha256(EVIL)}
    assert [e["n"] for e in manifest.history] == [1, 2]


def test_conflicting_tree_for_an_archived_commit_is_refused(store):
    meta = metadata("org/model", COMMIT_A, {"a.bin": (GOOD, True)})
    publish(store, GOOD, meta.files["a.bin"])
    store.record_revision(meta, {"a.bin": (sha256(GOOD), {"via": "pull"})}, {})
    forged = metadata("org/model", COMMIT_A, {"a.bin": (EVIL, True)})
    publish(store, EVIL, forged.files["a.bin"])
    with pytest.raises(ArchiveError):
        store.record_revision(forged, {"a.bin": (sha256(EVIL), {"via": "pull"})}, {})
    assert store.read_manifest("org/model", COMMIT_A).files["a.bin"].blob == sha256(GOOD)


def test_moved_ref_keeps_history_and_old_commit_stays_resolvable(store):
    for commit, data in ((COMMIT_A, GOOD), (COMMIT_B, EVIL)):
        meta = metadata("org/model", commit, {"model.bin": (data, True)})
        publish(store, data, meta.files["model.bin"])
        _, retained = store.record_revision(meta, {"model.bin": (sha256(data), {"via": "pull"})}, {}, move_refs=True)
        assert retained == {}
    ref = store.read_ref("org/model", "refs/heads/main")
    assert ref.commit == COMMIT_B
    assert [p["commit"] for p in ref.previous] == [COMMIT_A]
    assert store.resolve_revision("org/model", COMMIT_A) == COMMIT_A


def test_recorded_ref_is_kept_unless_asked_to_move_while_missing_refs_are_created(store):
    refs = ["refs/heads/main", "refs/tags/v1"]
    first = metadata("org/model", COMMIT_A, {"model.bin": (GOOD, True)}, refs=refs[:1])
    second = metadata("org/model", COMMIT_B, {"model.bin": (EVIL, True)}, refs=refs)
    publish(store, GOOD, first.files["model.bin"])
    publish(store, EVIL, second.files["model.bin"])
    assert store.record_revision(first, {"model.bin": (sha256(GOOD), {"via": "pull"})}, {})[1] == {}

    manifest, retained = store.record_revision(second, {"model.bin": (sha256(EVIL), {"via": "import"})}, {})
    assert retained == {"refs/heads/main": COMMIT_A}
    assert manifest.commit == COMMIT_B and store.resolve_revision("org/model", COMMIT_B) == COMMIT_B
    main = store.read_ref("org/model", "refs/heads/main")
    assert (main.commit, main.previous) == (COMMIT_A, [])
    assert store.read_ref("org/model", "refs/tags/v1").commit == COMMIT_B

    before = store._ref_path("org/model", "refs/tags/v1").read_bytes()
    assert store.record_revision(second, {}, {})[1] == {"refs/heads/main": COMMIT_A}
    assert store._ref_path("org/model", "refs/tags/v1").read_bytes() == before, "an unchanged ref is not rewritten"


def test_reopened_store_sees_everything_published(store):
    meta = metadata("org/model", COMMIT_A, {"model.bin": (GOOD, True)}, refs=["refs/heads/main", "refs/tags/v1"])
    publish(store, GOOD, meta.files["model.bin"])
    store.record_revision(meta, {"model.bin": (sha256(GOOD), {"via": "pull"})}, {})

    reopened = Store(store.root)
    assert reopened.list_repos() == ["org/model"]
    assert reopened.resolve_revision("org/model", "v1") == COMMIT_A
    assert reopened.resolve_revision("org/model", "refs/tags/v1") == COMMIT_A
    assert reopened.find_blob(meta.files["model.bin"]) == sha256(GOOD)


def test_staging_of_dead_process_is_swept_but_live_staging_is_kept(store):
    dead = store.staging_dir / "999-dead"
    dead.mkdir()
    (dead / "partial").write_bytes(b"x" * 100)
    (store.staging_dir / "999-dead.lock").touch()

    with store.staging() as live:
        (live / "work").write_bytes(b"y")
        assert not dead.exists()
        store.sweep_staging()
        assert (live / "work").exists()
    assert not live.exists()
    assert list(store.staging_dir.iterdir()) == []


def test_content_lock_excludes_other_holders_of_the_same_identity(store):
    file = lfs_file(GOOD)
    inside, order = threading.Event(), []

    def second():
        with store.content_lock(file):
            order.append("second")

    with store.content_lock(file):
        thread = threading.Thread(target=second)
        thread.start()
        inside.wait(0.2)
        order.append("first")
    thread.join(5)
    assert order == ["first", "second"]


def test_init_refuses_foreign_directory_and_open_requires_archive(tmp_path):
    foreign = tmp_path / "data"
    foreign.mkdir()
    (foreign / "something").write_text("x")
    with pytest.raises(ArchiveError):
        Store.init(foreign)
    with pytest.raises(ArchiveError):
        Store(tmp_path / "missing")


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "a/../../b", "a//b", "a\\b", "", ".", "a/./b", "a\nb"])
def test_unsafe_repository_paths_are_rejected(path):
    with pytest.raises(InvalidDocument):
        validate_repo_path(path)


@pytest.mark.parametrize("repo_id", ["../x", "a/b/c", "a--b/c", "org/..", "", ".hidden/x", "a/b c"])
def test_unsafe_repo_ids_are_rejected(repo_id):
    with pytest.raises(InvalidDocument):
        validate_repo_id(repo_id)


def test_metadata_with_traversal_path_or_bad_digest_is_rejected():
    def doc(entry):
        return {
            "schema": "hf-archive.metadata",
            "schema_version": 1,
            "info": {"id": "org/model", "sha": COMMIT_A, "private": False, "gated": False},
            "tree": [entry],
        }

    with pytest.raises(InvalidDocument):
        RevisionMetadata.from_hub_json(doc({"type": "file", "path": "../../evil", "oid": "0" * 40, "size": 1}))
    bad_lfs = {"type": "file", "path": "m.bin", "oid": "0" * 40, "size": 1}
    bad_lfs["lfs"] = {"oid": "../../../etc/passwd", "size": 1, "pointerSize": 1}
    with pytest.raises(InvalidDocument):
        RevisionMetadata.from_hub_json(doc(bad_lfs))


def test_manifest_with_tampered_blob_reference_is_rejected_on_read(store):
    meta = metadata("org/model", COMMIT_A, {"model.bin": (GOOD, True)})
    publish(store, GOOD, meta.files["model.bin"])
    store.record_revision(meta, {"model.bin": (sha256(GOOD), {"via": "pull"})}, {})
    path = store._manifest_path("org/model", COMMIT_A)
    doc = json.loads(path.read_text())
    for blob in ("../../../../etc/passwd", sha256(EVIL)):
        doc["files"]["model.bin"]["blob"] = blob
        path.write_text(json.dumps(doc))
        with pytest.raises(InvalidDocument):
            store.read_manifest("org/model", COMMIT_A)
