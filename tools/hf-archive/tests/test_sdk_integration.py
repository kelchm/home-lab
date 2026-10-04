"""Small, deterministic real-SDK/HTTP acceptance tests; no public model downloads."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

import pytest


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_oid(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


@dataclass(frozen=True)
class HubFile:
    path: str
    data: bytes
    lfs: bool = False

    def tree(self) -> dict:
        entry = {"type": "file", "path": self.path, "size": len(self.data), "oid": git_oid(self.data)}
        if self.lfs:
            entry["lfs"] = {"oid": sha256(self.data), "size": len(self.data), "pointerSize": 132}
        return entry

    def sibling(self) -> dict:
        entry = {"rfilename": self.path, "size": len(self.data), "blobId": git_oid(self.data)}
        if self.lfs:
            entry["lfs"] = {"sha256": sha256(self.data), "size": len(self.data), "pointerSize": 132}
        return entry


WEIGHT = HubFile("weights/model-Q4_K_M.gguf", b"GGUF-selected-weights\0" * 32768, True)
CONFIG = HubFile("config.json", b'{"model_type":"fixture","architectures":[]}\n')
UNSELECTED = HubFile("weights/model-Q8_0.gguf", b"GGUF-unselected-weights\0" * 16384, True)
COMMIT = "1" * 40
NEXT_COMMIT = "2" * 40


class FakeHub:
    """A wire fixture, with no SDK replacement and observable payload acquisition."""

    def __init__(self):
        self.repos = {("acme/tiny", COMMIT): [CONFIG, WEIGHT, UNSELECTED]}
        self.refs = {("acme/tiny", "main"): COMMIT}
        self.requests = []
        self.payload_gets = Counter()
        self.corrupt_once = set()
        self.truncate_once = set()
        self.delay = 0
        self.payload_started = threading.Event()
        self.lock = threading.Lock()
        hub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self.handle_request()

            def do_GET(self):
                self.handle_request()

            def reply(self, status, body=b"", headers=None):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, str(value))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def handle_request(self):
                path = unquote(urlsplit(self.path).path)
                with hub.lock:
                    hub.requests.append((self.command, self.path, dict(self.headers)))
                if path.startswith("/api/models/"):
                    tail = path.removeprefix("/api/models/").split("/")
                    repo = "/".join(tail[:2])
                    kind = tail[2] if len(tail) > 2 else "revision"
                    rev = tail[3] if len(tail) > 3 else "main"
                    if kind == "refs":
                        branches = [
                            {"name": name, "ref": "refs/heads/" + name, "targetCommit": target}
                            for (repo_id, name), target in hub.refs.items()
                            if repo_id == repo
                        ]
                        return self.reply(
                            200,
                            json.dumps({"branches": branches, "tags": [], "converts": []}).encode(),
                            {"Content-Type": "application/json"},
                        )
                    commit = hub.refs.get((repo, rev), rev)
                    files = hub.repos.get((repo, commit))
                    if files is None:
                        return self.reply(404, b'{"error":"unknown revision"}', {"X-Error-Code": "RevisionNotFound"})
                    if kind == "tree":
                        prefix = "/".join(tail[4:])
                        entries = [f.tree() for f in files if not prefix or f.path.startswith(prefix + "/")]
                        # Deliberately advertise an upstream Xet hash. Retained serving must strip it.
                        for entry in entries:
                            if "lfs" in entry:
                                entry["xetHash"] = "a" * 64
                        return self.reply(200, json.dumps(entries).encode(), {"Content-Type": "application/json"})
                    info = {
                        "id": repo,
                        "sha": commit,
                        "private": False,
                        "gated": False,
                        "siblings": [f.sibling() for f in files],
                    }
                    return self.reply(200, json.dumps(info).encode(), {"Content-Type": "application/json"})
                if "/resolve/" not in path:
                    return self.reply(404, b'{"error":"not found"}')
                repo, tail = path.lstrip("/").split("/resolve/", 1)
                revision, filename = tail.split("/", 1)
                commit = hub.refs.get((repo, revision), revision)
                file = next((f for f in hub.repos.get((repo, commit), []) if f.path == filename), None)
                if file is None:
                    return self.reply(404, b'{"error":"entry not found"}', {"X-Error-Code": "EntryNotFound"})
                data = file.data
                headers = {
                    "X-Repo-Commit": commit,
                    "ETag": '"' + (sha256(data) if file.lfs else git_oid(data)) + '"',
                    "Accept-Ranges": "bytes",
                }
                status = 200
                if self.command == "GET":
                    with hub.lock:
                        hub.payload_gets[(repo, filename)] += 1
                        if filename in hub.corrupt_once:
                            hub.corrupt_once.remove(filename)
                            data = b"X" * len(data)
                    hub.payload_started.set()
                    if hub.delay:
                        time.sleep(hub.delay)
                requested_range = self.headers.get("Range")
                if requested_range:
                    start, end = requested_range.removeprefix("bytes=").split("-", 1)
                    start = int(start)
                    end = int(end) if end else len(data) - 1
                    if start >= len(data):
                        return self.reply(416, headers={"Content-Range": f"bytes */{len(data)}"})
                    end = min(end, len(data) - 1)
                    headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                    data = data[start : end + 1]
                    status = 206
                if self.command == "GET" and filename in hub.truncate_once:
                    with hub.lock:
                        hub.truncate_once.remove(filename)
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(data)))
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(b"X" * (len(data) // 2))
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.reply(status, data, headers)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def clean_env(tmp_path: Path) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("HF_") and k not in {"HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"}
    }
    env.update(HF_HOME=str(tmp_path / "hf-home"), HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_PROGRESS_BARS="1")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return env


def run_python(code: str, *args, env: dict, timeout=60) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, "-c", code, *map(str, args)],
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"subprocess exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    return result


CLIENT_GUARD = """
import socket, sys
from urllib.parse import urlsplit
target = urlsplit(sys.argv[1])
def network_guard(event, args):
    if event == "socket.connect":
        address = args[1]
        allowed = isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}
        if not allowed or address[1] != target.port:
            raise RuntimeError("client attempted non-archive network access: " + repr(address))
