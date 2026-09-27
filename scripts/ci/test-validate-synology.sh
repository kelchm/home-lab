#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly TEMP_DIR="$(mktemp -d)"
readonly IMAGE='docker.io/library/busybox:1.38.0@sha256:fd7dc98638c8e305f4dc34e979f1c0fdfdcaeb0fbf8fcff77ae834b6da3d7e6e'

function cleanup() {
    rm -rf -- "${TEMP_DIR}"
}

trap cleanup EXIT

# Writes a declared project "app" whose publisher mounts a repository file.
# Arguments override the image, the publisher restart policy, and the target settings.
function write_fixture() {
    local image="$1"
    local publisher_restart="$2"
    local target_extra="$3"

    rm -rf -- "${TEMP_DIR}/synology"
    mkdir -p "${TEMP_DIR}/synology/app"
    {
        echo 'name: app'
        echo 'services:'
        echo '  app:'
        echo "    image: ${image}"
        echo '    volumes:'
        echo '      - /volume1/docker/app:/state'
        echo '    restart: unless-stopped'
        echo '  publish:'
        echo "    image: ${IMAGE}"
        echo '    volumes:'
        echo '      - ./file.txt:/source/file.txt:ro'
        echo '      - /volume1/docker/app:/state'
        echo "    restart: \"${publisher_restart}\""
    } >"${TEMP_DIR}/synology/app/compose.yaml"
    {
        echo 'name: app'
        echo 'working_dir: synology/app'
        if [[ -n "${target_extra}" ]]; then
            echo "${target_extra}"
        fi
    } >"${TEMP_DIR}/synology/.doco-cd.yml"
}

function expect_failure() {
    local description="$1"

    if "${ROOT_DIR}/scripts/ci/validate-synology.sh" "${TEMP_DIR}" 2>/dev/null; then
        echo "Expected Synology validation to fail: ${description}" >&2
        exit 1
    fi
}

write_fixture "${IMAGE}" no ''
"${ROOT_DIR}/scripts/ci/validate-synology.sh" "${TEMP_DIR}"

write_fixture docker.io/library/busybox:1.38.0 no ''
expect_failure 'tag-only image'

write_fixture "${IMAGE}" unless-stopped ''
expect_failure 'repository mount on a long-running service'

write_fixture "${IMAGE}" no 'destroy: true'
expect_failure 'destroy enabled'

write_fixture "${IMAGE}" no 'auto_discovery: true'
expect_failure 'auto-discovery enabled'

write_fixture "${IMAGE}" no ''
sed -i.bak 's|working_dir: synology/app|working_dir: synology/platform/app|' "${TEMP_DIR}/synology/.doco-cd.yml"
expect_failure 'platform directory declared as a target'

write_fixture "${IMAGE}" no ''
sed -i.bak 's|^name: app|name: other|' "${TEMP_DIR}/synology/.doco-cd.yml"
expect_failure 'target name differs from the Compose project name'

# Undeclared projects keep manual-apply semantics for repository-relative paths.
write_fixture "${IMAGE}" unless-stopped ''
rm "${TEMP_DIR}/synology/.doco-cd.yml"
"${ROOT_DIR}/scripts/ci/validate-synology.sh" "${TEMP_DIR}"

echo 'Synology validator regression tests passed.'
