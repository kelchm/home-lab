#!/usr/bin/env bash
# Build off the Sparks. --candidate preserves a new artifact for review; normal rebuilds must match the lock.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
mod=$(dirname "$here")
candidate_only=0
if [[ "${1:-}" == --candidate && $# == 1 ]]; then candidate_only=1
elif (( $# )); then echo "Usage: $0 [--candidate]" >&2; exit 2; fi
export DOCKER_CONTEXT=colima-tf-build
lock() { python3 -c 'import functools,json,sys; print(functools.reduce(lambda v,k: v[k], sys.argv[2:], json.load(open(sys.argv[1]))))' "$mod/upstream.lock.json" "$@"; }
python3 "$mod/prepare.py" --check
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
git init -q "$work/kit"
git -C "$work/kit" fetch -q --depth 1 "https://github.com/$(lock kit_repository).git" "$(lock kit_revision)"
git -C "$work/kit" checkout -q --detach FETCH_HEAD
test "$(git -C "$work/kit" rev-parse HEAD)" = "$(lock kit_revision)"
python3 - "$work/kit" "$mod/upstream.lock.json" <<'PY'
import hashlib, json, sys
from pathlib import Path
kit, lock = Path(sys.argv[1]), json.load(open(sys.argv[2]))
patches = sorted((kit / 'patches/engine').glob('*.patch'))
assert len(patches) == lock['kit_patch_count'], 'Kit patch count differs'
assert hashlib.sha256(b''.join(p.read_bytes() for p in patches)).hexdigest() == lock['kit_patches_sha256'], 'Kit patch bytes differ'
assert (kit / 'templates/glm-5.3-flash-vision.jinja').read_bytes() == (Path(sys.argv[2]).parent / 'chat_template.jinja').read_bytes(), 'Kit template differs'
PY
candidate="home-lab-glm53-candidate:$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
docker build --platform linux/arm64 -t "$candidate" -f "$here/Dockerfile" \
  --build-arg BASE_IMAGE="$(lock build base_image)" --build-arg RUNTIME_IMAGE="$(lock build runtime_image)" \
  --build-arg TF_REPO="https://github.com/$(lock engine_repository).git" --build-arg TF_REF="$(lock engine_revision)" \
  --build-arg ZIG_VERSION="$(lock build zig_version)" --build-arg ZIG_SHA256="$(lock build zig_sha256)" \
  --build-arg ZIG_CPU="$(lock build zig_cpu)" --build-arg ZIG_JOBS=8 --build-arg SM=121 \
  --build-arg PATCHES_HASH="$(lock kit_patches_sha256)" "$work/kit"
id=$(docker image inspect --format '{{.Id}}' "$candidate")
[[ "$id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Invalid daemon image ID: $id" >&2; exit 1; }
# Export the immutable daemon ID: containerd's .Id can be an index rather than the host's config digest.
config_id=$(docker save "$id" | tar -xOf - manifest.json | python3 -c '
import json,re,sys
m=json.load(sys.stdin)
assert len(m)==1, "Expected one exported image"
p=re.fullmatch(r"(?:blobs/sha256/)?([0-9a-f]{64})(?:\.json)?",m[0]["Config"])
assert p, "Invalid image config path"
print("sha256:"+p[1])')
echo "Candidate $candidate: config $config_id, daemon $id"
if (( candidate_only )); then exit 0; fi
if [[ "$config_id" != "$(lock image_id)" ]]; then
  echo "Rebuild differs from the lock; retained $candidate for review. Locked tag was not changed." >&2
  exit 1
fi
docker tag "$id" "$(lock image)"
docker image rm "$candidate" >/dev/null
echo "Verified $(lock image): $config_id"
