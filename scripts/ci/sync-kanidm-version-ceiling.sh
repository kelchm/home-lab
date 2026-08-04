#!/usr/bin/env bash
# Syncs the Renovate allowedVersions ceiling for kanidm/server from the
# kanidm_client SDK version resolved in the pinned kaniop release's Cargo.lock.
# Kaniop blocks kanidm server minors newer than its SDK minor, so the ceiling
# is "<major.(SDK minor + 1).0". Idempotent; exits 0 whether or not it rewrote.

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly OCIREPOSITORY="${ROOT_DIR}/kubernetes/apps/identity/kaniop/app/ocirepository.yaml"
readonly RENOVATERC="${ROOT_DIR}/.renovaterc.json5"

kaniop_tag="$(awk '/^  ref:/{r=1;next} r&&/tag:/{print $2;exit}' "${OCIREPOSITORY}")"
[[ "${kaniop_tag}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
    echo "ERROR: unexpected kaniop tag '${kaniop_tag}' in ${OCIREPOSITORY}" >&2
    exit 1
}

lockfile_url="https://raw.githubusercontent.com/pando85/kaniop/v${kaniop_tag}/Cargo.lock"
lockfile="$(mktemp)"
trap 'rm -f -- "${lockfile}"' EXIT
curl -fsSL -o "${lockfile}" "${lockfile_url}"
sdk_version="$(awk '/^name = "kanidm_client"$/{f=1;next} f&&/^version = /{gsub(/"/,"",$3);print $3;exit}' "${lockfile}")"
[[ "${sdk_version}" =~ ^([0-9]+)\.([0-9]+)\.[0-9]+$ ]] || {
    echo "ERROR: could not extract kanidm_client version from ${lockfile_url} (got '${sdk_version}')" >&2
    exit 1
}

ceiling="<${BASH_REMATCH[1]}.$((BASH_REMATCH[2] + 1)).0"

matches="$(grep -c 'allowedVersions: "<' "${RENOVATERC}")"
[[ "${matches}" -eq 1 ]] || {
    echo "ERROR: expected exactly 1 managed allowedVersions ceiling in ${RENOVATERC}, found ${matches}" >&2
    exit 1
}

current="$(sed -n 's/.*allowedVersions: "\(<[0-9.]*\)".*/\1/p' "${RENOVATERC}")"
echo "kaniop ${kaniop_tag} bundles kanidm_client ${sdk_version} -> ceiling ${ceiling} (current: ${current})"

if [[ "${current}" == "${ceiling}" ]]; then
    echo "in sync; nothing to do"
    exit 0
fi

sed -i.bak "s|allowedVersions: \"<[0-9.]*\"|allowedVersions: \"${ceiling}\"|" "${RENOVATERC}"
rm -f "${RENOVATERC}.bak"
echo "updated ${RENOVATERC}: ${current} -> ${ceiling}"
