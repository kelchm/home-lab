#!/usr/bin/env python3
"""Prepare checksum-pinned TensorFold build inputs; never launch containers."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request


def unpack(data, root):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        prefix = archive.getmembers()[0].name.split("/")[0] + "/"
        for member in archive.getmembers():
            if not member.name.startswith(prefix) or not member.isfile():
                continue
            relative = Path(member.name[len(prefix):])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe source archive path")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
            target.chmod(member.mode & 0o777)


def verify_sources(root, lock):
    expected = {}
    for key in ("kit", "engine"):
        destination = root / ("kit" if key == "kit" else "kit/vendor/TensorFold")
        with tarfile.open(root / (key + ".tar.gz"), mode="r:gz") as archive:
            prefix = archive.getmembers()[0].name.split("/")[0] + "/"
            for member in archive.getmembers():
                if not member.name.startswith(prefix) or not member.isfile():
                    continue
                relative = Path(member.name[len(prefix):])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe source archive path")
                data = archive.extractfile(member).read()
                if key == "kit" and relative.as_posix() == "docker/Dockerfile":
                    text = data.decode()
                    old = "FROM nvcr.io/nvidia/pytorch:26.07-py3"
                    if text.count(old) != 1:
                        raise ValueError("Upstream base-image anchor drifted")
                    data = text.replace(old, "FROM " + lock["base_image"], 1).encode()
                expected[destination / relative] = data
    for path, data in expected.items():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise ValueError(f"Missing or changed build input: {path.relative_to(root)}")
    unexpected = {path for path in (root / "kit").rglob("*") if path.is_file()} - expected.keys()
    if unexpected:
        raise ValueError(f"Unpinned build inputs: {sorted(str(path.relative_to(root)) for path in unexpected)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    lock = json.loads((here / "upstream.lock.json").read_text())
    root = here / "upstream"
    for key in ("kit", "engine"):
        entry = lock[key]
        cached = root / (key + ".tar.gz")
        if not args.check:
            url = f"https://codeload.github.com/{entry['repository']}/tar.gz/{entry['revision']}"
            data = urllib.request.urlopen(url, timeout=120).read()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"{key} archive checksum mismatch")
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
            unpack(data, root / ("kit" if key == "kit" else "kit/vendor/TensorFold"))
        if not cached.is_file() or hashlib.sha256(cached.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Missing or changed {key} archive; run prepare.py")
    if not args.check:
        dockerfile = root / "kit/docker/Dockerfile"
        text = dockerfile.read_text()
        old = "FROM nvcr.io/nvidia/pytorch:26.07-py3"
        if text.count(old) != 1:
            raise ValueError("Upstream base-image anchor drifted")
        dockerfile.write_text(text.replace(old, "FROM " + lock["base_image"], 1))
    verify_sources(root, lock)
    if (root / "kit/results/W20/build-patches.txt").read_text().split() != lock["patches"]:
        raise ValueError("Selected W20 patches differ from the locked build list")
    print(f"Verified TensorFold kit {lock['kit']['revision']} and engine {lock['engine']['revision']}")


if __name__ == "__main__":
    main()
