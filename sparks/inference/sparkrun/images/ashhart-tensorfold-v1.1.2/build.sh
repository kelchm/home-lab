#!/usr/bin/env bash
# build.sh: build image.lock.json's image on any linux/arm64 Docker daemon (no GPU) from its pins, never on a Spark.
# The build fails unless the unpatched server equals the release's and the patched one the lock's binary_sha256.
# --record writes the built server's sha256 and the image ID into the lock instead of checking them.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
record=0
[[ "${1:-}" == --record ]] && record=1
lock() { python3 -c 'import functools,json,sys; print(functools.reduce(lambda v, k: v[k], sys.argv[2:], json.load(open(sys.argv[1]))))' "$here/image.lock.json" "$@"; }

for patch in "$here"/patches/*.patch; do
  echo "$(lock patches "patches/${patch##*/}")  $patch" | shasum -a 256 -c - >/dev/null
done
patch_hash=$(cat "$here"/patches/*.patch | shasum -a 256 | cut -c1-12)
nccl_release=$(lock build nccl_release)
image=$(lock image)
[[ "$image" == *"-$patch_hash" ]] || { echo "image tag $image does not end in the patches' hash $patch_hash" >&2; exit 1; }

ctx=$(mktemp -d)
trap 'rm -rf -- "$ctx"' EXIT
curl -fsSL -o "$ctx/release.tar.gz" "$(lock tensorfold archive)"
echo "$(lock tensorfold archive_sha256)  $ctx/release.tar.gz" | shasum -a 256 -c - >/dev/null
mkdir "$ctx/release"
tar -xzf "$ctx/release.tar.gz" -C "$ctx/release" --strip-components=1
rm "$ctx/release.tar.gz"
echo "$(lock tensorfold release_binary_sha256)  $ctx/release/bin/tensorfold-native" | shasum -a 256 -c - >/dev/null
cp -R "$here/patches" "$ctx/patches"

docker build --platform linux/arm64 -t "$image" -f "$here/Dockerfile" \
  --build-arg DEVEL_IMAGE="$(lock build devel_image)" --build-arg RUNTIME_IMAGE="$(lock build runtime_image)" \
  --build-arg ZIG_VERSION="$(lock build zig_version)" --build-arg ZIG_SHA256="$(lock build zig_sha256)" \
  --build-arg NCCL_REPOSITORY="$(lock build nccl_repository)" --build-arg NCCL_REVISION="$(lock build nccl_revision)" \
  --build-arg NCCL_VERSION="${nccl_release#v}" \
  --build-arg RUNTIME_PACKAGES="$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["build"]["runtime_packages"]))' "$here/image.lock.json")" \
  --build-arg TF_REPOSITORY="$(lock tensorfold repository)" --build-arg TF_REVISION="$(lock tensorfold revision)" \
  --build-arg TF_VERSION="$(lock tensorfold version)" --build-arg PATCH_HASH="$patch_hash" \
  "$ctx"

built=$(docker run --rm --entrypoint sha256sum "$image" /opt/tensorfold/bin/tensorfold-native | cut -d' ' -f1)
id=$(docker image inspect --format '{{.Id}}' "$image")
echo "Built $image: image ID $id, tensorfold-native sha256 $built"
if (( record )); then
  python3 - "$here/image.lock.json" "$built" "$id" <<'EOF'
import json, sys
path, built, image_id = sys.argv[1:]
lock = json.load(open(path))
lock["binary_sha256"], lock["image_id"] = built, image_id
open(path, "w").write(json.dumps(lock, indent=2) + "\n")
EOF
elif [[ "$built" != "$(lock binary_sha256)" ]]; then
  echo "tensorfold-native $built is not the lock's $(lock binary_sha256)" >&2
  exit 1
fi
