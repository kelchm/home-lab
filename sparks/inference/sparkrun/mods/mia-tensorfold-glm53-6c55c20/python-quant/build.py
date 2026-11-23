#!/usr/bin/env python3
"""Rebuild the Python-equivalent GLM weight/KV variant on its qualified local MLA image."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import re
import sys
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen
from uuid import uuid4

from oci_layers import restore, verify_record


HERE = Path(__file__).resolve().parent
HOST_TESTS = (
    "test-glm-quant-ranks-host", "test-glm-mm-policy-host", "test-glm-fp8-kv-host", "test-glm-index-host",
    "test-glm-dflash-host", "test-glm-prompts-host", "test-glm-mla-port-host",
)


def sha(path):
    with path.open("rb") as contents:
        return hashlib.file_digest(contents, "sha256").hexdigest()


def require_file(path, digest):
    if path.is_symlink() or sha(path) != digest:
        raise SystemExit(f"Unexpected contents for {path}")


def image_identity(reference):
    # The containerd and classic Docker stores report different kinds of image ID in docker inspect.
    # Verify the platform config from the manifest, streaming past the shared CUDA layers.
    command = ["docker", "image", "save", "--platform", "linux/arm64", reference]
    digests = {}
    manifests = []
    oci_index = {}
    manifest_configs = {}
    with subprocess.Popen(command, stdout=subprocess.PIPE) as export:
        with tarfile.open(fileobj=export.stdout, mode="r|") as image:
            for member in image:
                if member.isfile() and member.size < 4 * 1024 * 1024:
                    data = image.extractfile(member).read()
                    if member.name == "manifest.json":
                        manifests = json.loads(data)
                    elif member.name == "index.json":
                        oci_index = json.loads(data)
                    elif member.name.startswith("blobs/sha256/") and data.startswith(b"{"):
                        document = json.loads(data)
                        config = document.get("config", {})
                        if document.get("schemaVersion") == 2 and isinstance(config, dict) and "digest" in config:
                            manifest_configs["sha256:" + hashlib.sha256(data).hexdigest()] = config["digest"]
                    digests[member.name] = hashlib.sha256(data).hexdigest()
        if export.wait():
            raise SystemExit(f"Docker could not export {reference}")
    manifest, = manifests
    config = "sha256:" + digests[manifest["Config"]]
    # A local OCI load can drop platform hints from the index. The platform-constrained export and the
    # child manifest's own cryptographic config link identify exactly the same executable on either store.
    bound = [entry["digest"] for entry in oci_index.get("manifests", []) if manifest_configs.get(entry["digest"]) == config]
    return config, bound[0] if len(bound) == 1 else None


def pinned_identity(reference, expected_config, expected_manifest, recorded=False):
    inspect = subprocess.run(["docker", "image", "inspect", "--platform", "linux/arm64", "--format",
                              "{{json .Descriptor}}", reference], text=True, capture_output=True)
    if inspect.returncode == 0:
        descriptor = json.loads(inspect.stdout) or {}
        if descriptor.get("digest") == expected_manifest:
            annotation = descriptor.get("annotations", {}).get("config.digest")
            if annotation == expected_config or recorded:
                return expected_config, expected_manifest
    return image_identity(reference)


def dockerfile(kit, lock, parent, host_tests, prebuilt, recompiled=(), private_name=None):
    source = (kit / "docker/Dockerfile").read_text()
    for kind, stage in (("devel", "toolchain"), ("runtime", "runtime")):
        before = f"FROM nvidia/cuda:${{CUDA_VERSION}}-{kind}-${{UBUNTU}} AS {stage}"
        if source.count(before) != 1:
            raise SystemExit(f"Unexpected {stage} base in Mia archive")
        source = source.replace(before, f"FROM {lock['build'][kind + '_image']} AS {stage}")
    # This matches the established local Dockerfile. The final image takes its runtime entirely from the pinned MLA parent.
    source = source.replace("libibverbs1 ibverbs-providers rdma-core \\", "libibverbs1 ibverbs-providers rdma-core python3 \\")
    if prebuilt:
        before = "RUN --mount=type=cache,id=tensorfold-zig-global,target=/root/.cache/zig"
        if source.count(before) != 2 or source.count("-Dnvcc=/usr/local/cuda/bin/nvcc -Dsm=") != 1:
            raise SystemExit("Unexpected native build instructions in the pinned source")
        start = source.index("# Every build compiles all kernels and executables again:")
        source = source[:start] + source[source.index(before, start):]
        source = source.replace(before, "# These are the published 0019/0020 CUDA images from the matching compiler build.\nCOPY fatbins/ /opt/verified-fatbins/\nRUN cd /opt/verified-fatbins && sha256sum -c SHA256SUMS > /dev/null\n" + before, 1)
        source = source.replace("-Dnvcc=/usr/local/cuda/bin/nvcc -Dsm=", "-Dfatbins=/opt/verified-fatbins -Dsm=")
    if recompiled:
        if set(recompiled) not in ({"qmm_group.fatbin"}, {"exl3_experts.fatbin"}, {"qmm_group.fatbin", "exl3_experts.fatbin"}):
            raise SystemExit("Unknown set of locally compiled CUDA archives")
        before = "RUN cd /opt/verified-fatbins && sha256sum -c SHA256SUMS > /dev/null"
        stage = [before]
        if "qmm_group.fatbin" in recompiled:
            stage.append('RUN /usr/local/cuda/bin/nvcc -fatbin -D__CUDA_NO_HALF_OPERATORS__ -D__CUDA_NO_HALF_CONVERSIONS__ -D__CUDA_NO_BFLOAT16_CONVERSIONS__ -D__CUDA_NO_HALF2_OPERATORS__ --expt-relaxed-constexpr -std=c++20 -O3 -Izig/kernels/cuda -gencode=arch=compute_121,code=sm_121 -o /opt/verified-fatbins/qmm_group.fatbin zig/kernels/cuda/qmm_group.cu')
            # NVCC gives one unused C++ iterator symbol a process-specific name.
            # Pinning the full archive hash permits only that exact metadata difference.
            if not private_name or not re.fullmatch(r"INTERNAL_ce744d04_12_qmm_group_cu_[a-f0-9]{8}_[0-9]+", private_name):
                raise SystemExit("Invalid recorded Q4 compiler label")
            q4_digest = recompiled["qmm_group.fatbin"]
            if not re.fullmatch(r"[a-f0-9]{64}", q4_digest):
                raise SystemExit("Invalid rebuilt Q4 digest")
            fix = "perl -0777 -pi -e 's/INTERNAL_ce744d04_12_qmm_group_cu_[a-f0-9]{8}_[0-9]+(?=4cuda3std3__45__cpo9iter_swap)/" + private_name + "/g' /opt/verified-fatbins/qmm_group.fatbin"
            stage.append("RUN " + fix + " && echo '" + q4_digest + "  /opt/verified-fatbins/qmm_group.fatbin' | sha256sum -c -")
        if "exl3_experts.fatbin" in recompiled:
            if recompiled["exl3_experts.fatbin"] != "3b240de04f8f183e504510505599dc99664421bb2b14dacd5e30a1ef34bd9674":
                raise SystemExit("Unknown rebuilt EXL3 kernel digest")
            stage.append('RUN cd /src/tensorfold/zig/kernels/cuda && nvcc -fatbin -D__CUDA_NO_HALF_OPERATORS__ -D__CUDA_NO_HALF_CONVERSIONS__ -D__CUDA_NO_BFLOAT16_CONVERSIONS__ -D__CUDA_NO_HALF2_OPERATORS__ --expt-relaxed-constexpr -std=c++20 -O3 --expt-relaxed-constexpr -gencode=arch=compute_121,code=sm_121 -o /opt/verified-fatbins/exl3_experts.fatbin exl3_experts.cu && echo "3b240de04f8f183e504510505599dc99664421bb2b14dacd5e30a1ef34bd9674  /opt/verified-fatbins/exl3_experts.fatbin" | sha256sum -c -')
        stage.append("FROM quant_source AS build")
        source = source.replace(before, "\n".join(stage)).replace("FROM toolchain AS build\n", "FROM toolchain AS quant_source\n")
        if "qmm_group.fatbin" in recompiled:
            source += "FROM scratch AS q4_export\nCOPY --from=quant_source /opt/verified-fatbins/qmm_group.fatbin /qmm_group.fatbin\n"
    return source + f"""FROM {parent} AS decode_exl3
