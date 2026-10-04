"""HTTP behaviour over real sockets: what a client can and cannot get out of an archive."""

from __future__ import annotations

import errno
import http.client
import json
import os
import sys
import threading
import time

import pytest
from conftest import COMMIT_A, COMMIT_B, git_oid, sha256, tree_entry

from hf_archive import server as server_module
from hf_archive.acquire import pull
from hf_archive.model import METADATA_SCHEMA, RevisionMetadata
from hf_archive.server import HubError, make_server

WEIGHT = bytes(range(256)) * 400
UNACQUIRED = b"other quantization" * 100
CONFIG = b'{"model_type": "tiny"}'
XET = "c" * 64


@pytest.fixture
def served(store, hub):
    """An archive holding org/model@A (config, sub/weight acquired; q8 not) on `main`, behind a live server."""
    tree = [
        tree_entry("config.json", CONFIG),
        tree_entry("sub/model.safetensors", WEIGHT, lfs=True, xet=XET),
        tree_entry("q8.gguf", UNACQUIRED, lfs=True, xet="d" * 64),
        {"type": "directory", "path": "sub", "oid": git_oid(b"sub")},
    ]
    info = {"id": "org/model", "sha": COMMIT_A, "private": False, "gated": False, "tags": ["gguf"]}
    meta = RevisionMetadata.from_hub_json(
        {"schema": METADATA_SCHEMA, "schema_version": 1, "info": info, "tree": tree, "refs": ["refs/heads/main"]}
    )
    hub.revisions[("org/model", "main")] = meta
    hub.content |= {("org/model", "config.json"): CONFIG, ("org/model", "sub/model.safetensors"): WEIGHT}
    pull(store, hub, "org/model", include=["config.json", "sub/*"])

    server = make_server(store.root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def request(server, method, path, headers=None, body=None):
    connection = http.client.HTTPConnection(*server.server_address[:2], timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        connection.close()


def get_json(server, path):
    status, _, body = request(server, "GET", path)
    assert status == 200, body
    return json.loads(body)


RESOLVE = "/org/model/resolve/main/sub/model.safetensors"


def test_head_reports_upstream_identity_for_lfs_and_git_files(served):
    status, headers, body = request(served, "HEAD", RESOLVE)
    assert (status, body) == (200, b"")
    assert headers["etag"] == f'"{sha256(WEIGHT)}"'
    assert headers["x-repo-commit"] == COMMIT_A
    assert int(headers["content-length"]) == len(WEIGHT)

    status, headers, _ = request(served, "HEAD", f"/org/model/resolve/{COMMIT_A}/config.json")
    assert status == 200
    assert headers["etag"] == f'"{git_oid(CONFIG)}"'
    assert int(headers["content-length"]) == len(CONFIG)


def test_get_returns_exact_bytes_and_supports_resume_ranges(served):
    assert request(served, "GET", RESOLVE)[2] == WEIGHT

    status, headers, body = request(served, "GET", RESOLVE, {"Range": "bytes=1000-"})
    assert status == 206
    assert body == WEIGHT[1000:]
    assert headers["content-range"] == f"bytes 1000-{len(WEIGHT) - 1}/{len(WEIGHT)}"

    assert request(served, "GET", RESOLVE, {"Range": "bytes=10-19"})[2] == WEIGHT[10:20]
    assert request(served, "GET", RESOLVE, {"Range": "bytes=-7"})[2] == WEIGHT[-7:]
    assert request(served, "GET", RESOLVE, {"Range": f"bytes=5-{len(WEIGHT) + 999}"})[2] == WEIGHT[5:]
    assert request(served, "GET", RESOLVE, {"Range": f"bytes={len(WEIGHT)}-"})[0] == 416
    # A resume against a different version of the file must restart, not splice.
    status, _, body = request(served, "GET", RESOLVE, {"Range": "bytes=1000-", "If-Range": '"stale"'})
    assert (status, body) == (200, WEIGHT)


def test_no_response_leaks_xet_signals(served):
    responses = [
        request(served, "HEAD", RESOLVE),
        request(served, "GET", RESOLVE),
        request(served, "GET", f"/api/models/org/model/tree/{COMMIT_A}?recursive=true&expand=true"),
        request(served, "GET", "/api/models/org/model/revision/main?blobs=true"),
        request(
            served, "POST", "/api/models/org/model/paths-info/main", body="paths=sub/model.safetensors&expand=True"
        ),
    ]
    for status, headers, body in responses:
        assert status == 200
        assert not [name for name in headers if "xet" in name], headers
        assert "link" not in headers
        if headers["content-type"] == "application/json":
            assert b"xet" not in body.lower() and XET.encode() not in body


def test_unacquired_file_is_not_reported_as_absent(served):
    status, headers, _ = request(served, "HEAD", "/org/model/resolve/main/q8.gguf")
    assert status == 404
    assert headers["x-error-code"] == "EntryNotAcquired"
    assert "hf-archive pull" in headers["x-error-message"]
    assert request(served, "GET", "/org/model/resolve/main/q8.gguf")[0] == 404


def test_absent_file_revision_and_repo_use_hub_error_codes(served):
    status, headers, _ = request(served, "HEAD", "/org/model/resolve/main/nope.bin")
    assert (status, headers["x-error-code"], headers["x-repo-commit"]) == (404, "EntryNotFound", COMMIT_A)
    for path in (f"/org/model/resolve/{COMMIT_B}/config.json", "/org/model/resolve/dev/config.json"):
        status, headers, _ = request(served, "HEAD", path)
        assert (status, headers["x-error-code"]) == (404, "RevisionNotFound")
    for path in ("/org/unknown/resolve/main/config.json", "/api/models/org/unknown", "/api/models/org/unknown/refs"):
        status, headers, _ = request(served, "GET", path)
        assert (status, headers["x-error-code"]) == (404, "RepoNotFound")


def test_metadata_describes_whole_tree_including_unacquired_files(served):
    info = get_json(served, "/api/models/org/model/revision/main")
    assert (info["id"], info["sha"], info["private"], info["gated"]) == ("org/model", COMMIT_A, False, False)
    assert [s["rfilename"] for s in info["siblings"]] == ["config.json", "q8.gguf", "sub/model.safetensors"]
    assert get_json(served, "/api/models/org/model")["sha"] == COMMIT_A

    tree = get_json(served, f"/api/models/org/model/tree/{COMMIT_A}?recursive=True&expand=False")
    files = {e["path"]: e for e in tree if e["type"] == "file"}
    assert set(files) == {"config.json", "q8.gguf", "sub/model.safetensors"}
    assert files["sub/model.safetensors"]["lfs"] == {"oid": sha256(WEIGHT), "size": len(WEIGHT), "pointerSize": 134}
    assert files["config.json"]["oid"] == git_oid(CONFIG) and "lfs" not in files["config.json"]

    top = get_json(served, "/api/models/org/model/tree/main")
    assert sorted((e["type"], e["path"]) for e in top) == [
        ("directory", "sub"),
        ("file", "config.json"),
        ("file", "q8.gguf"),
    ]
    assert [e["path"] for e in get_json(served, "/api/models/org/model/tree/main/sub")] == ["sub/model.safetensors"]

    refs = get_json(served, "/api/models/org/model/refs")
    assert refs["branches"] == [{"name": "main", "ref": "refs/heads/main", "targetCommit": COMMIT_A}]
    assert refs["tags"] == [] and refs["converts"] == []


def test_paths_info_accepts_form_and_json_bodies(served):
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    status, _, body = request(
        served, "POST", "/api/models/org/model/paths-info/main", form, "paths=config.json&paths=sub&paths=missing"
    )
    assert status == 200
    assert [(e["type"], e["path"]) for e in json.loads(body)] == [("file", "config.json"), ("directory", "sub")]

    as_json = {"Content-Type": "application/json"}
    for payload, expected in (('{"paths": ["config.json"]}', 200), ('{"paths": [{"a": 1}]}', 400), ("[1]", 400)):
        assert request(served, "POST", "/api/models/org/model/paths-info/main", as_json, payload)[0] == expected


@pytest.mark.parametrize("method", ["PUT", "POST", "DELETE", "PATCH"])
def test_no_write_route_and_no_passthrough(served, method):
    for path in (RESOLVE, "/api/models/org/model", "/api/repos/delete", "/api/models/org/model/commit/main"):
        status, _, _ = request(served, method, path, body="{}")
        assert status in (404, 405), (method, path, status)
    assert request(served, "GET", "/api/whoami-v2")[0] == 404
    assert request(served, "GET", RESOLVE)[2] == WEIGHT


def test_traversal_and_header_injection_attempts_are_refused(served, store):
    secret = store.root.parent / "secret.txt"
    secret.write_text("secret")
    for path in (
        "/org/model/resolve/main/../../../../secret.txt",
        "/org/model/resolve/main/%2e%2e/%2e%2e/secret.txt",
        "/org/model/resolve/main/sub%2F..%2F..%2Fsecret.txt",
        "/..%2F../resolve/main/x",
    ):
        status, _, body = request(served, "GET", path)
        assert status == 404 and b"secret" not in body.replace(b"secret.txt", b"")

    status, headers, _ = request(served, "GET", "/org/model/resolve/main/no%0D%0AInjected:%20yes")
    assert status == 400
    assert "injected" not in headers


def test_malformed_content_length_gets_a_response_instead_of_a_hang(served):
    for value in ("abc", "-1", "9" * 5000, str(10**9)):
        status, _, _ = request(served, "POST", "/api/models/org/model/paths-info/main", {"Content-Length": value})
        assert status in (400, 413)


def test_blob_corrupted_after_publication_is_never_served(served, store):
    blob = store.blob_path(sha256(WEIGHT))
    assert request(served, "GET", RESOLVE)[0] == 200
    os.chmod(blob, 0o644)
    corrupted = bytearray(WEIGHT)
    corrupted[5000] ^= 0xFF
    blob.write_bytes(bytes(corrupted))

    for method, headers in (("HEAD", {}), ("GET", {}), ("GET", {"Range": "bytes=0-99"})):
        status, response_headers, body = request(served, method, RESOLVE, headers)
        assert status == 500 and response_headers["x-error-code"] == "ArchiveInconsistent"
        assert bytes(corrupted[:100]) not in body

    served.archive.warm()
    assert served.archive.warm_failures == 1
    served.archive.warm()
    assert served.archive.warm_failures == 1, "a known-bad blob stays counted on later passes"


def test_manifest_pointing_git_file_at_other_valid_blob_is_refused(served, store):
    path = store._manifest_path("org/model", COMMIT_A)
    doc = json.loads(path.read_text())
    doc["files"]["config.json"]["blob"] = sha256(WEIGHT)
    doc["files"]["config.json"]["size"] = len(WEIGHT)
    path.write_text(json.dumps(doc))
    status, headers, _ = request(served, "GET", "/org/model/resolve/main/config.json")
    assert (status, headers["x-error-code"]) == (500, "ArchiveInconsistent")


def test_missing_blob_is_an_error_not_an_empty_success(served, store):
    os.unlink(store.blob_path(sha256(CONFIG)))
    status, headers, _ = request(served, "GET", "/org/model/resolve/main/config.json")
    assert (status, headers["x-error-code"]) == (500, "ArchiveInconsistent")


def _fail_reads(monkeypatch, blob):
    """Make hashing `blob` fail with EIO, as a failing disk does. Returns the files that hash was given."""
    seen = []
    real = server_module.hash_stream

    def failing(f):
        if f.name != str(blob):
            return real(f)
        seen.append(f)
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(server_module, "hash_stream", failing)
    return seen


def _replace(blob, data: bytes) -> None:
    """Put `data` at `blob` as a new file, the way a repair or a republication does."""
    fresh = blob.with_name("repaired")
    fresh.write_bytes(data)
    os.replace(fresh, blob)


def test_unreadable_blob_is_hashed_once_then_refused_until_the_file_is_replaced(served, store, monkeypatch):
    blob = store.blob_path(sha256(WEIGHT))
    seen = _fail_reads(monkeypatch, blob)

    for method in ("HEAD", "GET", "HEAD"):
        status, headers, body = request(served, method, RESOLVE)
        assert (status, headers["x-error-code"]) == (500, "ArchiveInconsistent"), "a finished failure is not pending"
        assert "retry-after" not in headers and WEIGHT[:100] not in body
    for _ in range(2):
        served.archive.warm()
        assert (served.archive.warm_state, served.archive.warm_failures) == ("ready", 1)
    health = get_json(served, "/healthz")
    assert (health["status"], health["warm"], health["warm_failures"]) == ("ok", "ready", 1)
    # One hash attempt for three requests and two passes.
    assert len(seen) == 1 and seen[0].closed
    assert not served.archive._jobs

    # The same bytes under a new inode are a different file: it is hashed again and served.
    monkeypatch.undo()
    _replace(blob, WEIGHT)
    assert request(served, "GET", RESOLVE)[2] == WEIGHT
    served.archive.warm()
    assert served.archive.warm_failures == 0


def test_blob_that_cannot_be_opened_is_refused_and_counted_without_reporting_warming(served, store, monkeypatch):
    blob = store.blob_path(sha256(WEIGHT))
    real_open = open
    states = []

    def denied(path, *args, **kwargs):
        if os.fspath(path) == str(blob):
            states.append(served.archive.warm_state)
            raise PermissionError(errno.EACCES, "Permission denied", str(blob))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(server_module, "open", denied, raising=False)
    monkeypatch.setattr(server_module.os, "access", lambda path, mode: os.fspath(path) != str(blob))

    for _ in range(2):
        status, headers, _ = request(served, "HEAD", RESOLVE)
        assert (status, headers["x-error-code"]) == (500, "ArchiveInconsistent")
        assert "retry-after" not in headers
    served.archive.warm()
    served.archive.warm()
    assert (served.archive.warm_state, served.archive.warm_failures) == ("ready", 1)
    assert states[-1] == "ready", "a pass that can hash nothing must not report warming"
    assert not served.archive._jobs and sha256(WEIGHT) not in served.archive._digests

    monkeypatch.undo()
    assert request(served, "GET", RESOLVE)[2] == WEIGHT
    served.archive.warm()
    assert served.archive.warm_failures == 0


def test_worker_open_failure_is_recorded_for_the_file_the_request_saw(served, store, monkeypatch):
    """The request opens the blob, then the hashing thread cannot: no local fstat, but still an outcome."""
    blob = store.blob_path(sha256(WEIGHT))
    file = store.read_manifest("org/model", COMMIT_A).files["sub/model.safetensors"].file
    archive = served.archive
    real_open = open
    hashes = []

    def worker_denied(path, *args, **kwargs):
        if os.fspath(path) == str(blob) and threading.current_thread().name == "verify":
            hashes.append(path)
            raise PermissionError(errno.EACCES, "Permission denied", str(blob))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(server_module, "open", worker_denied, raising=False)
    descriptors = len(os.listdir("/dev/fd"))
    for _ in range(3):
        with pytest.raises(HubError) as refused:
            archive.open_verified(sha256(WEIGHT), file, wait=None)
        assert (refused.value.status, refused.value.code) == (500, "ArchiveInconsistent")
    assert len(hashes) == 1, "an unchanged file is not hashed again"
    assert len(os.listdir("/dev/fd")) == descriptors, "refused requests must not leak the blob descriptor"
    assert archive._digests[sha256(WEIGHT)] == (archive._signature(os.stat(blob)), None)

    # A replacement is a different file, so the recorded failure does not apply to it.
    _replace(blob, WEIGHT)
    with pytest.raises(HubError):
        archive.open_verified(sha256(WEIGHT), file, wait=None)
    assert len(hashes) == 2
    monkeypatch.undo()
    _replace(blob, WEIGHT)
    with archive.open_verified(sha256(WEIGHT), file, wait=None) as f:
        assert f.read() == WEIGHT


def test_outcome_for_a_replaced_file_is_not_applied_to_the_file_a_request_opened(served, store, monkeypatch):
    """A request holding the old file, whose hash ran against its replacement, retries instead of borrowing it."""
    blob = store.blob_path(sha256(WEIGHT))
    file = store.read_manifest("org/model", COMMIT_A).files["sub/model.safetensors"].file
    archive = served.archive
    real = server_module.hash_stream
    fail = [True]

    def swapped(f):
        if fail[0]:
            raise OSError(errno.EIO, "Input/output error")
        return real(f)

    real_open = open

    def replace_before_worker(path, *args, **kwargs):
        if os.fspath(path) == str(blob) and threading.current_thread().name == "verify" and fail[0]:
            _replace(blob, WEIGHT)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(server_module, "hash_stream", swapped)
    monkeypatch.setattr(server_module, "open", replace_before_worker, raising=False)
    with pytest.raises(HubError) as stale:
        archive.open_verified(sha256(WEIGHT), file, wait=None)
    assert (stale.value.status, stale.value.code) == (503, "VerificationPending")
    # The failure belongs to the replacement, which the next request sees and is refused for.
    with pytest.raises(HubError) as refused:
        archive.open_verified(sha256(WEIGHT), file, wait=None)
    assert refused.value.status == 500

    # Replaced again while the old failure is on record: that record is for another file.
    fail[0] = False
    _replace(blob, WEIGHT)
    with archive.open_verified(sha256(WEIGHT), file, wait=None) as f:
        assert f.read() == WEIGHT


def test_blob_removed_while_its_hash_was_starting_is_refused_then_recovers(served, store, monkeypatch):
    blob = store.blob_path(sha256(WEIGHT))
    file = store.read_manifest("org/model", COMMIT_A).files["sub/model.safetensors"].file
    archive = served.archive
    real_open = open

    def unlink_before_worker(path, *args, **kwargs):
        if os.fspath(path) == str(blob) and threading.current_thread().name == "verify":
            os.unlink(blob)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(server_module, "open", unlink_before_worker, raising=False)
    for _ in range(2):
        with pytest.raises(HubError) as refused:
            archive.open_verified(sha256(WEIGHT), file, wait=None)
        assert (refused.value.status, refused.value.code) == (500, "ArchiveInconsistent")
    monkeypatch.undo()
    archive.warm()
    assert (archive.warm_state, archive.warm_failures) == ("ready", 1)

    blob.write_bytes(WEIGHT)
    with archive.open_verified(sha256(WEIGHT), file, wait=None) as f:
        assert f.read() == WEIGHT
    archive.warm()
    assert archive.warm_failures == 0


def test_slow_first_verification_answers_503_with_retry_after_then_serves(served, monkeypatch):
    release = threading.Event()
    real = server_module.hash_stream

    def slow(f):
        release.wait(10)
        return real(f)

    monkeypatch.setattr(server_module, "hash_stream", slow)
    monkeypatch.setattr(server_module, "VERIFY_WAIT", 0.1)
    fresh = make_server(served.store.root)
    threading.Thread(target=fresh.serve_forever, daemon=True).start()
    try:
        started = time.monotonic()
        status, headers, body = request(fresh, "HEAD", RESOLVE)
        assert (status, headers["x-error-code"]) == (503, "VerificationPending")
        assert "retry-after" in headers and time.monotonic() - started < 5
        release.set()
        deadline = time.monotonic() + 5
        while request(fresh, "HEAD", RESOLVE)[0] != 200:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert request(fresh, "GET", RESOLVE)[2] == WEIGHT
    finally:
        release.set()
        fresh.shutdown()
        fresh.server_close()


def test_health_reports_warming_until_every_blob_is_verified(served):
    assert get_json(served, "/healthz")["warm"] == "off"
    served.archive.warm_state = "warming"
    status, _, body = request(served, "GET", "/healthz")
    assert status == 503 and json.loads(body)["status"] == "warming"
    served.archive.warm()
    health = get_json(served, "/healthz")
    assert (health["status"], health["warm"], health["warm_failures"], health["repos"]) == ("ok", "ready", 0, 1)


def test_restarted_server_serves_pin_and_last_known_ref_without_writing(served, store):
    before = sorted((str(p), p.stat().st_mtime_ns) for p in store.root.rglob("*") if "locks" not in p.parts)
    restarted = make_server(store.root)
    threading.Thread(target=restarted.serve_forever, daemon=True).start()
    try:
        assert request(restarted, "GET", RESOLVE)[2] == WEIGHT
        assert request(restarted, "GET", f"/org/model/resolve/{COMMIT_A}/config.json")[2] == CONFIG
        assert get_json(restarted, "/api/models/org/model/revision/main")["sha"] == COMMIT_A
    finally:
        restarted.shutdown()
        restarted.server_close()
    after = sorted((str(p), p.stat().st_mtime_ns) for p in store.root.rglob("*") if "locks" not in p.parts)
    assert before == after


def test_server_module_has_no_upstream_client():
    """The server must not be able to call upstream: importing it loads neither the SDK nor an HTTP client."""
    import subprocess

    clients = {"huggingface_hub", "httpx2", "urllib3", "requests", "hf_xet"}
    code = f"import sys, hf_archive.server; print(sorted({clients!r} & {{m.split('.')[0] for m in sys.modules}}))"
    assert subprocess.check_output([sys.executable, "-c", code], text=True).strip() == "[]"
