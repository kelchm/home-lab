#!/usr/bin/env python3
"""Copy existing model stores into SparkNest; never move or modify a source.

The coordinator runs on the Mac using its existing SSH access. This same
stdlib-only file runs on Athena for receiving, cloning, and verification.
Runtime inventories and journals live outside the repository.
"""

import argparse
import base64
import collections
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys
import time

ATHENA = os.environ.get("SPARKNEST_ATHENA_HOST", "kelchm@10.32.10.5")
ROOT = Path("/volume1/models/sparknest")
AGENT = "/volume1/docker/sparknest/ingest.py"
CONTAINER = "sparknest-sparknest-1"
DOCKER = "/usr/local/bin/docker"
SKIP = {"@eaDir", ".DS_Store", ".locks", "token", "stored_tokens"}
CHUNK = 4 << 20
DIRECT_CONFIG = Path("/volume1/docker/sparknest-transfer/sources.json")
TRANSPORT_EXIT = 75  # An agent's own SSH hop failed; the coordinator pauses as for its own SSH.


def encoded(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def decoded(value):
    return json.loads(base64.urlsafe_b64decode(value))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def walk(root, warnings=None, exclude_dirs=()):
    def onerror(exc):
        if warnings is None:
            raise exc
        warnings.append(str(exc))
    for folder, dirs, files in os.walk(str(root), onerror=onerror):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and d not in exclude_dirs)
        for name in dirs:
            path = Path(folder) / name
            if path.is_symlink() and warnings is not None:
                warnings.append("linked directory not followed: " + str(path))
        for name in sorted(files):
            if name not in SKIP:
                path = Path(folder) / name
                if name.endswith(".lock") and path.is_file() and path.stat().st_size == 0:
                    continue
                yield path


def identity(path):
    st = path.stat()
    return [st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev]


def file_record(path, relative, etag=None, sha256=None):
    if sha256 is None and re.fullmatch(r"sha256-[0-9a-f]{64}", path.name):
        sha256 = path.name[7:]
    return {"src": str(path.resolve()), "rel": str(relative),
            "identity": identity(path), "etag": etag, "sha256": sha256}


def children(root, warnings):
    try:
        return sorted(root.iterdir())
    except OSError as exc:
        warnings.append(str(exc))
        return []


def records(root, warnings, exclude_dirs=()):
    result = []
    for path in walk(root, warnings, exclude_dirs):
        try:
            if path.is_file():
                result.append(file_record(path, path.relative_to(root)))
            else:
                warnings.append("unreadable or broken file: " + str(path))
        except OSError as exc:
            warnings.append(str(exc))
    return result


