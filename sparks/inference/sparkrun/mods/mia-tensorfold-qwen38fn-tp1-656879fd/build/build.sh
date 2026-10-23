#!/usr/bin/env bash
# build.sh: build the lock's image on any linux/arm64 Docker daemon (no GPU), never on a Spark: the engine and the kit at
# their pinned commits, the kit's patches by checksum and then this mod's. --record writes the image ID into the lock.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
mod=$(dirname "$here")
record=0
[[ "${1:-}" == --record ]] && record=1
lock() { python3 -c 'import functools,json,sys; print(functools.reduce(lambda v, k: v[k], sys.argv[2:], json.load(open(sys.argv[1]))))' "$mod/upstream.lock.json" "$@"; }
python3 "$mod/prepare.py" --check
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
fetch() { # fetch DIR REPOSITORY COMMIT
  git init -q "$work/$1" && git -C "$work/$1" remote add origin "https://github.com/$2.git"
  git -C "$work/$1" fetch -q --depth 1 origin "$3" && git -C "$work/$1" checkout -q --detach FETCH_HEAD
  test "$(git -C "$work/$1" rev-parse HEAD)" = "$3"
}
fetch tensorfold "$(lock engine_repository)" "$(lock engine_revision)"
fetch kit "$(lock kit_repository)" "$(lock kit_revision)"
test "$(cat "$work"/kit/patches/*.patch | shasum -a 256 | cut -d' ' -f1)" = "$(lock kit_patches_sha256)"
for patch in "$work"/kit/patches/*.patch "$mod"/patches/*.patch; do git -C "$work/tensorfold" apply "$patch"; done
image=$(lock image)
hash=$(cat "$work"/kit/patches/*.patch "$mod"/patches/*.patch | shasum -a 256 | cut -c1-12)
[[ "$image" == *"-$hash" ]] || { echo "image tag $image does not end in the patches' hash $hash" >&2; exit 1; }
rm -rf "$work/tensorfold/.git"
docker build --platform linux/arm64 -t "$image" -f "$here/Dockerfile" \
  --build-arg BASE_IMAGE="$(lock build base_image)" \
  --build-arg ZIG_VERSION="$(lock build zig_version)" --build-arg ZIG_SHA256="$(lock build zig_sha256)" \
  --build-arg ZIG_CPU="$(lock build zig_cpu)" --build-arg KERNEL_SOURCE_MTIME="$(lock build kernel_source_mtime)" \
  --build-arg PIP_PACKAGES="$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["build"]["pip_packages"]))' "$mod/upstream.lock.json")" \
  --build-arg KIT_REVISION="$(lock kit_revision)" --build-arg PATCHES_HASH="$hash" \
  "$work"
# The image config's digest: the ID a host's Docker reports after docker load, whichever store built it.
id=$(docker save "$image" | tar -xOf - manifest.json | python3 -c 'import json,sys; c=json.load(sys.stdin)[0]["Config"]; print("sha256:" + c.rsplit("/", 1)[-1].removesuffix(".json"))')
echo "Built $image: image ID $id"
if (( record )); then
  python3 - "$mod/upstream.lock.json" "$id" <<'EOF'
import json, sys
path, image_id = sys.argv[1:]
lock = json.load(open(path))
lock["image_id"] = image_id
open(path, "w").write(json.dumps(lock, indent=2) + "\n")
EOF
  python3 "$mod/prepare.py" --check
elif [[ "$id" != "$(lock image_id)" ]]; then
  echo "image ID $id is not the lock's $(lock image_id); a rebuild can differ, so record it with --record" >&2
  exit 1
fi
