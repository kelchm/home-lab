#!/usr/bin/env bash
# Build out/tensorfold-native from the pins in upstream.lock.json on any linux/arm64 Docker daemon.
# No GPU, no nvcc and no NVIDIA base image: the CUDA fatbins are the published image's own, fetched by layer digest.
# --record writes the built binary's sha256 into the lock; without it a binary that differs from the lock fails.
set -euo pipefail
mod=$(cd "$(dirname "$0")/.." && pwd)
record=0
[[ "${1:-}" == --record ]] && record=1
rm -rf "$mod/out"
python3 "$mod/prepare.py" --check
lock() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); [v:=v[k] for k in sys.argv[2:]]; print(v)' "$mod/upstream.lock.json" "$@"; }

ctx=$(mktemp -d)
trap 'rm -rf -- "$ctx"' EXIT
cp -R "$mod/patches" "$ctx/patches"

image=$(lock image)
repository=${image#ghcr.io/}; repository=${repository%@*}
layer=$(lock build fatbin_layer)
token=$(curl -fsS "https://ghcr.io/token?scope=repository:${repository}:pull" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -fsSL -H "Authorization: Bearer $token" -o "$ctx/layer.tar.gz" "https://ghcr.io/v2/${repository}/blobs/${layer}"
echo "${layer#sha256:}  $ctx/layer.tar.gz" | shasum -a 256 -c - >/dev/null
tar -xzf "$ctx/layer.tar.gz" -C "$ctx" opt/tensorfold/fatbin opt/tensorfold/native/bin/tensorfold-native
echo "$(lock build published_binary_sha256)  $ctx/opt/tensorfold/native/bin/tensorfold-native" | shasum -a 256 -c - >/dev/null
mv "$ctx/opt/tensorfold/fatbin" "$ctx/fatbin"
rm -rf "$ctx/opt" "$ctx/layer.tar.gz"

docker build --platform linux/arm64 --output "type=local,dest=$mod/out" -f "$mod/build/Dockerfile" \
  --build-arg BASE_IMAGE="$(lock build base_image)" \
  --build-arg ZIG_VERSION="$(lock build zig_version)" --build-arg ZIG_SHA256="$(lock build zig_sha256)" \
  --build-arg ENGINE_REPOSITORY="$(lock engine_repository)" --build-arg ENGINE_REVISION="$(lock engine_revision)" \
  --build-arg KIT_REPOSITORY="$(lock kit_repository)" --build-arg KIT_REVISION="$(lock kit_revision)" \
  --build-arg KIT_PATCHES_SHA256="$(lock build kit_patches_sha256)" \
  --build-arg ZIG_TARGET="$(lock build zig_target)" --build-arg ZIG_CPU="$(lock build zig_cpu)" \
  "$ctx"

built=$(shasum -a 256 "$mod/out/tensorfold-native" | cut -d' ' -f1)
echo "Built out/tensorfold-native sha256 $built"
if (( record )); then
  python3 "$mod/prepare.py" --record-binary
else
  python3 "$mod/prepare.py" --check --require-binary
fi