def inventory(spec):
    items, warnings = [], []
    for store in spec["stores"]:
        root = Path(store["root"])
        if not root.is_dir():
            warnings.append("missing or inaccessible source root: " + str(root))
            continue
        kind = store["kind"]
        if kind == "hf":
            referenced = set()
            repos = [p for p in children(root, warnings) if p.name.startswith("models--") and p.is_dir()]
            for repo in repos:
                if repo.is_symlink():
                    warnings.append("linked repository directory not followed: " + str(repo))
                    continue
                if not (repo / "snapshots").is_dir():
                    continue
                files, refs, trees = [], {}, []
                for rev in children(repo / "snapshots", warnings):
                    if rev.is_symlink():
                        warnings.append("linked revision directory not followed: " + str(rev))
                        continue
                    if not rev.is_dir():
                        continue
                    for path in walk(rev, warnings):
                        if not path.is_file():
                            warnings.append("missing snapshot target: " + str(path))
                            continue
                        relative = str(path.relative_to(rev))
                        etag = Path(os.readlink(path)).name if path.is_symlink() else None
                        if etag and len(etag) not in (40, 64):
                            etag = None
                        rec = file_record(path, relative, etag,
                                          etag if etag and len(etag) == 64 else None)
                        rec["revision"] = rev.name
                        files.append(rec)
                        referenced.add(str(path.resolve()))
                    tree = repo / "trees" / (rev.name + ".json")
                    if tree.is_file():
                        trees.append(file_record(tree, tree.name))
                refs_complete, before = True, len(warnings)
                for path in walk(repo / "refs", warnings) if (repo / "refs").exists() else []:
                    try:
                        if path.is_file():
                            refs[str(path.relative_to(repo / "refs"))] = path.read_text().strip()
                    except OSError as exc:
                        warnings.append("unreadable ref: " + str(exc))
                if len(warnings) > before:
                    refs_complete = False  # Any ref walk problem means agreement is unknown.
                if files:
                    items.append({"kind": "hf", "name": repo.name, "files": files, "refs": refs,
                                  "refs_complete": refs_complete, "trees": trees, "store": store["name"]})
            # Retain bytes not assigned to a snapshot, without inventing a revision.
            # The shared store is walked first, so a link into it is already covered;
            # a link to an outside file is retained once by its target.
            orphan_files, seen = [], set(referenced)
            for blobroot in [root / "blobs"] + [p / "blobs" for p in repos if not p.is_symlink()]:
                if not blobroot.exists():
                    continue
                for path in walk(blobroot, warnings):
                    if path.is_file() and str(path.resolve()) not in seen:
                        seen.add(str(path.resolve()))
                        orphan_files.append(file_record(path, path.relative_to(root)))
            if orphan_files:
                items.append({"kind": "tree", "name": "unreferenced-hf-blobs",
                              "files": orphan_files, "store": store["name"]})
        elif kind == "modelkeep":
            for manifest in walk(root, warnings):
                if manifest.name != ".modelkeep-manifest.json":
                    continue
                try:
                    data = json.loads(manifest.read_text())
                    name = "models--" + data["repo_id"].replace("/", "--")
                    revision = data["commit"]
                    files = []
                    for entry in data["files"]:
                        path = manifest.parent / entry["path"]
                        if not path.is_file():
                            warnings.append("missing ModelKeep file: " + str(path))
                            continue
                        rec = file_record(path, entry["path"], sha256=entry.get("sha256"))
                        rec["revision"] = revision
                        files.append(rec)
                    if files:
                        items.append({"kind": "hf", "name": name, "files": files,
                                      "refs": {"main": revision}, "trees": [], "store": store["name"]})
                except (OSError, ValueError, KeyError) as exc:
                    warnings.append(str(manifest) + ": " + str(exc))
        else:
            # Each local model folder keeps its actual metadata and filenames.
            roots = [root]
            if kind == "models":
                roots = []
                loose = []
                for owner in children(root, warnings):
                    if owner.name in SKIP:
                        continue
                    if owner.is_symlink() and owner.is_dir():
                        warnings.append("linked model owner directory not followed: " + str(owner))
                        continue
                    if owner.is_file():
                        loose.append(file_record(owner, owner.relative_to(root)))
                    elif owner.is_dir():
                        for model in children(owner, warnings):
                            if model.is_symlink() and model.is_dir():
                                warnings.append("linked model directory not followed: " + str(model))
                            elif model.is_dir():
                                roots.append(model)
                            elif model.is_file():
                                loose.append(file_record(model, model.relative_to(root)))
                            else:
                                warnings.append("broken model link: " + str(model))
                    else:
                        warnings.append("broken model link: " + str(owner))
                if loose:
                    items.append({"kind": "tree", "name": "unassigned-root-files", "files": loose, "store": store["name"]})
            for model in roots:
                if not model.is_dir() or model.name in SKIP:
                    continue
                files = records(model, warnings, store.get("exclude_dirs", []))
                if files:
                    name = str(model.relative_to(root)) if kind == "models" else store["name"]
                    items.append({"kind": "tree", "name": name, "files": files, "store": store["name"]})
    for item in items:
        item["host"] = spec["host"]
        item["sudo"] = spec.get("sudo", spec["name"] == "athena")
        item["source"] = spec["name"]
        key_input = {"source": spec["name"], "store": item["store"], "name": item["name"],
                     "files": item["files"], "refs": item.get("refs", {})}
        if item.get("trees"):
            key_input["trees"] = item["trees"]
        item["key"] = hashlib.sha256(json.dumps(key_input, sort_keys=True).encode()).hexdigest()[:20]
    return {"items": items, "warnings": warnings}


def remote_command(host, argv, sudo=False):
    command = ("sudo -n " if sudo else "") + shlex.join([str(a) for a in argv])
    control = os.environ.get("SPARKNEST_SSH_CONTROL_DIR")
    connection = ["-S", str(Path(control) / host.split("@")[-1])] if control else []
    if host == "kelchm@10.32.10.5":
        # The management address already pins this same NAS's host key.
        connection += ["-o", "HostKeyAlias=10.32.20.5"]
    return ["ssh"] + connection + ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", host, command]


def agent(mode, request, stdin=None):
    argument = "-" if mode in ("prepare", "filter-stage", "probe-copies") else encoded(request)
    if argument == "-":
        stdin = json.dumps(request).encode()
    cmd = remote_command(ATHENA, ["python3", AGENT, mode, argument], sudo=True)
    options = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, **options)
    return json.loads(result.stdout)


def digest_file(path, with_git=False):
    sha = hashlib.sha256()
    git = hashlib.sha1(("blob " + str(path.stat().st_size) + "\0").encode()) if with_git or path.stat().st_size <= 1 << 20 else None
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            sha.update(chunk)
            if git is not None:
                git.update(chunk)
    return sha.hexdigest(), git.hexdigest() if git is not None else None


def reusable_copy(rec, copies):
    for value in copies:
        if value["bytes"] != rec["identity"][0]:
            continue
        if rec.get("sha256") and value["sha256"] != rec["sha256"]:
            continue
        if rec.get("etag") and len(rec["etag"]) == 40 and value.get("git_sha1") != rec["etag"]:
            continue
        return value
    return None


