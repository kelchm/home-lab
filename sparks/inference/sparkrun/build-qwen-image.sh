#!/usr/bin/env bash
# Rebuild the frozen TP1 mod without replacing its qualified tag until verification succeeds.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
if [[ "${1:-}" == --help ]]; then
  echo "Usage: $0 [MOD_DIRECTORY]"
  exit 0
fi
if (( $# > 1 )) || [[ "${1:-}" == -* ]]; then
  echo "Usage: $0 [MOD_DIRECTORY] (no --record or image-ID override)" >&2
  exit 2
fi
mod=$(cd "${1:-$here/mods/mia-tensorfold-qwen38fn-tp1-656879fd}" && pwd)
lock() { python3 -c 'import functools,json,sys; print(functools.reduce(lambda v, k: v[k], sys.argv[2:], json.load(open(sys.argv[1]))))' "$mod/upstream.lock.json" "$@"; }
python3 "$mod/prepare.py" --check
expected=$(lock image_id)
[[ "$expected" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Invalid qualified image ID: $expected" >&2; exit 1; }
work=$(mktemp -d)
candidate="home-lab-qwen-candidate:$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
cleanup() {
  docker image rm "$candidate" >/dev/null 2>&1 || echo "Could not remove temporary tag $candidate; remove it manually if it exists." >&2
  rm -rf -- "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
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
docker build --platform linux/arm64 -t "$candidate" -f "$mod/build/Dockerfile" \
  --build-arg BASE_IMAGE="$(lock build base_image)" \
  --build-arg ZIG_VERSION="$(lock build zig_version)" --build-arg ZIG_SHA256="$(lock build zig_sha256)" \
  --build-arg ZIG_CPU="$(lock build zig_cpu)" --build-arg KERNEL_SOURCE_MTIME="$(lock build kernel_source_mtime)" \
  --build-arg PIP_PACKAGES="$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["build"]["pip_packages"]))' "$mod/upstream.lock.json")" \
  --build-arg KIT_REVISION="$(lock kit_revision)" --build-arg PATCHES_HASH="$hash" \
  "$work"
if ! id=$(docker image inspect --format '{{.Id}}' "$candidate"); then
  echo "Cannot inspect candidate $candidate; qualified tag $image was not changed." >&2
  exit 1
fi
[[ "$id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Invalid daemon image ID for candidate $candidate: $id" >&2; exit 1; }
# Containerd stores can report an index/manifest ID. The lock records the config
# digest seen after docker load on the Sparks. Export this exact immutable ID,
# never the candidate tag, so a concurrent retag cannot change what is checked.
if ! config_id=$(docker image save "$id" | tar -xOf - manifest.json | python3 -c '
import json,re,sys
manifest=json.load(sys.stdin)
if not isinstance(manifest,list) or len(manifest)!=1:
    raise SystemExit("Expected one image in the Docker export manifest")
config=manifest[0]["Config"]
match=re.fullmatch(r"(?:blobs/sha256/)?([0-9a-f]{64})(?:\.json)?",config)
if not match:
    raise SystemExit("Invalid config digest path in the Docker export manifest")
print("sha256:"+match[1])
'); then
  echo "Cannot read config digest for candidate $id; qualified tag $image was not changed." >&2
  exit 1
fi
if [[ "$config_id" != "$expected" ]]; then
  cat >&2 <<EOF
Qwen rebuild refused: candidate $candidate
  expected: $expected
  actual:   $config_id
  daemon ID: $id
Qualified tag $image was not changed.
Different content requires a new versioned mod and profile, a new image tag and an updated lock, followed by qualification. Do not rewrite the existing profile's image ID or frozen checksums to accept this rebuild.
EOF
  exit 1
fi
# Use the accepted immutable ID, so even a later change to the candidate tag cannot publish different content.
if ! docker image tag "$id" "$image"; then
  echo "Candidate $id matched, but publishing qualified tag $image failed." >&2
  exit 1
fi
echo "Verified and tagged $image: config $config_id (daemon ID $id)"
