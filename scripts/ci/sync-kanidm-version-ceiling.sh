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
readonly RENOVATERC="${RENOVATERC_OVERRIDE:-${ROOT_DIR}/.renovaterc.json5}"

# The rule is identified by the kanidm/server entry in its matchPackageNames.
# Other package rules carry their own unrelated ceilings, so both the guard and
# the rewrite below are scoped to this one block rather than the whole file.
readonly RULE_MARKER="kanidm/server"

# Walks .renovaterc.json5 one packageRule block at a time. In inspect mode
# (ceiling empty) it reports "<matching rules><TAB><ceilings in them><TAB><value>";
# otherwise it rewrites allowedVersions inside the matching block only and
# prints the file. Buffering the block is what keeps an unrelated ceiling
# elsewhere in the file from matching.
# shellcheck disable=SC2016 # awk program; values arrive via -v, not expansion
readonly RULE_AWK='
function process(  i) {
    if (has_marker) {
        matched++
        for (i = 1; i <= n; i++) {
            if (buf[i] ~ /allowedVersions: "[^"]*"/) {
                seen++
                current = buf[i]
                sub(/^.*allowedVersions: "/, "", current)
                sub(/".*$/, "", current)
                if (ceiling != "") {
                    sub(/allowedVersions: "[^"]*"/, \
                        "allowedVersions: \"" ceiling "\"", buf[i])
                }
            }
        }
    }
    if (ceiling != "") {
        for (i = 1; i <= n; i++) {
            print buf[i]
        }
    }
    n = 0
    has_marker = 0
}
/^    \{$/ {
    inrule = 1
    n = 0
    has_marker = 0
    inpackages = 0
    buf[++n] = $0
    next
}
inrule && /^    \},?$/ {
    buf[++n] = $0
    inrule = 0
    process()
    next
}
inrule {
    # Only matchPackageNames identifies the rule. A description or comment
    # mentioning kanidm/server must not make an unrelated rule a candidate.
    if (index($0, "matchPackageNames")) {
        inpackages = 1
    }
    if (inpackages && index($0, marker)) {
        has_marker = 1
    }
    if (inpackages && index($0, "]")) {
        inpackages = 0
    }
    buf[++n] = $0
    next
}
{
    if (ceiling != "") {
        print
    }
}
END {
    if (inrule) {
        process()
    }
    if (ceiling == "") {
        printf "%d\t%d\t%s\n", matched, seen, current
    }
}
'

kaniop_tag="$(awk '/^  ref:/{r=1;next} r&&/tag:/{print $2;exit}' "${OCIREPOSITORY}")"
[[ "${kaniop_tag}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
    echo "ERROR: unexpected kaniop tag '${kaniop_tag}' in ${OCIREPOSITORY}" >&2
    exit 1
}

lockfile_url="https://raw.githubusercontent.com/pando85/kaniop/v${kaniop_tag}/Cargo.lock"
lockfile="$(mktemp)"
trap 'rm -f -- "${lockfile}"' EXIT
# The regression test supplies a fixture here so it can run without network.
if [[ -n "${KANIOP_LOCKFILE_OVERRIDE:-}" ]]; then
    lockfile_url="${KANIOP_LOCKFILE_OVERRIDE}"
    cp -- "${KANIOP_LOCKFILE_OVERRIDE}" "${lockfile}"
else
    curl -fsSL -o "${lockfile}" "${lockfile_url}"
fi
sdk_version="$(awk '/^name = "kanidm_client"$/{f=1;next} f&&/^version = /{gsub(/"/,"",$3);print $3;exit}' "${lockfile}")"
[[ "${sdk_version}" =~ ^([0-9]+)\.([0-9]+)\.[0-9]+$ ]] || {
    echo "ERROR: could not extract kanidm_client version from ${lockfile_url} (got '${sdk_version}')" >&2
    exit 1
}

ceiling="<${BASH_REMATCH[1]}.$((BASH_REMATCH[2] + 1)).0"

IFS=$'\t' read -r rule_count ceiling_count current < <(
    awk -v marker="${RULE_MARKER}" -v ceiling="" "${RULE_AWK}" "${RENOVATERC}"
)

[[ "${rule_count}" -eq 1 ]] || {
    echo "ERROR: expected exactly 1 packageRule matching '${RULE_MARKER}' in ${RENOVATERC}, found ${rule_count}" >&2
    exit 1
}

[[ "${ceiling_count}" -eq 1 ]] || {
    echo "ERROR: expected exactly 1 allowedVersions ceiling in the '${RULE_MARKER}' rule, found ${ceiling_count}" >&2
    exit 1
}

echo "kaniop ${kaniop_tag} bundles kanidm_client ${sdk_version} -> ceiling ${ceiling} (current: ${current})"

if [[ "${current}" == "${ceiling}" ]]; then
    echo "in sync; nothing to do"
    exit 0
fi

rewritten="$(mktemp)"
trap 'rm -f -- "${lockfile}" "${rewritten}"' EXIT
awk -v marker="${RULE_MARKER}" -v ceiling="${ceiling}" "${RULE_AWK}" "${RENOVATERC}" >"${rewritten}"
cat "${rewritten}" >"${RENOVATERC}"
echo "updated ${RENOVATERC}: ${current} -> ${ceiling}"