def receive(request, stream=None):
    """Receive one file, or reflink an existing Athena copy; verify before publish."""
    rec = request["file"]
    folder = ROOT / "incoming/bulk" / request["key"] / "payloads"
    folder.mkdir(parents=True, exist_ok=True)
    fid = hashlib.sha256((rec["src"] + str(rec["identity"])).encode()).hexdigest()
    final, receipt = folder / fid, folder / (fid + ".json")
    if final.is_file() and receipt.is_file():
        result = json.loads(receipt.read_text())
        if identity(final) == result["stored_identity"]:
            if rec.get("etag") and len(rec["etag"]) == 40 and not result.get("git_sha1"):
                sha, git = digest_file(final, with_git=True)
                if identity(final) != result["stored_identity"] or sha != result["sha256"]:
                    raise ValueError("cached receipt payload changed")
                result["git_sha1"] = git
                atomic_json(receipt, result)
            if reusable_copy(rec, [result]) is None:
                raise ValueError("cached receipt differs from the published file identity")
            return result
    if request.get("probe"):
        return None
    partial = folder / (fid + ".partial")
    candidate = request.get("candidate")
    method = "ssh"
    if candidate:
        source = Path(candidate)
        before = identity(source)
        if candidate == rec["src"] and before != rec["identity"]:
            raise ValueError("Athena source changed since inventory")
        if before[0] != rec["identity"][0]:
            raise ValueError("candidate size differs")
        with source.open("rb") as source_file, partial.open("wb") as target:
            try:
                fcntl.ioctl(target.fileno(), 0x40049409, source_file.fileno())
                method = "reflink"
            except OSError:
                shutil.copyfileobj(source_file, target, CHUNK)
                method = "local-copy"
            target.flush()
            os.fsync(target.fileno())
        sha, git = digest_file(partial, with_git=bool(rec.get("etag") and len(rec["etag"]) == 40))
        if identity(source) != before:
            raise ValueError("Athena source changed while copying")
    else:
        stream = stream if stream is not None else sys.stdin.buffer
        remaining = rec["identity"][0]
        sha_obj = hashlib.sha256()
        git_obj = hashlib.sha1(("blob " + str(remaining) + "\0").encode()) if remaining <= 1 << 20 or (rec.get("etag") and len(rec["etag"]) == 40) else None
        with partial.open("wb") as target:
            while remaining:
                chunk = stream.read(min(CHUNK, remaining))
                if not chunk:
                    raise ValueError("transfer ended early")
                target.write(chunk)
                sha_obj.update(chunk)
                if git_obj is not None:
                    git_obj.update(chunk)
                remaining -= len(chunk)
            target.flush()
            os.fsync(target.fileno())
        trailer = stream.read(64).decode("ascii")
        sha, git = sha_obj.hexdigest(), git_obj.hexdigest() if git_obj is not None else None
        if sha != trailer:
            raise ValueError("source and destination SHA-256 differ")
    if rec.get("sha256") and rec["sha256"] != sha:
        raise ValueError("content differs from the source's recorded SHA-256")
    if rec.get("etag") and len(rec["etag"]) == 40 and rec["etag"] != git:
        raise ValueError("content differs from the source's Git blob identity")
    os.replace(partial, final)
    final.chmod(0o444)
    result = {"path": str(final), "sha256": sha, "git_sha1": git,
              "bytes": final.stat().st_size, "method": method, "stored_identity": identity(final)}
    atomic_json(receipt, result)
    return result


def send(request):
    rec = request["file"]
    path = Path(rec["src"])
    if identity(path) != rec["identity"]:
        raise ValueError("source changed since inventory: " + str(path))
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            sha.update(chunk)
            sys.stdout.buffer.write(chunk)
    if identity(path) != rec["identity"]:
        raise ValueError("source changed while reading: " + str(path))
    sys.stdout.buffer.write(sha.hexdigest().encode())
    sys.stdout.buffer.flush()


def send_authorized(allowlist_path):
    """Forced SSH command: stream only an inventoried, unchanged source file."""
    request = decoded(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    rec = request["file"]
    allowed = json.loads(Path(allowlist_path).read_text())
    if rec["identity"] not in allowed.get(rec["src"], []):
        raise ValueError("source is outside this transfer's inventory")
    send(request)


def receive_direct(request):
    """Pull Spark payloads to Athena over the storage VLAN, bypassing the Mac."""
    cached = receive(dict(request, probe=True))
    if cached is not None:
        return cached
    profile = json.loads(DIRECT_CONFIG.read_text())[request["source_host"]]
    command = ["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
               "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes",
               "-o", "HostKeyAlgorithms=ssh-ed25519",
               "-o", "UserKnownHostsFile=" + profile["known_hosts"],
               "-i", profile["identity"], profile["host"], encoded({"file": request["file"]})]
    source = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)

    def stop(abandon):
        # Never buffer an unread payload: close the pipe and end the source.
        unread = abandon or source.stdout.read(1)
        source.stdout.close()
        if abandon:
            try:
                source.wait(timeout=5)  # Let a failed SSH report 255 before any terminate.
            except subprocess.TimeoutExpired:
                pass
        if unread and source.poll() is None:
            source.terminate()
        source.wait()
        error = source.stderr.read().decode(errors="replace")[-2000:]
        source.stderr.close()
        if source.returncode == 255:
            raise ConnectionError("direct source SSH failed: " + error)
        return bool(unread), error

    try:
        result = receive(request, stream=source.stdout)
    except Exception as exc:
        _, error = stop(True)
        raise RuntimeError("direct receive failed: " + str(exc) + "\n" + error) from exc
    unread, error = stop(False)
    if not unread and source.returncode:
        raise RuntimeError("direct source failed: " + error)
    return dict(result, transport="direct-storage-vlan", source_host=profile["host"])