sys.addaudithook(network_guard)
import huggingface_hub.file_download as fd
def forbid_xet(*args, **kwargs):
    raise AssertionError("retained endpoint signaled direct Xet transfer")
fd.xet_get = forbid_xet
"""


def ordinary_objects(root: Path, digest: str) -> list[Path]:
    return [p for p in root.rglob(digest) if p.is_file() and not p.is_symlink()]


def metadata_document(repo: str, commit: str, files: list[HubFile], refs=None) -> dict:
    return {
        "schema": "hf-archive.metadata",
        "schema_version": 1,
        "info": {
            "id": repo,
            "sha": commit,
            "private": False,
            "gated": False,
            "siblings": [f.sibling() for f in files],
        },
        "tree": [f.tree() for f in files],
        "refs": refs or [],
    }


def cli_args(root: Path, operation: str, *args) -> list[str]:
    return [operation, "--root", str(root), *map(str, args)]


def archive_cli(root: Path, operation: str, *args, env: dict, check=True):
    result = subprocess.run(
        [sys.executable, "-m", "hf_archive.cli", *cli_args(root, operation, *args)],
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    if check:
        assert result.returncode == 0, f"archive {operation} failed\n{result.stdout}\n{result.stderr}"
    return result


def pull(root: Path, hub: FakeHub, env: dict, *, repo="acme/tiny", revision="main", patterns=None):
    args = [repo, "--revision", revision, "--endpoint", hub.endpoint]
    for pattern in patterns or [CONFIG.path, WEIGHT.path]:
        args.extend(["--include", pattern])
    return archive_cli(root, "pull", *args, env=env)


def import_args(source: Path, metadata: Path, mapping=None) -> list[str]:
    info = json.loads(metadata.read_text())["info"]
    args = [info["id"], "--source", str(source), "--metadata", str(metadata), "--revision", info["sha"]]
    for repo_path, source_path in (mapping or {}).items():
        args.extend(["--map", f"{repo_path}={source_path}"])
    return args


OFFLINE_CLI_GUARD = """
import runpy, sys
def reject_network(event, args):
    if event == "socket.connect":
        raise AssertionError("offline import attempted network: " + repr(args))
