#!/usr/bin/env bash
# Build the frozen kit off the Sparks. Export and verify its config ID before using a rebuild.
set -euo pipefail
mod=$(cd -- "$(dirname -- "$0")" && pwd)
python3 "$mod/prepare.py" --check
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

lock() {
  python3 -c 'import functools,json,sys; print(functools.reduce(lambda value,key: value[key], sys.argv[2:], json.load(open(sys.argv[1]))))' "$mod/upstream.lock.json" "$@"
}

kit=$(lock kit_repository)
revision=$(lock kit_revision)
curl -fLsS --retry 2 --max-time 180 "https://codeload.github.com/$kit/tar.gz/$revision" -o "$work/kit.tar.gz"
printf '%s  %s\n' "$(lock kit_archive_sha256)" "$work/kit.tar.gz" | shasum -a 256 -c -
mkdir "$work/kit"
tar -xzf "$work/kit.tar.gz" --strip-components 1 -C "$work/kit"

# Add Python for the SparkRun launcher and readiness gate. Pin both upstream CUDA bases by digest.
python3 - "$mod/upstream.lock.json" "$work" <<'PY'
import json
from pathlib import Path
import sys

lock = json.loads(Path(sys.argv[1]).read_text())
work = Path(sys.argv[2])
source = (work / "kit/docker/Dockerfile").read_text()
for old, new in (
    ("FROM nvidia/cuda:${CUDA_VERSION}-devel-${UBUNTU} ", f"FROM {lock['build']['devel_image']} "),
    ("FROM nvidia/cuda:${CUDA_VERSION}-runtime-${UBUNTU} ", f"FROM {lock['build']['runtime_image']} "),
    ("libibverbs1 ibverbs-providers rdma-core", "libibverbs1 ibverbs-providers rdma-core python3"),
):
    if source.count(old) != 1:
        raise SystemExit(f"Unexpected upstream Dockerfile anchor {old!r}")
    source = source.replace(old, new)
(work / "Dockerfile").write_text(source)
PY

# Use a separate rebuild tag so a config-ID mismatch cannot move the staged tag.
tag="$(lock image)-rebuild-$(date -u +%Y%m%dT%H%M%SZ)-$$"
docker build --progress=plain --platform linux/arm64 -f "$work/Dockerfile" -t "$tag" \
  --label "org.opencontainers.image.source=https://github.com/$kit" \
  --label "org.opencontainers.image.revision=$revision" \
  --build-arg "TF_REF=$(lock engine_revision)" --build-arg "ZIG_SHA256=$(lock build zig_sha256)" \
  --build-arg ZIG_VERSION=0.17.0 --build-arg SM=121 --build-arg ZIG_CPU=baseline --build-arg TOOLS=0 \
  --build-arg "PATCHES_HASH=$(lock upstream_patch_hash)" "$work/kit"
docker run --rm --network none --entrypoint /usr/local/bin/tensorfold-native "$tag" --version
printf 'Built %s. Compare the exported config ID with %s before staging.\n' "$tag" "$(lock image_id)"