COPY --from=build /opt/tensorfold/native/bin/tensorfold-native /opt/tensorfold/native/bin/tensorfold-native
COPY --from=build /opt/tensorfold/BUILD-INFO /opt/tensorfold/BUILD-INFO
ARG PATCHES_HASH
LABEL org.opencontainers.image.base.name="{parent}" tf.python_quant="${{PATCHES_HASH}}"
FROM build AS host_controls
COPY mla-host-tests.patch host-controls.patch /tmp/
RUN --mount=type=cache,id=tensorfold-zig-global,target=/root/.cache/zig \\
    git apply --check /tmp/mla-host-tests.patch && git apply /tmp/mla-host-tests.patch \\
    && git apply --check /tmp/host-controls.patch && git apply /tmp/host-controls.patch \\
    && zig build -j3 {' '.join(host_tests)} --summary all
"""


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--mod-dir", type=Path, default=HERE.parent, help="home-lab Mia mod containing upstream.lock.json")
    args.add_argument("--kit-archive", type=Path, help="existing Mia source tarball")
    args.add_argument("--go", default="go", help="Go 1.26 executable for cold-build OCI reconstruction")
    args.add_argument("--variant", type=Path, default=HERE / "variant.json", help="variant manifest")
    args.add_argument("--fatbin-binary", type=Path, help="optional local executable from the locked primary image")
    options = args.parse_args()
    mod = options.mod_dir
    lock = json.loads((mod / "upstream.lock.json").read_text())
    variant_dir = options.variant.resolve().parent
    variant = json.loads(options.variant.read_text())
    def find_input(name):
        if Path(name).name != name:
            raise SystemExit(f"Invalid build input filename {name}")
        alternate = variant_dir / name
        return alternate if alternate.is_file() else HERE / name

    host_tests = [*HOST_TESTS, *variant.get("extra_host_tests", [])]
    if any(not re.fullmatch(r"test-[a-z0-9-]+", step) for step in host_tests):
        raise SystemExit("Invalid Zig host-test step")
    prebuilt = "cuda_inputs_sha256" in variant
    s2 = lock["derived_image"]
    mla = s2["mla_image"]
    if mla["image_id"] != variant["parent_image_id"] or pinned_identity(mla["image"], mla["image_id"], mla["manifest_digest"])[0] != variant["parent_image_id"]:
        raise SystemExit("The required local MLA parent does not match its pinned config digest")
    patches = [mod / s2["patch"], mod / mla["patch"]]
    for path in patches:
        require_file(path, lock["mod_files"][path.relative_to(mod).as_posix()])
    installed = sorted(variant["patches"])
    for name in installed:
        require_file(find_input(name), variant["patches"][name])
    extra = [find_input(name) for name in installed]
    digest = hashlib.sha256(b"".join(path.read_bytes() for path in extra)).hexdigest()[:12]
    if not variant["image"].endswith("-" + digest):
        raise SystemExit("Final tag does not identify the installed patches")
    mla_tests = mod / mla["host_tests"]
    require_file(mla_tests, lock["mod_files"][mla["host_tests"]])
    require_file(find_input("host-controls.patch"), variant["controls_sha256"])
    temp_tag = "tf-python-quant:rebuild-" + uuid4().hex
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        archive = options.kit_archive or work / "kit.tar.gz"
        if options.kit_archive is None:
            url = f"https://codeload.github.com/{lock['kit_repository']}/tar.gz/{lock['kit_revision']}"
            with urlopen(url, timeout=120) as contents, archive.open("wb") as dest:
                shutil.copyfileobj(contents, dest)
        require_file(archive, lock["kit_archive_sha256"])
        with tarfile.open(archive) as t:
            t.extractall(work, filter="data")
        kit = work / f"GLM-5.3-Flash-DGX-Sparks-1104-{lock['kit_revision']}"
        patch_dir = kit / "patches/engine"
        for path in patches + extra:
            shutil.copyfile(path, patch_dir / path.name)
        for path in patch_dir.iterdir():
            if path.is_file():
                path.chmod(0o644)
        for path in (patch_dir.parent, patch_dir):
            path.chmod(0o700)
        shutil.copyfile(mla_tests, kit / "mla-host-tests.patch")
        shutil.copyfile(find_input("host-controls.patch"), kit / "host-controls.patch")
        if prebuilt:
            primary = json.loads((HERE / "variant.json").read_text())
            if primary["image_id"] != variant["primary_image_id"]:
                raise SystemExit("Unexpected primary CUDA kernel source image")
            manifest = find_input("cuda-inputs.sha256")
            require_file(manifest, variant["cuda_inputs_sha256"])
            binary = options.fatbin_binary or work / "primary-native"
            if options.fatbin_binary is None:
                if pinned_identity(primary["image"], primary["image_id"], primary["manifest_digest"])[0] != primary["image_id"]:
                    raise SystemExit("The installed primary image has the wrong identity")
                container = subprocess.check_output(["docker", "create", "--entrypoint=/bin/true", primary["image"]], text=True).strip()
                try:
                    subprocess.run(["docker", "cp", f"{container}:/opt/tensorfold/native/bin/tensorfold-native", str(binary)], check=True)
                finally:
                    subprocess.run(["docker", "rm", container], check=True)
            require_file(binary, primary["native_binary_sha256"])
            subprocess.run([sys.executable, str(HERE / "extract-primary-fatbins.py"), str(binary), str(manifest), str(kit / "fatbins")], check=True)
        with (kit / ".dockerignore").open("a") as rules:
            if prebuilt:
                rules.write("\n!fatbins/\n!fatbins/**\n")
            rules.write("\n!mla-host-tests.patch\n!host-controls.patch\n")
        build_file = work / "Dockerfile"
        build_file.write_text(dockerfile(kit, lock, mla["image"], host_tests, prebuilt, variant.get("recompiled_kernel_sha256", {}), variant.get("q4_private_symbol")))
        build = ["docker", "build", "--platform=linux/arm64", "--provenance=true", "--progress=plain",
                 "--file", str(build_file), "--build-arg", "ZIG_CPU=baseline", "--build-arg", "ZIG_VERSION=0.17.0",
                 "--build-arg", "TOOLS=0", "--build-arg", "SM=121", "--build-arg", "TF_REF=" + lock["engine_revision"],
                 "--build-arg", "ZIG_SHA256=" + lock["build"]["zig_sha256"], "--build-arg", "PATCHES_HASH=" + digest]
        if prebuilt:
            build.extend(["--build-arg", "ZIG_JOBS=4"])
        subprocess.run([*build, "--target=host_controls", "--output=type=cacheonly", str(kit)], check=True)
        subprocess.run([*build, "--target=decode_exl3", "--tag", temp_tag, str(kit)], check=True)
        try:
            record = variant_dir / "oci-record.json.gz"
            require_file(record, variant["oci_record_sha256"])
            verify_record(record, variant["image_id"], variant["manifest_digest"])
            config, manifest = pinned_identity(temp_tag, variant["image_id"], variant["manifest_digest"], recorded=True)
            if config == variant["image_id"] and manifest == variant["manifest_digest"]:
                subprocess.run(["docker", "image", "tag", temp_tag, variant["image"]], check=True)
            else:
                print(f"Rebuilt config {config}; restoring pinned OCI metadata after verifying the two built files.", flush=True)
                restore(record, temp_tag, mla["image"], options.go, variant["image_id"], variant["manifest_digest"])
                config, manifest = pinned_identity(variant["image"], variant["image_id"], variant["manifest_digest"], recorded=True)
                if (config, manifest) != (variant["image_id"], variant["manifest_digest"]):
                    raise SystemExit(f"Docker read back {config} and {manifest}; both must match the variant lock")
            subprocess.run(["docker", "run", "--rm", "--network=none", "--entrypoint=tensorfold-native", variant["image"], "--version"], check=True)
            print(f"{variant['image']} {config}", flush=True)
        finally:
            subprocess.run(["docker", "image", "rm", temp_tag], check=False)


if __name__ == "__main__":
    main()