def safe_relative(text):
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe relative path: " + text)
    return path


def prepare(request):
    item, copies = request["item"], request["copies"]
    base = ROOT / "incoming/bulk" / safe_relative(item["key"])
    staged = base / ("hub" if item["kind"] == "hf" else "tree")
    # Rebuild only disposable staging links on retry, never payload receipts or originals.
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True, exist_ok=True)
    expected = []
    for rec, copy in zip(item["files"], copies):
        rel = safe_relative(rec["rel"])
        if item["kind"] == "hf":
            repo = staged / safe_relative(item["name"])
            etag = rec.get("etag") or copy["sha256"]
            dest = repo / "blobs" / etag
            snap = repo / "snapshots" / safe_relative(rec["revision"]) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                os.link(copy["path"], dest)
            snap.parent.mkdir(parents=True, exist_ok=True)
            if not snap.exists() and not snap.is_symlink():
                snap.symlink_to(os.path.relpath(dest, snap.parent))
            namespace = "/hf-home/hub/" + item["name"] + "/snapshots/" + rec["revision"] + "/" + str(rel)
        else:
            dest = staged / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                os.link(copy["path"], dest)
            namespace = "/imports/" + item["source"] + "/" + item["store"] + "/" + item["name"] + "/" + str(rel)
        expected.append({"path": namespace, "sha256": copy["sha256"], "bytes": copy["bytes"]})
    if item["kind"] == "hf":
        repo = staged / item["name"]
        for rec, copy in zip(item.get("trees", []), copies[len(item["files"]):]):
            path = repo / "trees" / safe_relative(rec["rel"])
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                os.link(copy["path"], path)
            expected.append({"path": "/hf-home/hub/" + item["name"] + "/trees/" + rec["rel"],
                             "sha256": copy["sha256"], "bytes": copy["bytes"]})
        revisions = {rec["revision"] for rec in item["files"]}
        for ref, target in item.get("refs", {}).items():
            if target not in revisions:
                continue
            names = [source_ref(item, ref)]
            if ref in item.get("shared_refs", {}):
                names.append(ref)
            for name in names:
                path = repo / "refs" / safe_relative(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(target)
                expected.append({"path": "/hf-home/hub/" + item["name"] + "/refs/" + name,
                                 "sha256": hashlib.sha256(target.encode()).hexdigest(), "bytes": len(target.encode())})
    # Expected hashes are outside the daemon's writable /data mount.
    manifest = Path(AGENT).parent / "ingest-manifests" / (item["key"] + ".json")
    atomic_json(manifest, expected)
    manifest.chmod(0o444)
    atomic_json(base / "source.json", item)
    # The daemon has no DAC override capability; ordinary writes to these
    # staged inputs fail even though they share its writable /data bind.
    for folder, dirs, files in os.walk(staged):
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink():
                path.chmod(0o444)
        Path(folder).chmod(0o555)
    return {"staged": str(staged), "base": str(base), "files": len(expected),
            "manifest": "/config/ingest-manifests/" + manifest.name}


def probe_copies(request):
    valid = []
    for copy in request["copies"]:
        path = Path(copy["path"])
        if ROOT / "incoming/bulk" not in path.parents:
            raise ValueError("receipt outside bulk staging")
        try:
            if path.is_file() and identity(path) == copy["stored_identity"]:
                valid.append(str(path))
        except OSError:
            pass
    return {"valid": valid}


def verify(request):
    root = Path("/mnt/sparknest")
    expected = json.loads(Path(request["manifest"]).read_text())
    checked, bytes_checked, bad = 0, 0, []
    for entry in expected:
        path = root / safe_relative(entry["path"].lstrip("/"))
        try:
            actual, _ = digest_file(path)
            if path.stat().st_size != entry["bytes"] or actual != entry["sha256"]:
                bad.append(entry["path"])
            checked += 1
            bytes_checked += path.stat().st_size
        except OSError as exc:
            bad.append(entry["path"] + ": " + str(exc))
    return {"checked": checked, "bytes": bytes_checked, "bad": bad}


HUB = Path("/mnt/sparknest/hf-home/hub")


def read_refs(request):
    """Current archive ref targets; None where a ref is absent."""
    result = {}
    for name in request["refs"]:
        path = HUB / safe_relative(request["name"]) / "refs" / safe_relative(name)
        try:
            result[name] = path.read_text().strip()
        except FileNotFoundError:
            result[name] = None
    return result


def lmstudio_acceptance(request):
    """Check native sharing and that every legacy file has verified native content."""
    repo = HUB / safe_relative(request["name"])
    store = (HUB / "blobs").resolve()
    unshared = []
    for etag in request["lfs"]:
        path = repo / "blobs" / safe_relative(etag)
        if not path.is_symlink() or store not in path.resolve().parents or not path.is_file():
            unshared.append(etag)
    expected = {(entry["sha256"], entry["bytes"]) for entry in json.loads(Path(request["manifest"]).read_text())}
    legacy = Path("/mnt/sparknest") / safe_relative(request["legacy"].lstrip("/"))
    unmatched, count = [], 0
    def fail(exc):
        raise exc  # An unlisted subtree must block cleanup, not escape the check.
    if not legacy.is_dir():
        raise ValueError("legacy import is not a readable directory: " + str(legacy))
    for folder, dirs, files in os.walk(legacy, onerror=fail):
        for name in dirs + files:
            path = Path(folder) / name
            if path.is_symlink():
                unmatched.append(str(path) + ": link")
        for name in files:
            path = Path(folder) / name
            if path.is_symlink():
                continue
            count += 1
            sha, _ = digest_file(path)
            if (sha, path.stat().st_size) not in expected:
                unmatched.append(str(path))
    return {"unshared": unshared, "unmatched": unmatched, "legacy_files": count}


def preflight(request):
    """Omit already archived blob entries from the native directory import.

    SparkNest v0.8.3 cannot adopt a source regular file onto a destination
    blob symlink. Its directory importer can still add snapshots and refs.
    Compare existing bytes before omitting their staging links.
    """
    base = Path(request["staged"])
    existing = []
    for path in sorted(base.glob("models--*/blobs/*")):
        if not path.is_file():
            continue
        dest = Path("/mnt/sparknest/hf-home/hub") / path.relative_to(base)
        if dest.exists():
            expected, _ = digest_file(path)
            actual, _ = digest_file(dest)
            if actual != expected:
                raise ValueError("existing archive blob differs: " + str(dest))
            existing.append(str(path.relative_to(base)))
    return {"existing": existing}


def filter_stage(request):
    base = ROOT / "incoming/bulk" / safe_relative(request["key"]) / "hub"
    for relative in request["existing"]:
        path = base / safe_relative(relative)
        if path.is_file() and not path.is_symlink():
            path.unlink()  # Only a staging hard link; the verified payload receipt remains.
    return {"omitted": len(request["existing"])}


def docker(*args, capture=True):
    cmd = remote_command(ATHENA, [DOCKER, "exec", CONTAINER] + list(args), sudo=True)
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, check=True).stdout


