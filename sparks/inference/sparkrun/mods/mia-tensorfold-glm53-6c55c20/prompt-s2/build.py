#!/usr/bin/env python3
"""Build the optional SM121 packed-prefill overlay on the frozen Mia image."""
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


HERE = Path(__file__).resolve().parent
STOCK = HERE.parent


def identity(reference, work):
    """Return the immutable daemon ID and the config digest used by the recipe."""
    image_id = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", reference], text=True).strip()
    # Docker's containerd store exposes the OCI manifest ID. Its config is in the exported archive.
    archive = work / "image.tar"
    subprocess.run(["docker", "image", "save", "--platform", "linux/arm64", "--output", str(archive), image_id], check=True)
    with tarfile.open(archive) as tar:
        (manifest,) = json.load(tar.extractfile("manifest.json"))
        config = tar.extractfile(manifest["Config"]).read()
    archive.unlink()
    return image_id, "sha256:" + hashlib.sha256(config).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit-archive", type=Path, help="use a previously downloaded, verified Mia archive")
    args = parser.parse_args()
    subprocess.run([sys.executable, str(STOCK / "prepare.py"), "--check"], check=True)
    lock = json.loads((STOCK / "upstream.lock.json").read_text())
    variant = lock["derived_image"]
    patch = STOCK / variant["patch"]
    patch_hash = lock["mod_files"][variant["patch"]]
    source_image = lock["image"]
    tag = f"{variant['image']}-rebuild-{uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        _, config = identity(source_image, work)
        if config != lock["image_id"] or config != variant["parent_image_id"]:
            raise SystemExit(f"Load the frozen stock image {source_image} before building the overlay")
        archive = args.kit_archive or (work / "kit.tar.gz")
        if not args.kit_archive:
            url = f"https://codeload.github.com/{lock['kit_repository']}/tar.gz/{lock['kit_revision']}"
            with urlopen(url, timeout=120) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != lock["kit_archive_sha256"]:
            raise SystemExit("Mia archive hash differs from the parent lock")
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter="data")
        kit = work / f"GLM-5.3-Flash-DGX-Sparks-1104-{lock['kit_revision']}"
        shutil.copy(patch, kit / "patches/engine")
        source = (kit / "docker/Dockerfile").read_text()
        anchor = "FROM nvidia/cuda:${CUDA_VERSION}-devel-${UBUNTU} AS toolchain"
        if source.count(anchor) != 1:
            raise SystemExit("Unexpected upstream toolchain stage")
        source = source.replace(anchor, f"FROM {lock['build']['devel_image']} AS toolchain")
        # The native executable embeds the CUDA fatbins. Keep the stock runtime layers and codec libraries.
        derived = f"""
FROM {source_image} AS prompt_s2
COPY --from=build /opt/tensorfold/native/bin/tensorfold-native /opt/tensorfold/native/bin/tensorfold-native
COPY --from=build /opt/tensorfold/BUILD-INFO /opt/tensorfold/BUILD-INFO
LABEL org.opencontainers.image.base.name="{source_image}" tf.prompt_s2="{patch_hash}"
"""
        dockerfile = work / "Dockerfile"
        dockerfile.write_text(source + derived)
        subprocess.run([
            "docker", "build", "--progress=plain", "--platform=linux/arm64", "--provenance=false",
            "--target=prompt_s2", "--tag", tag, "--file", str(dockerfile),
            "--build-arg", f"TF_REF={lock['engine_revision']}",
            "--build-arg", f"ZIG_SHA256={lock['build']['zig_sha256']}",
            "--build-arg", "ZIG_VERSION=0.17.0", "--build-arg", "ZIG_CPU=baseline",
            "--build-arg", "SM=121", "--build-arg", "TOOLS=0", str(kit),
        ], check=True)
        subprocess.run(["docker", "run", "--rm", "--network=none", "--entrypoint=tensorfold-native", tag, "--version"], check=True)
        built_id, config = identity(tag, work)
        if config != variant["image_id"]:
            raise SystemExit(f"Rebuild {tag} produced {config}. The qualified recipe requires {variant['image_id']}.")
        subprocess.run(["docker", "image", "tag", built_id, variant["image"]], check=True)
    print(f"{variant['image']} {config}")


if __name__ == "__main__":
    main()
