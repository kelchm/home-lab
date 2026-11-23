#!/usr/bin/env python3
"""Rebuild the qualified MLA prefill image on the local S2 image and run its host controls."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen
from uuid import uuid4

from build import identity


MOD = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit-archive", type=Path, help="use a previously downloaded, verified Mia archive")
    args = parser.parse_args()
    subprocess.run([sys.executable, str(MOD / "prepare.py"), "--check"], check=True)
    lock = json.loads((MOD / "upstream.lock.json").read_text())
    parent = lock["derived_image"]
    variant = parent["mla_image"]
    tag = f"{variant['image']}-rebuild-{uuid4().hex[:12]}"

    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        _, parent_config = identity(parent["image"], work)
        if parent_config != parent["image_id"] or parent_config != variant["parent_image_id"]:
            raise SystemExit(f"Load the qualified S2 image {parent['image']} before building MLA")

        archive = args.kit_archive or (work / "kit.tar.gz")
        if args.kit_archive is None:
            url = f"https://codeload.github.com/{lock['kit_repository']}/tar.gz/{lock['kit_revision']}"
            with urlopen(url, timeout=120) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != lock["kit_archive_sha256"]:
            raise SystemExit("Mia archive hash differs from the stock lock")
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter="data")
        kit = work / f"GLM-5.3-Flash-DGX-Sparks-1104-{lock['kit_revision']}"
        for patch in (parent["patch"], variant["patch"]):
            shutil.copy(MOD / patch, kit / "patches/engine")
        shutil.copy(MOD / variant["host_tests"], kit / "mla-host-tests.patch")
        with (kit / ".dockerignore").open("a") as exclusions:
            exclusions.write("\n!mla-host-tests.patch\n")
        source = (kit / "docker/Dockerfile").read_text()
        for kind, stage in (("devel", "toolchain"), ("runtime", "runtime")):
            anchor = f"FROM nvidia/cuda:${{CUDA_VERSION}}-{kind}-${{UBUNTU}} AS {stage}"
            if source.count(anchor) != 1:
                raise SystemExit(f"Unexpected upstream {stage} stage")
            source = source.replace(anchor, f"FROM {lock['build'][kind + '_image']} AS {stage}")
        patch_hash = lock["mod_files"][variant["patch"]]
        dockerfile = work / "Dockerfile"
        # The controls record dispatch and test rank agreement separately from the 18-patch executable.
        dockerfile.write_text(source + f"""
FROM build AS mla_controls
COPY mla-host-tests.patch /tmp/mla-host-tests.patch
RUN --mount=type=cache,id=tensorfold-zig-global,target=/root/.cache/zig \
    git apply --check /tmp/mla-host-tests.patch && git apply /tmp/mla-host-tests.patch \
    && zig build -j3 test-glm-mla-port-host test-glm-prompts-host --summary all
FROM {parent['image']} AS prompt_mla
COPY --from=build /opt/tensorfold/native/bin/tensorfold-native /opt/tensorfold/native/bin/tensorfold-native
COPY --from=build /opt/tensorfold/BUILD-INFO /opt/tensorfold/BUILD-INFO
LABEL org.opencontainers.image.base.name="{parent['image']}" tf.prompt_mla="{patch_hash[:12]}"
""")
        build = [
            "docker", "build", "--progress=plain", "--platform=linux/arm64", "--provenance=false",
            "--file", str(dockerfile), "--build-arg", f"TF_REF={lock['engine_revision']}",
            "--build-arg", f"ZIG_SHA256={lock['build']['zig_sha256']}",
            "--build-arg", "ZIG_VERSION=0.17.0", "--build-arg", "ZIG_CPU=baseline",
            "--build-arg", "SM=121", "--build-arg", "TOOLS=0",
        ]
        subprocess.run([*build, "--target=mla_controls", "--output=type=cacheonly", str(kit)], check=True)
        subprocess.run([*build, "--target=prompt_mla", "--tag", tag, str(kit)], check=True)
        subprocess.run(["docker", "run", "--rm", "--network=none", "--entrypoint=tensorfold-native", tag, "--version"], check=True)
        built_id, config = identity(tag, work)
        if config != variant["image_id"]:
            raise SystemExit(f"Rebuild {tag} produced {config}. The qualified recipe requires {variant['image_id']}.")
        subprocess.run(["docker", "image", "tag", built_id, variant["image"]], check=True)
    print(f"{variant['image']} {config}")


if __name__ == "__main__":
    main()