def native_job(*args):
    response = json.loads(docker("nest", "--json", *args, "--wait"))
    job = response.get("job", {})
    failures = [failure for host in job.get("hosts", {}).values() for failure in host.get("failed", [])]
    if job.get("error") or job.get("cancelled") or failures or response.get("import", {}).get("errors") or not job.get("finished"):
        raise RuntimeError("native import job did not succeed: " + json.dumps(response))
    return response


def source_ref(item, ref):
    return "sources/" + item["source"] + "/" + item["store"] + "/" + item["key"] + "/" + ref


def finish_lmstudio(item, state, manifest):
    """Remove a legacy generic import only after its native replacement is accepted."""
    notes = state["share_job"].get("job", {}).get("notes", [])
    if any(": kept " in note for note in notes):
        raise RuntimeError("LM Studio model still has native sharing skips")
    old = safe_relative(item["legacy_import_name"])
    if len(old.parts) != 2 or item["source"] != "mac" or item["store"] != "lmstudio":
        raise ValueError("refusing cleanup outside the legacy Mac LM Studio namespace")
    script = """import http.client, socket
c = http.client.HTTPConnection('localhost', timeout=60)
c.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.sock.connect('/data/state/api.sock')
c.request('GET', '/v1/hf')
r = c.getresponse()
if r.status != 200: raise RuntimeError('model catalog HTTP ' + str(r.status))
print(r.read().decode())
"""
    catalog = json.loads(docker("python3", "-c", script))
    repo = item["name"][8:].replace("--", "/", 1)
    revisions = {rec["revision"] for rec in item["files"]}
    branches = {source_ref(item, ref) for ref, target in item.get("refs", {}).items() if target in revisions}
    # Catalog rows are per revision; accept only the converted revision, complete on Athena.
    accepted = {branch for row in catalog["repos"] if row["repo"] == repo
                and any(h.get("host") == "athena" and h.get("missing_files") == 0 for h in row.get("hosts", []))
                for branch in row.get("branches", []) if branch in branches}
    if not branches or accepted != branches:
        raise RuntimeError("converted LM Studio revision is missing or incomplete in the native catalog")
    path = "/imports/mac/lmstudio/" + str(old)
    probe = """import json, os, sys
try:
    os.lstat('/mnt/sparknest' + sys.argv[1])
except FileNotFoundError:
    print('false')
else:
    print('true')
"""
    if not json.loads(docker("python3", "-c", probe, path)):
        return {"path": path, "absent": True, "catalog_repo": repo}
    lfs = sorted({rec["etag"] for rec in item["files"] if rec.get("etag") and len(rec["etag"]) == 64})
    check = json.loads(docker("python3", "/config/ingest.py", "lmstudio-acceptance", encoded(
        {"name": item["name"], "lfs": lfs, "legacy": path, "manifest": manifest})))
    if check["unshared"] or check["unmatched"]:
        raise RuntimeError("legacy import kept: " + json.dumps(check))
    response = json.loads(docker("nest", "--json", "rm", "--recursive", path))
    if not isinstance(response, list) or any(result.get("errors") for result in response):
        raise RuntimeError("legacy import cleanup failed: " + json.dumps(response))
    return {"path": path, "result": response, "catalog_repo": repo, "acceptance": check}


