#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly TEMP_DIR="$(mktemp -d)"

function cleanup() {
    rm -rf -- "${TEMP_DIR}"
}

trap cleanup EXIT

# Two unrelated ceilings plus the kanidm one: the shape that broke the
# original file-wide grep/sed.
function write_fixture() {
    cat >"${TEMP_DIR}/.renovaterc.json5" <<'EOF'
{
  packageRules: [
    {
      description: "Valkey: cap the VSS cache sidecar at the 7.x series",
      matchDatasources: ["docker"],
      matchPackageNames: ["docker.io/valkey/valkey"],
      allowedVersions: "<8.0.0",
    },
    {
      description: "kanidm/server: cap at the minor supported by kaniop's SDK",
      matchDatasources: ["docker"],
      matchPackageNames: ["/kanidm/server$/"],
      allowedVersions: "<1.11.0",
    },
    {
      description: "Something else entirely",
      matchDatasources: ["docker"],
      matchPackageNames: ["docker.io/example/example"],
      allowedVersions: "<9.0.0",
    },
  ],
}
EOF
}

# A fixture lockfile keeps the test hermetic; SDK 1.11.1 implies a <1.12.0
# ceiling regardless of which kaniop tag the repo currently pins.
cat >"${TEMP_DIR}/Cargo.lock" <<'EOF'
[[package]]
name = "kanidm_client"
version = "1.11.1"

[[package]]
name = "unrelated"
version = "9.9.9"
EOF

function run_sync() {
    RENOVATERC_OVERRIDE="${TEMP_DIR}/.renovaterc.json5" \
        KANIOP_LOCKFILE_OVERRIDE="${TEMP_DIR}/Cargo.lock" \
        "${ROOT_DIR}/scripts/ci/sync-kanidm-version-ceiling.sh"
}

function ceiling_for() {
    local package="$1"
    awk -v pkg="${package}" '
        /^    \{$/ { block = ""; next }
        /^    \},?$/ {
            if (index(block, pkg)) {
                match(block, /allowedVersions: "[^"]*"/)
                v = substr(block, RSTART, RLENGTH)
                sub(/allowedVersions: "/, "", v)
                sub(/"$/, "", v)
                print v
                exit
            }
            next
        }
        { block = block $0 "\n" }
    ' "${TEMP_DIR}/.renovaterc.json5"
}

# The sync must succeed with unrelated ceilings present, and must move only
# the kanidm ceiling.
write_fixture
run_sync >/dev/null

kanidm_ceiling="$(ceiling_for 'kanidm/server')"
valkey_ceiling="$(ceiling_for 'valkey')"
other_ceiling="$(ceiling_for 'example/example')"

if [[ "${valkey_ceiling}" != "<8.0.0" ]]; then
    echo "Expected the Valkey ceiling to stay <8.0.0, got '${valkey_ceiling}'." >&2
    exit 1
fi

if [[ "${other_ceiling}" != "<9.0.0" ]]; then
    echo "Expected the unrelated ceiling to stay <9.0.0, got '${other_ceiling}'." >&2
    exit 1
fi

if [[ "${kanidm_ceiling}" != "<1.12.0" ]]; then
    echo "Expected the kanidm ceiling to become <1.12.0, got '${kanidm_ceiling}'." >&2
    exit 1
fi

# Idempotent: a second run over the synced file must not change it again.
before="$(cat "${TEMP_DIR}/.renovaterc.json5")"
run_sync >/dev/null
if [[ "${before}" != "$(cat "${TEMP_DIR}/.renovaterc.json5")" ]]; then
    echo 'Expected the sync to be idempotent.' >&2
    exit 1
fi

# A missing kanidm rule must fail loudly rather than rewrite a neighbour.
write_fixture
sed -i.bak 's|"/kanidm/server\$/"|"docker.io/unrelated/unrelated"|' "${TEMP_DIR}/.renovaterc.json5"
rm -f "${TEMP_DIR}/.renovaterc.json5.bak"
if run_sync >/dev/null 2>&1; then
    echo 'Expected the sync to fail when no kanidm/server rule is present.' >&2
    exit 1
fi

if [[ "$(ceiling_for 'valkey')" != "<8.0.0" ]]; then
    echo 'Expected a failed sync to leave the Valkey ceiling untouched.' >&2
    exit 1
fi

# An ambiguous match (two kanidm/server rules) must also fail loudly.
write_fixture
sed -i.bak 's|"docker.io/example/example"|"/kanidm/server\$/"|' "${TEMP_DIR}/.renovaterc.json5"
rm -f "${TEMP_DIR}/.renovaterc.json5.bak"
if run_sync >/dev/null 2>&1; then
    echo 'Expected the sync to fail when the kanidm/server rule is ambiguous.' >&2
    exit 1
fi

echo 'Kanidm ceiling sync regression tests passed.'