sys.addaudithook(reject_network)
sys.argv = ["hf_archive", *sys.argv[1:]]
runpy.run_module("hf_archive.cli", run_name="__main__")
"""


def offline_import(root: Path, source: Path, metadata: Path, env: dict, mapping=None):
    return run_python(OFFLINE_CLI_GUARD, *cli_args(root, "import", *import_args(source, metadata, mapping)), env=env)


def tree_fingerprint(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.is_symlink(), sha256(p.read_bytes())) for p in root.rglob("*") if p.is_file()}


@contextmanager
def served(root: Path, tmp_path: Path, verification_delay=0):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    guard_file = tmp_path / "outbound-attempts.txt"
    env = clean_env(tmp_path)
    code = """
import os, pathlib, runpy, sys
marker = pathlib.Path(sys.argv[1])
delay = float(sys.argv[2])
def audit(event, args):
    if event == "socket.connect":
        marker.write_text(repr(args))
        raise RuntimeError("read-only archive attempted outbound network")
sys.addaudithook(audit)
assert not any(os.getenv(k) for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"))
if delay:
    import time
    import hf_archive.server as server
    hash_stream = server.hash_stream
    def slow_verified_read(file):
        if os.fstat(file.fileno()).st_size > 1024:
            time.sleep(delay)
        return hash_stream(file)
    server.hash_stream = slow_verified_read
sys.argv = ["hf_archive", *sys.argv[3:]]
runpy.run_module("hf_archive.cli", run_name="__main__")
"""
    log = tmp_path / "server.log"
    with log.open("w+") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(guard_file),
                str(verification_delay),
                *cli_args(root, "serve", "--host", "127.0.0.1", "--port", str(port)),
            ],
            env=env,
            stdout=output,
            stderr=output,
        )
        endpoint = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output.seek(0)
                    pytest.fail("archive server exited: " + output.read())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("archive server did not become ready")
            yield endpoint
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            assert not guard_file.exists(), "serving contacted network: " + guard_file.read_text()


SDK_ACCEPTANCE = (
    CLIENT_GUARD
    + """
from pathlib import Path
import hashlib, json
from huggingface_hub import HfApi, hf_hub_download, snapshot_download
from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError
endpoint, cache, revision, commit, expected_weight, expected_config = sys.argv[1:]
api = HfApi(endpoint=endpoint, token=False)
info = api.model_info("acme/tiny", revision=revision, files_metadata=True)
assert info.sha == commit
tree = list(api.list_repo_tree("acme/tiny", revision=revision, recursive=True))
assert {f.path for f in tree} == {"config.json", "weights/model-Q4_K_M.gguf", "weights/model-Q8_0.gguf"}
assert all(f.xet_hash is None for f in tree), "tree retains direct-network Xet metadata"
p = hf_hub_download("acme/tiny", "weights/model-Q4_K_M.gguf", revision=revision,
                    token=False, cache_dir=cache + "-single")
assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == expected_weight
snapshot = Path(snapshot_download("acme/tiny", revision=revision, endpoint=endpoint,
                    token=False, cache_dir=cache + "-snapshot",
                    allow_patterns=["config.json", "weights/model-Q4_K_M.gguf"]))
assert hashlib.sha256((snapshot / "weights/model-Q4_K_M.gguf").read_bytes()).hexdigest() == expected_weight
assert hashlib.sha256((snapshot / "config.json").read_bytes()).hexdigest() == expected_config
assert not (snapshot / "weights/model-Q8_0.gguf").exists()
try:
    snapshot_download("acme/tiny", revision=revision, endpoint=endpoint, token=False,
                      cache_dir=cache + "-absent", allow_patterns=["weights/model-Q8_0.gguf"])
except (HfHubHTTPError, LocalEntryNotFoundError) as error:
    response = getattr(error, "response", None)
    if response is not None:
        assert response.status_code in {404, 409, 503}, repr(error)
else:
    raise AssertionError("unretained selected file falsely downloaded")
print(json.dumps({"revision": revision, "commit": info.sha, "selected_snapshot": "verified"}))
"""
)


def test_real_sdk_fresh_caches_after_read_only_restart(tmp_path):
    root = tmp_path / "archive"
    env = clean_env(tmp_path)
    with FakeHub() as hub:
        pull(root, hub, env)
        assert hub.payload_gets[("acme/tiny", WEIGHT.path)] == 1
        assert hub.payload_gets[("acme/tiny", UNSELECTED.path)] == 0
    # The upstream is shut down before serving starts. Neither server nor SDK may contact it.
    before = tree_fingerprint(root)
    for number, revision in enumerate(["main", COMMIT]):
        case = tmp_path / f"restart-{number}"
        case.mkdir()
        with served(root, case) as endpoint:
            client_env = clean_env(case)
            client_env["HF_ENDPOINT"] = endpoint
            run_python(
                SDK_ACCEPTANCE,
                endpoint,
                case / "fresh-cache",
                revision,
                COMMIT,
                sha256(WEIGHT.data),
                sha256(CONFIG.data),
                env=client_env,
            )
        assert tree_fingerprint(root) == before, "read-only serving changed retained archive files"
    retained = ordinary_objects(root, sha256(WEIGHT.data))
    assert len(retained) == 1
    assert retained[0].read_bytes() == WEIGHT.data


def test_retained_endpoint_ranges_are_verified_and_standard(tmp_path):
    root = tmp_path / "archive"
    with FakeHub() as hub:
        pull(root, hub, clean_env(tmp_path))
    with served(root, tmp_path) as endpoint:
        url = endpoint + f"/acme/tiny/resolve/{COMMIT}/{WEIGHT.path}"
        with urlopen(Request(url, method="HEAD")) as response:
            assert response.status == 200
            assert int(response.headers["Content-Length"]) == len(WEIGHT.data)
            assert response.headers["X-Repo-Commit"] == COMMIT
            assert response.headers["ETag"].strip('"') == sha256(WEIGHT.data)
            assert not any("xet" in name.lower() for name in response.headers)
        for spec, expected in [
            ("bytes=0-99", WEIGHT.data[:100]),
            ("bytes=100-", WEIGHT.data[100:]),
            ("bytes=-99", WEIGHT.data[-99:]),
        ]:
            with urlopen(Request(url, headers={"Range": spec})) as response:
                assert response.status == 206
                assert response.read() == expected
                assert int(response.headers["Content-Length"]) == len(expected)
                assert response.headers["Content-Range"].endswith(f"/{len(WEIGHT.data)}")
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url, headers={"Range": f"bytes={len(WEIGHT.data)}-"}))
        assert error.value.code == 416


def test_interrupted_corrupt_upstream_cannot_publish_or_poison_retry(tmp_path):
    root = tmp_path / "archive"
    env = clean_env(tmp_path)
    # The SDK buffers 10 MiB chunks; use enough bytes that the bad half contains a full client chunk.
    resume_weight = HubFile(WEIGHT.path, b"good-resume-1234!" * (26 * 1024 * 1024 // 16), True)
    with FakeHub() as hub:
        hub.repos[("acme/tiny", COMMIT)] = [CONFIG, resume_weight, UNSELECTED]
        # A bad prefix followed by a disconnected body exercises the real SDK's resume path.
        # The next range response contains genuine suffix bytes, making total size alone insufficient.
        hub.truncate_once.add(WEIGHT.path)
        first = archive_cli(
            root,
            "pull",
            "acme/tiny",
            "--revision",
            "main",
            "--endpoint",
            hub.endpoint,
            "--include",
            WEIGHT.path,
            env=env,
            check=False,
        )
        assert first.returncode != 0, "a resumed corrupt upstream file was accepted"
        assert not ordinary_objects(root, sha256(resume_weight.data)), "unverified object was published"
        assert not list(root.glob("blobs/sha256/*/*")), "corrupt acquired bytes leaked into the retained store"
        assert not list(root.glob("repos/*/revisions/*.json")), "failed acquisition published a revision"
        # Retry must discard any poisoned SDK partial or completed cache entry.
        pull(root, hub, env, patterns=[WEIGHT.path])
        assert hub.payload_gets[("acme/tiny", WEIGHT.path)] >= 2
        assert any(
            method == "GET" and headers.get("Range", "").startswith("bytes=") for method, _, headers in hub.requests
        ), "real SDK did not resume the interrupted body"
        assert ordinary_objects(root, sha256(resume_weight.data))[0].read_bytes() == resume_weight.data
    with served(root, tmp_path) as endpoint:
        code = (
            CLIENT_GUARD
            + """
from pathlib import Path
import hashlib
from huggingface_hub import hf_hub_download
p=hf_hub_download("acme/tiny", "weights/model-Q4_K_M.gguf", revision="main",
                  endpoint=sys.argv[1], cache_dir=sys.argv[2], token=False)
assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == sys.argv[3]
"""
        )
        run_python(code, endpoint, tmp_path / "fresh-sdk-cache", sha256(resume_weight.data), env=env)


def test_unretained_file_does_not_permanently_poison_sdk_cache(tmp_path):
    root = tmp_path / "archive"
    env = clean_env(tmp_path)
    cache = tmp_path / "same-sdk-cache"
    with FakeHub() as hub:
        pull(root, hub, env)
        with served(root, tmp_path) as endpoint:
            code = (
                CLIENT_GUARD
                + """
from huggingface_hub import hf_hub_download, try_to_load_from_cache
from huggingface_hub.file_download import _CACHED_NO_EXIST
from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError
try:
    hf_hub_download("acme/tiny", "weights/model-Q8_0.gguf", revision=sys.argv[3],
                    endpoint=sys.argv[1], cache_dir=sys.argv[2], token=False)
except (HfHubHTTPError, LocalEntryNotFoundError):
    pass
else:
    raise AssertionError("known unretained file falsely succeeded")
assert try_to_load_from_cache("acme/tiny", "weights/model-Q8_0.gguf", revision=sys.argv[3],
                              cache_dir=sys.argv[2]) is not _CACHED_NO_EXIST
"""
            )
            run_python(code, endpoint, cache, COMMIT, env=env)
        pull(root, hub, env, patterns=[UNSELECTED.path])
    with served(root, tmp_path) as endpoint:
        code = (
            CLIENT_GUARD
            + """
from huggingface_hub import hf_hub_download
from pathlib import Path
import hashlib
p = hf_hub_download("acme/tiny", "weights/model-Q8_0.gguf", revision=sys.argv[3],
                    endpoint=sys.argv[1], cache_dir=sys.argv[2], token=False)
assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == sys.argv[4]
"""
        )
        run_python(code, endpoint, cache, COMMIT, sha256(UNSELECTED.data), env=env)


def test_concurrent_cross_repo_renamed_pulls_acquire_one_global_object(tmp_path):
    root = tmp_path / "archive"
    env = clean_env(tmp_path)
    renamed = HubFile("renamed.gguf", WEIGHT.data, True)
    with FakeHub() as hub:
        hub.repos[("acme/clone", NEXT_COMMIT)] = [CONFIG, renamed]
        hub.refs[("acme/clone", "main")] = NEXT_COMMIT
        pull(root, hub, env, patterns=[CONFIG.path])
        hub.delay = 0.5
        jobs = []
        try:
            for repo, path in [("acme/tiny", WEIGHT.path), ("acme/clone", renamed.path)]:
                args = cli_args(root, "pull", repo, "--revision", "main", "--endpoint", hub.endpoint, "--include", path)
                jobs.append(
                    subprocess.Popen(
                        [sys.executable, "-m", "hf_archive.cli", *args],
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                )
            for process in jobs:
                stdout, stderr = process.communicate(timeout=60)
                assert process.returncode == 0, f"concurrent pull failed: {stdout}\n{stderr}"
        finally:
            for process in jobs:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        total_payload_gets = (
            hub.payload_gets[("acme/tiny", WEIGHT.path)] + hub.payload_gets[("acme/clone", renamed.path)]
        )
        assert total_payload_gets == 1, f"identical global content acquired {total_payload_gets} times"
        objects = ordinary_objects(root, sha256(WEIGHT.data))
        assert len(objects) == 1
        assert objects[0].read_bytes() == WEIGHT.data
        # A further revision and repo name must also reuse the object without another payload GET.
        hub.repos[("acme/tiny", NEXT_COMMIT)] = [CONFIG, WEIGHT, UNSELECTED]
        pull(root, hub, env, revision=NEXT_COMMIT, patterns=[WEIGHT.path])
        assert hub.payload_gets[("acme/tiny", WEIGHT.path)] + hub.payload_gets[("acme/clone", renamed.path)] == 1
    with served(root, tmp_path) as endpoint:
        for repo, revision, path in [
            ("acme/tiny", COMMIT, WEIGHT.path),
            ("acme/tiny", NEXT_COMMIT, WEIGHT.path),
            ("acme/clone", "main", renamed.path),
        ]:
            with urlopen(endpoint + f"/{repo}/resolve/{revision}/{path}") as response:
                assert response.read() == WEIGHT.data


def test_offline_native_snapshot_and_mapped_plain_imports_reuse_global_objects(tmp_path):
    root = tmp_path / "archive"
    env = clean_env(tmp_path)
    cache = tmp_path / "native-hf-cache"
    snapshot = cache / "snapshots" / COMMIT
    for file in [CONFIG, WEIGHT]:
        blob = cache / "blobs" / (sha256(file.data) if file.lfs else git_oid(file.data))
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(file.data)
        link = snapshot / file.path
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(blob, link.parent))
    first_meta = tmp_path / "native-metadata.json"
    first_meta.write_text(
        json.dumps(metadata_document("acme/tiny", COMMIT, [CONFIG, WEIGHT, UNSELECTED], ["refs/heads/main"]))
    )
    before = tree_fingerprint(cache)
    offline_import(root, cache, first_meta, env)
    assert tree_fingerprint(cache) == before
    assert (snapshot / WEIGHT.path).is_symlink()
    # A different repository/revision/path and a renamed plain source share the verified global object.
    renamed = HubFile("renamed.gguf", WEIGHT.data, True)
    plain = tmp_path / "plain-model"
    plain.mkdir()
    (plain / "downloaded.gguf").write_bytes(WEIGHT.data)
    clone_meta = tmp_path / "plain-metadata.json"
    clone_meta.write_text(json.dumps(metadata_document("acme/clone", NEXT_COMMIT, [renamed], ["refs/heads/main"])))
    offline_import(root, plain, clone_meta, env, mapping={renamed.path: "downloaded.gguf"})
    objects = ordinary_objects(root, sha256(WEIGHT.data))
    assert len(objects) == 1
    assert objects[0].read_bytes() == WEIGHT.data
    assert os.stat(objects[0]).st_ino != os.stat(cache / "blobs" / sha256(WEIGHT.data)).st_ino
    manifest = json.loads((root / "repos" / "models--acme--tiny" / "revisions" / f"{COMMIT}.json").read_text())
    assert manifest["files"][UNSELECTED.path]["blob"] is None
    assert manifest["files"][CONFIG.path]["blob"] == sha256(CONFIG.data)
    assert manifest["files"][WEIGHT.path]["blob"] == sha256(WEIGHT.data)
    # Original sources can change after import without changing retained bytes.
    (cache / "blobs" / sha256(WEIGHT.data)).write_bytes(b"X" * len(WEIGHT.data))
    (plain / "downloaded.gguf").write_bytes(b"Y" * len(WEIGHT.data))
    assert objects[0].read_bytes() == WEIGHT.data
    with served(root, tmp_path) as endpoint:
        for repo, path in [("acme/tiny", WEIGHT.path), ("acme/clone", renamed.path)]:
            with urlopen(endpoint + f"/{repo}/resolve/main/{path}") as response:
                assert response.read() == WEIGHT.data


def test_import_waits_for_concurrent_pull_of_same_content(tmp_path):
    root = tmp_path / "archive"
    env = clean_env(tmp_path)
    renamed = HubFile("renamed.gguf", WEIGHT.data, True)
    source = tmp_path / "import-source"
    source.mkdir()
    (source / renamed.path).write_bytes(WEIGHT.data)
    metadata = tmp_path / "import-metadata.json"
    metadata.write_text(json.dumps(metadata_document("acme/clone", NEXT_COMMIT, [renamed], ["refs/heads/main"])))
    with FakeHub() as hub:
        pull(root, hub, env, patterns=[CONFIG.path])
        hub.delay = 0.5
        hub.payload_started.clear()
        args = cli_args(root, "pull", "acme/tiny", "--endpoint", hub.endpoint, "--include", WEIGHT.path)
        job = subprocess.Popen(
            [sys.executable, "-m", "hf_archive.cli", *args],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert hub.payload_started.wait(timeout=10), "concurrent pull did not begin its payload transfer"
            imported = offline_import(root, source, metadata, env)
            assert "via reuse" in imported.stdout, "import did not reuse the concurrently acquired content"
            stdout, stderr = job.communicate(timeout=60)
            assert job.returncode == 0, f"concurrent pull failed: {stdout}\n{stderr}"
        finally:
            if job.poll() is None:
                job.kill()
                job.wait(timeout=5)
        assert hub.payload_gets[("acme/tiny", WEIGHT.path)] == 1
    objects = ordinary_objects(root, sha256(WEIGHT.data))
    assert len(objects) == 1
    assert objects[0].read_bytes() == WEIGHT.data
    assert (source / renamed.path).read_bytes() == WEIGHT.data
    with (
        served(root, tmp_path) as endpoint,
        urlopen(endpoint + f"/acme/clone/resolve/main/{renamed.path}") as response,
    ):
        assert response.read() == WEIGHT.data


def test_default_sdk_timeout_gets_success_or_honest_retryable_warming(tmp_path):
    root = tmp_path / "archive"
    env = clean_env(tmp_path)
    with FakeHub() as hub:
        pull(root, hub, env, patterns=[WEIGHT.path])
    with served(root, tmp_path, verification_delay=12) as endpoint:
        code = (
            CLIENT_GUARD
            + """
import hashlib, json, time
from pathlib import Path
from huggingface_hub import hf_hub_download, constants, try_to_load_from_cache
from huggingface_hub.file_download import _CACHED_NO_EXIST
assert constants.HF_HUB_ETAG_TIMEOUT == 10
assert constants.HF_HUB_DOWNLOAD_TIMEOUT == 10
def download():
    return hf_hub_download("acme/tiny", "weights/model-Q4_K_M.gguf", revision="main",
                           endpoint=sys.argv[1], cache_dir=sys.argv[2], token=False)
try:
    path = download()
    print(json.dumps({"cold_first_call": "success", "sdk_etag_timeout": constants.HF_HUB_ETAG_TIMEOUT}))
except Exception as error:
    cursor = error
    response = None
    while cursor is not None:
        response = getattr(cursor, "response", None)
        if response is not None:
            break
        cursor = cursor.__cause__
    assert response is not None and response.status_code in {429, 503}, (
        "cold verified serving outlasted default SDK timeout without a retryable warming response: " + repr(error))
    print(json.dumps({"warming_status": response.status_code, "sdk_etag_timeout": constants.HF_HUB_ETAG_TIMEOUT}))
    assert try_to_load_from_cache("acme/tiny", "weights/model-Q4_K_M.gguf", revision=sys.argv[4],
                                  cache_dir=sys.argv[2]) is not _CACHED_NO_EXIST
    deadline = time.monotonic() + 20
    while True:
        time.sleep(0.25)
        try:
            path = download()
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == sys.argv[3]
"""
        )
        result = run_python(code, endpoint, tmp_path / "default-timeout-cache", sha256(WEIGHT.data), COMMIT, env=env)
        assert '"cold_first_call": "success"' in result.stdout or '"warming_status":' in result.stdout