def annotate_inventory(data):
    """Preserve known HF blob identities and make conflicting default refs explicit."""
    names, refs = collections.defaultdict(set), collections.defaultdict(set)
    for item in data["items"]:
        if item["kind"] != "hf":
            continue
        for rec in item["files"]:
            if rec.get("etag"):
                names[(item["name"], rec["revision"], rec["rel"], rec["identity"][0])].add(rec["etag"])
        for ref, target in item.get("refs", {}).items():
            refs[(item["name"], ref)].add(target)
        if item.get("refs_complete") is False:
            refs[(item["name"], None)].add(None)
    for item in data["items"]:
        if item["kind"] != "hf":
            continue
        for rec in item["files"]:
            known = names[(item["name"], rec["revision"], rec["rel"], rec["identity"][0])]
            if not rec.get("etag") and len(known) == 1:
                rec["etag"] = next(iter(known))
                if len(rec["etag"]) == 64:
                    if rec.get("sha256") and rec["sha256"] != rec["etag"]:
                        raise ValueError("conflicting source digests: " + rec["src"])
                    rec["sha256"] = rec["etag"]
        item["shared_refs"] = {ref: target for ref, target in item.get("refs", {}).items()
                               if len(refs[(item["name"], ref)]) == 1 and not refs[(item["name"], None)]
                               and not (item.get("legacy_import_name") and ref == "imported")}
    data["ref_conflicts"] = [{"repo": repo, "ref": ref, "targets": sorted(targets)}
                             for (repo, ref), targets in sorted(refs.items(), key=str) if ref and len(targets) > 1]
    data["schema"] = 2
    return data


def run_inventory(specs, output):
    script = Path(__file__).read_text()
    def one(spec):
        if spec["host"] == "local":
            return inventory(spec)
        cmd = remote_command(spec["host"], ["python3", "-c", script, "inventory-agent", encoded(spec)],
                             sudo=spec.get("sudo", spec["name"] == "athena"))
        return json.loads(subprocess.check_output(cmd, stdin=subprocess.DEVNULL))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, specs))
    data = annotate_inventory({"created": time.time(), "items": [item for r in results for item in r["items"]],
                               "warnings": [w for r in results for w in r["warnings"]]})
    atomic_json(output, data)
    print(json.dumps({"items": len(data["items"]), "files": sum(len(i["files"]) for i in data["items"]),
                      "logical_bytes": sum(f["identity"][0] for i in data["items"] for f in i["files"]),
                      "warnings": data["warnings"], "ref_conflicts": data["ref_conflicts"]}))


def process_start(pid):
    """Start time distinguishes this worker from a later process reusing its PID."""
    return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], stdout=subprocess.PIPE,
                          text=True).stdout.strip() or None


def archive_refs(item):
    names = list(item.get("shared_refs", {}))
    if item["kind"] != "hf" or not names:
        return {}
    return json.loads(docker("python3", "/config/ingest.py", "read-refs",
                             encoded({"name": item["name"], "refs": names})))


def wait_for_staging_jobs(key):
    """A native job from an interrupted run may still read this item's staging."""
    marker = "/data/incoming/bulk/" + key + "/"
    waited = []
    for job in json.loads(docker("nest", "--json", "jobs")).get("jobs", []):
        if not job.get("finished") and marker in job.get("what", ""):
            docker("nest", "--json", "jobs", str(job["id"]), "--wait")
            waited.append(job["id"])
    return waited


def transport_failure(exc):
    return isinstance(exc, ConnectionError) or (
        isinstance(exc, subprocess.CalledProcessError) and exc.returncode in (255, TRANSPORT_EXIT))


