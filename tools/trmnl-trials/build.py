#!/usr/bin/env python3
"""Build one experimental server from a verified upstream snapshot."""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parent
SERVERS = {"fastapi": "byos_fastapi", "django": "byos_django", "phoenix": "byos_phoenix", "node-lite": "byos_node_lite"}
FILES = {
    "fastapi": ["fastapi-requirements.txt"],
    "django": ["django-requirements.txt", "django_asgi.py", "django-entrypoint.sh"],
    "phoenix": ["phoenix-package.json", "phoenix-package-lock.json"],
    "node-lite": ["node-package-lock.json", "node-config.mjs"],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", choices=SERVERS)
    parser.add_argument("--docker-context", required=True)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    source = next(item for item in json.loads((ROOT / "sources.json").read_text()) if item["repo"] == SERVERS[args.server])
    inputs = hashlib.sha256(Path(__file__).read_bytes())
    inputs.update(json.dumps(source, sort_keys=True).encode())
    selected = [f"{args.server}.Dockerfile", *FILES[args.server]]
    for path in sorted(ROOT / "images" / name for name in selected):
        inputs.update(path.name.encode())
        inputs.update(path.read_bytes())
    tag = f"source-{source['sha'][:12]}-build-{inputs.hexdigest()[:12]}"
    image = f"ghcr.io/kelchm/trmnl-trial-{args.server}:{tag}"
    url = f"https://codeload.github.com/usetrmnl/{source['repo']}/tar.gz/{source['sha']}"
    archive = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(archive).hexdigest() != source["sha256"]:
        raise ValueError("Upstream source archive checksum changed")
    with tempfile.TemporaryDirectory(prefix=f"trmnl-{args.server}-") as temp:
        context = Path(temp)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            for member in bundle.getmembers():
                relative = PurePosixPath(*PurePosixPath(member.name).parts[1:])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe source archive path")
                if member.isfile():
                    target = context.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bundle.extractfile(member).read())
                    target.chmod(member.mode & 0o777)
        (context / ".trial").mkdir()
        for name in selected:
            shutil.copy2(ROOT / "images" / name, context / ".trial" / name)
        docker = ["docker", "--context", args.docker_context]
        command = docker + ["build", "--platform", "linux/amd64", "--progress", "plain", "--file", str(ROOT / "images" / f"{args.server}.Dockerfile"), "--tag", image,
                           "--label", "org.opencontainers.image.source=https://github.com/kelchm/home-lab",
                           "--label", f"org.opencontainers.image.version={tag}",
                           "--label", f"io.trmnl.upstream.revision={source['sha']}", str(context)]
        subprocess.run(command, check=True)
        if args.push:
            subprocess.run(docker + ["push", image], check=True)
        print(json.dumps({"server": args.server, "image": image, "upstream": source["sha"]}))


if __name__ == "__main__":
    main()
