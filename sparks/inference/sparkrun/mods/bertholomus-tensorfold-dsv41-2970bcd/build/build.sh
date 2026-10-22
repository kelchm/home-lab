#!/usr/bin/env bash
# build.sh: build the lock's image on any linux/arm64 Docker daemon (no GPU) from the pinned engine, inputs and patches.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
mod=$(dirname "$here")
lock() { python3 -c 'import functools,json,sys; print(functools.reduce(lambda v, k: v[k], sys.argv[2:], json.load(open(sys.argv[1]))))' "$mod/upstream.lock.json" "$@"; }
repo=https://github.com/$(lock engine_repository).git
image=$(lock image)
python3 "$mod/prepare.py" --check
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fetch() { # fetch DIR BRANCH COMMIT
  git clone -q --filter=blob:none --no-checkout --branch "$2" --single-branch "$repo" "$work/$1"
  git -C "$work/$1" checkout -q "$3"
}
fetch tf-zig "$(lock engine_branch)" "$(lock engine_revision)"
fetch tf-py "$(lock inputs_branch)" "$(lock inputs_revision)"
for patch in "$mod"/patches/*.patch; do git -C "$work/tf-zig" apply "$patch"; done
rm -rf "$work"/tf-zig/.git "$work"/tf-py/.git
cp "$here/Dockerfile" "$work/Dockerfile"
docker build -t "$image" "$work"
docker image inspect --format '{{.Id}}' "$image"
