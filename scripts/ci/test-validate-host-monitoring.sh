#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly TEMP_DIR="$(mktemp -d)"

trap 'rm -rf -- "${TEMP_DIR}"' EXIT

# Self targets only: these tests render Compose without starting containers.
function write_fixture() {
    rm -rf -- "${TEMP_DIR}/proxmox" "${TEMP_DIR}/sparks"
    for platform in proxmox sparks; do
        mkdir -p "${TEMP_DIR}/${platform}/monitoring" "${TEMP_DIR}/${platform}/platform/doco-cd"
        cp "${ROOT_DIR}/${platform}/platform/doco-cd/compose.yaml" "${TEMP_DIR}/${platform}/platform/doco-cd/"
    done
    cp "${ROOT_DIR}/sparks/platform/doco-cd/compose.spark-1.yaml" "${TEMP_DIR}/sparks/platform/doco-cd/"
    printf 'name: doco-cd\nworking_dir: proxmox/platform/doco-cd\n' >"${TEMP_DIR}/proxmox/monitoring/.doco-cd.yml"
    printf 'name: doco-cd\nworking_dir: sparks/platform/doco-cd\ncompose_files: [compose.yaml, compose.spark-1.yaml]\n' >"${TEMP_DIR}/sparks/monitoring/.doco-cd.spark-1.yml"
    printf 'name: doco-cd\nworking_dir: sparks/platform/doco-cd\n' >"${TEMP_DIR}/sparks/monitoring/.doco-cd.spark-2.yml"
}

function expect_bind_failure() {
    local description="$1" output
    if output="$("${ROOT_DIR}/scripts/ci/validate-host-monitoring.sh" "${TEMP_DIR}" 2>&1)"; then
        echo "Expected host validation to fail: ${description}" >&2
        exit 1
    fi
    if [[ "${output}" != *"required binds, writable state"* ]]; then
        echo "Unexpected validation failure for ${description}: ${output}" >&2
        exit 1
    fi
}

write_fixture
"${ROOT_DIR}/scripts/ci/validate-host-monitoring.sh" "${TEMP_DIR}" >/dev/null

for platform in proxmox sparks; do
    for mutation in missing_socket missing_data wrong_source wrong_target read_only; do
        write_fixture
        compose="${TEMP_DIR}/${platform}/platform/doco-cd/compose.yaml"
        case "${mutation}" in
            missing_socket) sed -i.bak '\|/var/run/docker.sock:/var/run/docker.sock|d' "${compose}" ;;
            missing_data) sed -i.bak '\|/var/lib/doco-cd:/data|d' "${compose}" ;;
            wrong_source) sed -i.bak 's|/var/lib/doco-cd:/data|/var/lib/other:/data|' "${compose}" ;;
            wrong_target) sed -i.bak 's|/var/lib/doco-cd:/data|/var/lib/doco-cd:/other|' "${compose}" ;;
            read_only) sed -i.bak 's|/var/lib/doco-cd:/data|/var/lib/doco-cd:/data:ro|' "${compose}" ;;
        esac
        expect_bind_failure "${platform}: ${mutation}"
    done
done

echo 'Host deployer bind regression tests passed.'