def run_queue(inventory_path, journal_path):
    lock = open(str(journal_path) + ".lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    worker_path = Path(journal_path).parent / "worker.json"
    worker = json.loads(worker_path.read_text()) if worker_path.exists() else {}
    worker.update(pid=os.getpid(), process_start=process_start(os.getpid()), journal=str(journal_path))
    atomic_json(worker_path, worker)
    inv = json.loads(Path(inventory_path).read_text())
    if inv.get("schema") != 2:
        inv = annotate_inventory(inv)
    journal = json.loads(Path(journal_path).read_text()) if Path(journal_path).exists() else {"items": {}, "files": {}}
    journal.pop("finished", None)
    journal.pop("blocked", None)
    valid = set(agent("probe-copies", {"copies": list(journal["files"].values())})["valid"])
    journal["files"] = {fid: value for fid, value in journal["files"].items() if value["path"] in valid}
    verified_copies = collections.defaultdict(list)
    for value in journal["files"].values():
        verified_copies[value["sha256"]].append(value)
    candidates_by_sha, candidates_by_model = {}, {}
    for item in inv["items"]:
        if item["source"] == "athena":
            for rec in item["files"]:
                if rec.get("sha256"):
                    candidates_by_sha.setdefault(rec["sha256"], rec["src"])
                if item["kind"] == "hf":
                    candidates_by_model.setdefault((item["name"], rec["revision"], rec["rel"]), rec["src"])
    script = Path(__file__).read_text()
    # Athena-local sources first, then small remote models, retaining all copies' provenance.
    ordered = sorted(inv["items"], key=lambda i: (i["source"] != "athena", sum(f["identity"][0] for f in i["files"])))
    for item in ordered:
        key = item["key"]
        previous = journal["items"].get(key, {})
        if previous.get("state") == "verified":
            # A later inventory can agree on a shared ref this item has not written yet.
            if previous.get("shared_refs", {}) == item.get("shared_refs", {}) or None not in archive_refs(item).values():
                previous["shared_refs"] = item.get("shared_refs", {})
                continue
        state = {"state": "staging", "name": item["name"], "source": item["source"],
                 "store": item["store"], "started": time.time(), "total_files": len(item["files"]) + len(item.get("trees", [])),
                 "total_bytes": sum(f["identity"][0] for f in item["files"] + item.get("trees", []))}
        journal["items"][key] = state
        atomic_json(journal_path, journal)
        print("STAGING", item["source"], item["store"], item["name"], flush=True)
        try:
            if item["store"] == "lmstudio" and item["kind"] != "hf":
                raise ValueError("LM Studio folders require verified native model identities; generic fallback is disabled")
            copies = []
            records = item["files"] + item.get("trees", [])
            for n, rec in enumerate(records):
                state["current"] = rec["rel"]
                state["current_bytes"] = rec["identity"][0]
                atomic_json(journal_path, journal)
                fid = hashlib.sha256((item["source"] + rec["src"] + str(rec["identity"])).encode()).hexdigest()
                own = journal["files"].get(fid)
                result = reusable_copy(rec, [own]) if own else None
                known = verified_copies.get(rec.get("sha256"), [])
                if result is None:
                    reusable = reusable_copy(rec, known)
                    if reusable:
                        result = dict(reusable, method="reuse-verified")
                if result is None:
                    request = {"key": key, "file": rec}
                    result = agent("receive", dict(request, probe=True))
                    candidate = rec["src"] if item["source"] == "athena" else candidates_by_sha.get(rec.get("sha256"))
                    if candidate is None and item["kind"] == "hf" and rec.get("revision"):
                        candidate = candidates_by_model.get((item["name"], rec["revision"], rec["rel"]))
                    if candidate is None and known:
                        candidate = known[0]["path"]
                    if result is None and candidate and rec.get("sha256"):
                        request["candidate"] = candidate
                        try:
                            result = agent("receive", request)
                        except subprocess.CalledProcessError as exc:
                            if item["source"] == "athena" or transport_failure(exc):
                                raise
                            # An existing candidate can be stale; use the actual source instead.
                            state.setdefault("candidate_errors", []).append((exc.stderr or b"").decode(errors="replace")[-2000:])
                            request.pop("candidate", None)
                            result = None
                    elif result is None and item["source"] == "athena":
                        request["candidate"] = rec["src"]
                        result = agent("receive", request)
                    if result is None:
                        if item["host"] != "local" and item["source"] != "athena" and os.environ.get("SPARKNEST_DIRECT_SOURCES") == "1":
                            result = agent("receive-direct", dict(request, source_host=item["host"]))
                    if result is None:
                        source_request = {"file": rec}
                        if item["host"] == "local":
                            source_cmd = [sys.executable, __file__, "send", encoded(source_request)]
                        else:
                            source_cmd = remote_command(item["host"], ["python3", "-c", script, "send", encoded(source_request)],
                                                        sudo=item.get("sudo", item["source"] == "athena"))
                        source = subprocess.Popen(source_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
                        receiver = subprocess.Popen(remote_command(ATHENA, ["python3", AGENT, "receive", encoded({"key": key, "file": rec})], sudo=True),
                                                    stdin=source.stdout, stdout=subprocess.PIPE)
                        source.stdout.close()
                        output = receiver.communicate()[0]
                        source_rc = source.wait()
                        if receiver.returncode or source_rc:
                            if source_rc == 255 or receiver.returncode == 255:
                                raise ConnectionError("SSH connection failed during transfer")
                            raise RuntimeError("transfer failed: source={} receiver={}".format(source_rc, receiver.returncode))
                        result = json.loads(output)
                journal["files"][fid] = result
                candidates_by_sha.setdefault(result["sha256"], result["path"])
                verified_copies[result["sha256"]].append(result)
                if rec.get("sha256") and rec["sha256"] != result["sha256"]:
                    raise RuntimeError("recorded SHA-256 differs for " + rec["src"])
                if rec.get("etag") and len(rec["etag"]) == 40 and rec["etag"] != result["git_sha1"]:
                    raise RuntimeError("recorded Git identity differs for " + rec["src"])
                copies.append(result)
                state["done_files"] = n + 1
                state["staged_bytes"] = sum(c["bytes"] for c in copies)
                state["current"] = rec["rel"]
                atomic_json(journal_path, journal)
                print("FILE", n + 1, "/", len(records), result["method"], result["bytes"], rec["rel"], flush=True)
            state["waited_jobs"] = wait_for_staging_jobs(key)
            # Import keeps an existing archive ref; stage only agreed refs it can actually write.
            existing = archive_refs(item)
            kept = {ref: target for ref, target in existing.items() if target not in (None, item["shared_refs"][ref])}
            if kept:
                state["archive_ref_conflicts"] = kept
                item = dict(item, shared_refs={r: t for r, t in item["shared_refs"].items() if r not in kept})
            prepared = agent("prepare", {"item": item, "copies": copies})
            staged = prepared["staged"].replace(str(ROOT / "incoming"), "/data/incoming", 1)
            state["state"] = "importing"
            atomic_json(journal_path, journal)
            if item["kind"] == "hf":
                present = json.loads(docker("python3", "/config/ingest.py", "preflight", encoded({"staged": staged})))
                state["already_present"] = agent("filter-stage", {"key": key, "existing": present["existing"]})
                state["import_job"] = native_job("import", staged, "/hf-home/hub", "--seal", "blobs")
                state["share_job"] = native_job("hf", "share", item["name"], "--verify")
            else:
                destination = "/imports/" + item["source"] + "/" + item["store"] + "/" + item["name"]
                state["import_job"] = native_job("import", staged, destination, "--seal", "all")
            state["state"] = "verifying"
            atomic_json(journal_path, journal)
            # Run the same verification routine inside the container against the FUSE view.
            verification = json.loads(docker("python3", "/config/ingest.py", "verify", encoded({"manifest": prepared["manifest"]})))
            state["verification"] = verification
            if verification["bad"]:
                raise RuntimeError("archive content verification failed: " + str(verification["bad"]))
            if item.get("legacy_import_name"):
                state["legacy_cleanup"] = finish_lmstudio(item, state, prepared["manifest"])
            state["shared_refs"] = item.get("shared_refs", {})
            state["state"] = "verified"
            state["finished"] = time.time()
            atomic_json(journal_path, journal)
            print("VERIFIED", item["source"], item["name"], verification["bytes"], flush=True)
        except Exception as exc:
            state["state"] = "failed"
            detail = getattr(exc, "stderr", None) or getattr(exc, "stdout", None)
            state["error"] = str(exc) + ("\n" + detail.decode(errors="replace")[-8000:] if detail else "")
            atomic_json(journal_path, journal)
            print("FAILED", item["source"], item["name"], str(exc), flush=True)
            if transport_failure(exc):
                journal["blocked"] = "SSH connection unavailable; resume after restoring access"
                atomic_json(journal_path, journal)
                raise
    journal["finished"] = time.time()
    atomic_json(journal_path, journal)
    counts = collections.Counter(i["state"] for i in journal["items"].values())
    print("FINISHED", dict(counts), flush=True)


def main():
    mode = sys.argv[1]
    if mode == "send-authorized":
        send_authorized(sys.argv[2])
        return
    if mode == "inventory":
        run_inventory(json.loads(Path(sys.argv[2]).read_text()), sys.argv[3])
        return
    if mode == "run":
        run_queue(sys.argv[2], sys.argv[3])
        return
    request = json.load(sys.stdin) if sys.argv[2] == "-" else decoded(sys.argv[2])
    if mode == "send":
        send(request)
        return
    try:
        result = {"inventory-agent": inventory, "receive": receive,
              "receive-direct": receive_direct,
              "prepare": prepare, "verify": verify, "preflight": preflight,
              "filter-stage": filter_stage, "probe-copies": probe_copies,
              "read-refs": read_refs, "lmstudio-acceptance": lmstudio_acceptance}[mode](request)
    except ConnectionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(TRANSPORT_EXIT)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
