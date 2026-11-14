#!/usr/bin/env python3
"""Turn LM Studio folders into pinned HF imports using saved Hub API trees.

Metadata input: one <original-item-key>.json per collection, containing the
canonical `repo`, immutable `revision`, and API tree `files`. Fetch metadata
from the Hub, including historical revisions when necessary; never invent a
commit. Output is a replacement inventory plus Git-hash proofs for existing
receipts. Stop the coordinator before replacing its inventory or journal.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re

import ingest


def file_id(item, rec):
    return hashlib.sha256((item["source"] + rec["src"] + str(rec["identity"])).encode()).hexdigest()


def tree_files(metadata):
    if not re.fullmatch(r"[0-9a-f]{40}", metadata["revision"]):
        raise ValueError("HF revision must be an immutable commit")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", metadata["repo"]):
        raise ValueError("invalid HF repository identity")
    result = {}
    for entry in metadata["files"]:
        if entry.get("type") != "file":
            continue
        path = str(ingest.safe_relative(entry["path"]))
        lfs = entry.get("lfs")
        etag = lfs["oid"] if lfs else entry["oid"]
        if not re.fullmatch(r"[0-9a-f]{%d}" % (64 if lfs else 40), etag):
            raise ValueError("invalid HF file identity: " + path)
        result[path] = {"size": entry["size"], "blob_id": entry["oid"],
                        "lfs_sha256": etag if lfs else None, "xet_hash": entry.get("xetHash")}
    return result


def convert(item, metadata, receipts, metadata_dir):
    files = tree_files(metadata)
    converted, proofs, mappings = [], {}, []
    for original in item["files"]:
        rec = dict(original)
        fid = file_id(item, rec)
        receipt = receipts.get(fid)
        sha = receipt["sha256"] if receipt else None
        git = receipt.get("git_sha1") if receipt else None
        # Git-managed metadata can exceed the old receiver's 1 MiB threshold.
        # Read only small source files, and tie that proof to the receipt SHA.
        if rec["identity"][0] <= 32 << 20:
            path = Path(rec["src"])
            if ingest.identity(path) != rec["identity"]:
                raise ValueError("source changed: " + str(path))
            measured_sha, git = ingest.digest_file(path, with_git=True)
            if ingest.identity(path) != rec["identity"] or (sha and measured_sha != sha):
                raise ValueError("source/receipt mismatch: " + str(path))
            sha = measured_sha
            if receipt:
                proofs[fid] = {"sha256": sha, "git_sha1": git}

        def matches(entry):
            if entry["size"] != rec["identity"][0]:
                return False
            expected = entry["lfs_sha256"] or entry["blob_id"]
            actual = sha if entry["lfs_sha256"] else git
            return actual == expected if actual else bool(entry["lfs_sha256"])

        target = rec["rel"]
        if target not in files or not matches(files[target]):
            # Renames are accepted only with an already verified content ID.
            candidates = [name for name, entry in files.items() if matches(entry)
                          and (sha if entry["lfs_sha256"] else git)]
            if len(candidates) != 1:
                raise ValueError("no unique published match: " + item["name"] + "/" + target)
            target = candidates[0]
        entry = files[target]
        rec.update(rel=target, revision=metadata["revision"],
                   etag=entry["lfs_sha256"] or entry["blob_id"],
                   sha256=entry["lfs_sha256"] or sha)
        converted.append(rec)
        mappings.append({"local": original["rel"], "published": target})
    if len({r["rel"] for r in converted}) != len(converted):
        raise ValueError("multiple local files map to one published path")
    tree = {"files": files}
    tree_path = metadata_dir / (item["key"] + "-tree.json")
    if not tree_path.exists() or json.loads(tree_path.read_text()) != tree:
        ingest.atomic_json(tree_path, tree)
    tree_rec = ingest.file_record(tree_path, metadata["revision"] + ".json",
                                  sha256=ingest.digest_file(tree_path)[0])
    result = dict(item, kind="hf", name="models--" + metadata["repo"].replace("/", "--"),
                  files=converted, trees=[tree_rec], refs={"imported": metadata["revision"]},
                  shared_refs={}, legacy_import_name=item["name"],
                  provenance={"source_key": item["key"], "local_collection": item["name"],
                              "repo": metadata["repo"], "revision": metadata["revision"],
                              "mappings": mappings, "partial_snapshot": True})
    # New identity prevents an earlier generic "verified" state skipping conversion.
    result["key"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:20]
    return result, proofs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    journal = json.loads(args.journal.read_text())
    proofs, converted, items = {}, [], []
    for item in inventory["items"]:
        if item["store"] == "lmstudio" and item["kind"] != "hf":
            metadata = json.loads((args.metadata_dir / (item["key"] + ".json")).read_text())
            item, hashes = convert(item, metadata, journal["files"], args.metadata_dir)
            proofs.update(hashes)
            converted.append(item["provenance"])
        item.pop("shared_refs", None)
        items.append(item)
    inventory["items"] = items
    ingest.atomic_json(args.output, ingest.annotate_inventory(inventory))
    ingest.atomic_json(args.output.with_suffix(".proofs.json"), proofs)
    print(json.dumps({"converted": len(converted), "proofs": len(proofs), "output": str(args.output)}))


if __name__ == "__main__":
    main()
