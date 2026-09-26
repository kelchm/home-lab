#!/usr/bin/env python3
"""Materialize the checksum-pinned Mia files for SparkRun's native mod copier.

Run on the SparkRun control host before launch. No host paths, model weights,
containers, or serving processes are managed here. --check works offline.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request


def verify(root, lock):
    runtime_names = [Path(name).name for name in lock["files"]
                     if name.startswith(("overlay/", "files/", "scripts/"))]
    if len(runtime_names) != len(set(runtime_names)):
        raise ValueError("Runtime file names collide in /opt/glm53")
    if len(lock["patches"]) != len(set(lock["patches"])) or any(
        name not in lock["files"] or Path(name).name not in runtime_names
        for name in lock["patches"]
    ):
        raise ValueError("Patch sequence must contain unique, locked runtime files")
    for name, expected in lock["files"].items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Missing or changed upstream file: {name}; run prepare.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    lock = json.loads((here / "upstream.lock.json").read_text())
    root = here / "upstream"
    if not args.check:
        url = f"https://codeload.github.com/{lock['repository']}/tar.gz/{lock['revision']}"
        with urllib.request.urlopen(url, timeout=120) as response:
            archive = response.read()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            prefix = tar.getmembers()[0].name.split("/")[0]
            # Read only named regular files; never extract archive paths or links.
            files = {}
            for name, expected in lock["files"].items():
                member = tar.getmember(f"{prefix}/{name}")
                if not member.isfile():
                    raise ValueError(f"Not a regular file: {name}")
                data = tar.extractfile(member).read()
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError(f"Upstream checksum mismatch: {name}")
                files[name] = data
            for name, data in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    verify(root, lock)
    print(f"Verified {len(lock['files'])} Mia files at {lock['revision']}")


if __name__ == "__main__":
    main()
