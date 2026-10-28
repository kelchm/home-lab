#!/usr/bin/env bash
# Build a frozen DeepSeek mod without letting a rebuild replace its qualified tag on drift.
# The snapshot's build/build.sh predates this guard; keep its checksummed bytes intact.
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
mod=$(cd "${1:-$here/mods/bertholomus-tensorfold-dsv41-2970bcd-mia-fixes}" && pwd)
lock() { python3 -c 'import functools,json,sys; print(functools.reduce(lambda v, k: v[k], sys.argv[2:], json.load(open(sys.argv[1]))))' "$mod/upstream.lock.json" "$@"; }
python3 "$mod/prepare.py" --check
repo=https://github.com/$(lock engine_repository).git
image=$(lock image)
expected=$(lock image_id)
[[ "$expected" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Invalid qualified image ID in $mod/upstream.lock.json: $expected" >&2; exit 1; }
work=$(mktemp -d)
candidate="home-lab-deepseek-candidate:$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
cleanup() {
  docker image rm "$candidate" >/dev/null 2>&1 || echo "Could not remove temporary tag $candidate; remove it manually if it exists." >&2
  rm -rf -- "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
fetch() { # fetch DIR BRANCH COMMIT
  git clone -q --filter=blob:none --no-checkout --branch "$2" --single-branch "$repo" "$work/$1"
  git -C "$work/$1" checkout -q "$3"
}
fetch tf-zig "$(lock engine_branch)" "$(lock engine_revision)"
fetch tf-py "$(lock inputs_branch)" "$(lock inputs_revision)"
for patch in "$mod"/patches/*.patch; do git -C "$work/tf-zig" apply "$patch"; done
rm -rf "$work"/tf-zig/.git "$work"/tf-py/.git
cp "$mod/build/Dockerfile" "$work/Dockerfile"
if ! docker build -t "$candidate" "$work"; then
  echo "Candidate build failed; qualified tag $image was not changed." >&2
  exit 1
fi
if ! id=$(docker image inspect --format '{{.Id}}' "$candidate"); then
  echo "Cannot inspect candidate $candidate; qualified tag $image was not changed." >&2
  exit 1
fi
if [[ "$id" != "$expected" ]]; then
  cat >&2 <<EOF
DeepSeek rebuild refused: candidate $candidate
  expected: $expected
  actual:   $id
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
echo "Verified and tagged $image: $id"
