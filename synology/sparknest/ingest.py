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

ATHENA = "kelchm@10.32.20.5"
ROOT = Path("/volume1/models/sparknest")
AGENT = "/volume1/docker/sparknest/ingest.py"
CONTAINER = "sparknest-sparknest-1"
DOCKER = "/usr/local/bin/docker"
SKIP = {"@eaDir", ".DS_Store", ".locks", "token", "stored_tokens"}
CHUNK = 4 << 20


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
                    warnings.append("linked repository directory: " + str(repo))
                if not (repo / "snapshots").is_dir():
                    continue
                files, refs, trees = [], {}, []
                for rev in children(repo / "snapshots", warnings):
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
                for path in walk(repo / "refs", warnings) if (repo / "refs").exists() else []:
                    if path.is_file():
                        refs[str(path.relative_to(repo / "refs"))] = path.read_text().strip()
                if files:
                    items.append({"kind": "hf", "name": repo.name, "files": files,
                                  "refs": refs, "trees": trees, "store": store["name"]})
            # Retain bytes not assigned to a snapshot, without inventing a revision.
            orphan_files = []
            for blobroot in [root / "blobs"] + [p / "blobs" for p in repos]:
                if not blobroot.exists():
                    continue
                for path in walk(blobroot, warnings):
                    if path.is_file() and not path.is_symlink() and str(path.resolve()) not in referenced:
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
                    if owner.is_file():
                        loose.append(file_record(owner, owner.relative_to(root)))
                    elif owner.is_dir():
                        for model in children(owner, warnings):
                            if model.is_dir():
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
        item["key"] = hashlib.sha256(json.dumps({"source": spec["name"], "store": item["store"],
                                                "name": item["name"], "files": item["files"],
                                                "refs": item.get("refs", {})}, sort_keys=True).encode()).hexdigest()[:20]
    return {"items": items, "warnings": warnings}


def remote_command(host, argv, sudo=False):
    command = ("sudo -n " if sudo else "") + shlex.join([str(a) for a in argv])
    control = os.environ.get("SPARKNEST_SSH_CONTROL_DIR")
    connection = ["-S", str(Path(control) / host.split("@")[-1])] if control else []
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


def receive(request):
    """Receive one file, or reflink an existing Athena copy; verify before publish."""
    rec = request["file"]
    folder = ROOT / "incoming/bulk" / request["key"] / "payloads"
    folder.mkdir(parents=True, exist_ok=True)
    fid = hashlib.sha256((rec["src"] + str(rec["identity"])).encode()).hexdigest()
    final, receipt = folder / fid, folder / (fid + ".json")
    if final.is_file() and receipt.is_file():
        result = json.loads(receipt.read_text())
        if identity(final) == result["stored_identity"]:
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
        remaining = rec["identity"][0]
        sha_obj = hashlib.sha256()
        git_obj = hashlib.sha1(("blob " + str(remaining) + "\0").encode()) if remaining <= 1 << 20 or (rec.get("etag") and len(rec["etag"]) == 40) else None
        with partial.open("wb") as target:
            while remaining:
                chunk = sys.stdin.buffer.read(min(CHUNK, remaining))
                if not chunk:
                    raise ValueError("transfer ended early")
                target.write(chunk)
                sha_obj.update(chunk)
                if git_obj is not None:
                    git_obj.update(chunk)
                remaining -= len(chunk)
            target.flush()
            os.fsync(target.fileno())
        trailer = sys.stdin.buffer.read(64).decode("ascii")
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
            source_ref = "sources/" + item["source"] + "/" + item["store"] + "/" + item["key"] + "/" + ref
            names = [source_ref]
            if ref in item.get("shared_refs", {}):
                names.append(ref)
            for name in names:
                path = repo / "refs" / safe_relative(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(target)
            expected.append({"path": "/hf-home/hub/" + item["name"] + "/refs/" + source_ref,
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
                               if len(refs[(item["name"], ref)]) == 1}
    data["ref_conflicts"] = [{"repo": repo, "ref": ref, "targets": sorted(targets)}
                             for (repo, ref), targets in sorted(refs.items()) if len(targets) > 1]
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


def run_queue(inventory_path, journal_path):
    lock = open(str(journal_path) + ".lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    inv = json.loads(Path(inventory_path).read_text())
    if inv.get("schema") != 2:
        inv = annotate_inventory(inv)
    journal = json.loads(Path(journal_path).read_text()) if Path(journal_path).exists() else {"items": {}, "files": {}}
    journal.pop("finished", None)
    journal.pop("blocked", None)
    valid = set(agent("probe-copies", {"copies": list(journal["files"].values())})["valid"])
    journal["files"] = {fid: value for fid, value in journal["files"].items() if value["path"] in valid}
    verified_copies = {v["sha256"]: v for v in journal["files"].values()}
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
        if journal["items"].get(key, {}).get("state") == "verified":
            continue
        state = {"state": "staging", "name": item["name"], "source": item["source"],
                 "store": item["store"], "started": time.time(), "total_files": len(item["files"]) + len(item.get("trees", [])),
                 "total_bytes": sum(f["identity"][0] for f in item["files"] + item.get("trees", []))}
        journal["items"][key] = state
        atomic_json(journal_path, journal)
        print("STAGING", item["source"], item["store"], item["name"], flush=True)
        try:
            copies = []
            records = item["files"] + item.get("trees", [])
            for n, rec in enumerate(records):
                state["current"] = rec["rel"]
                state["current_bytes"] = rec["identity"][0]
                atomic_json(journal_path, journal)
                fid = hashlib.sha256((item["source"] + rec["src"] + str(rec["identity"])).encode()).hexdigest()
                result = journal["files"].get(fid)
                if result is None and rec.get("sha256") in verified_copies:
                    result = dict(verified_copies[rec["sha256"]], method="reuse-verified")
                if result is None:
                    request = {"key": key, "file": rec}
                    result = agent("receive", dict(request, probe=True))
                    candidate = rec["src"] if item["source"] == "athena" else candidates_by_sha.get(rec.get("sha256"))
                    if candidate is None and item["kind"] == "hf" and rec.get("revision"):
                        candidate = candidates_by_model.get((item["name"], rec["revision"], rec["rel"]))
                    if result is None and candidate and rec.get("sha256"):
                        request["candidate"] = candidate
                        try:
                            result = agent("receive", request)
                        except subprocess.CalledProcessError as exc:
                            if item["source"] == "athena" or exc.returncode == 255:
                                raise
                            # An existing candidate can be stale; use the actual source instead.
                            state.setdefault("candidate_errors", []).append((exc.stderr or b"").decode(errors="replace")[-2000:])
                            request.pop("candidate", None)
                            result = None
                    elif result is None and item["source"] == "athena":
                        request["candidate"] = rec["src"]
                        result = agent("receive", request)
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
                verified_copies.setdefault(result["sha256"], result)
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
            if isinstance(exc, ConnectionError) or (isinstance(exc, subprocess.CalledProcessError) and exc.returncode == 255):
                journal["blocked"] = "SSH connection unavailable; resume after restoring access"
                atomic_json(journal_path, journal)
                raise
    journal["finished"] = time.time()
    atomic_json(journal_path, journal)
    counts = collections.Counter(i["state"] for i in journal["items"].values())
    print("FINISHED", dict(counts), flush=True)


def main():
    mode = sys.argv[1]
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
    result = {"inventory-agent": inventory, "receive": receive,
              "prepare": prepare, "verify": verify, "preflight": preflight,
              "filter-stage": filter_stage, "probe-copies": probe_copies}[mode](request)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
