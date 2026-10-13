#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly TEMP_DIR="$(mktemp -d)"

trap 'rm -rf -- "${TEMP_DIR}"' EXIT

# Self targets only: these tests render Compose without starting containers.
function write_fixture() {
    local platform
    rm -rf -- "${TEMP_DIR}/proxmox" "${TEMP_DIR}/sparks"
    for platform in proxmox sparks; do
        mkdir -p "${TEMP_DIR}/${platform}/monitoring" "${TEMP_DIR}/${platform}/platform/doco-cd"
        cp "${ROOT_DIR}/${platform}/platform/doco-cd/compose.yaml" "${TEMP_DIR}/${platform}/platform/doco-cd/"
    done
    printf 'name: doco-cd\nworking_dir: proxmox/platform/doco-cd\n' >"${TEMP_DIR}/proxmox/monitoring/.doco-cd.yml"
    printf 'name: doco-cd\nworking_dir: sparks/platform/doco-cd\nenvironment:\n  SPARK_HOST: spark-1\n' >"${TEMP_DIR}/sparks/monitoring/.doco-cd.spark-1.yml"
    printf 'name: doco-cd\nworking_dir: sparks/platform/doco-cd\nenvironment:\n  SPARK_HOST: spark-2\n' >"${TEMP_DIR}/sparks/monitoring/.doco-cd.spark-2.yml"
}

function expect_failure() {
    local description="$1" expected="$2" output
    if output="$("${ROOT_DIR}/scripts/ci/validate-host-monitoring.sh" "${TEMP_DIR}" 2>&1)"; then
        echo "Expected host validation to fail: ${description}" >&2
        exit 1
    fi
    if [[ "${output}" != *"${expected}"* ]]; then
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
        expect_failure "${platform}: ${mutation}" 'required binds, writable state'
    done
done

write_fixture
cp "${TEMP_DIR}/sparks/monitoring/.doco-cd.spark-2.yml" "${TEMP_DIR}/sparks/monitoring/.doco-cd.yml"
expect_failure 'shared Spark self target' 'never the shared target'

for mutation in host_network apparmor; do
    write_fixture
    compose="${TEMP_DIR}/proxmox/platform/doco-cd/compose.yaml"
    case "${mutation}" in
        host_network) yq -i '.services["doco-cd"].network_mode = "bridge"' "${compose}" ;;
        apparmor) yq -i '.services["doco-cd"].security_opt = []' "${compose}" ;;
    esac
    expect_failure "PVE security: ${mutation}" 'host networking and apparmor=unconfined'
done

for host in spark-1 spark-2; do
    write_fixture
    sed -i.bak '/SPARK_HOST:/d' "${TEMP_DIR}/sparks/monitoring/.doco-cd.${host}.yml"
    SPARK_HOST="${host}" expect_failure "${host}: missing declared identity despite ambient environment" 'Compose render failed'

    write_fixture
    sed -i.bak "s|SPARK_HOST: ${host}|SPARK_HOST: wrong-host|" "${TEMP_DIR}/sparks/monitoring/.doco-cd.${host}.yml"
    expect_failure "${host}: wrong identity" 'retain the shared poll and its own'
done

for mutation in missing_host_poll missing_shared_poll; do
    write_fixture
    case "${mutation}" in
        missing_host_poll) sed -i.bak '/target: /d' "${TEMP_DIR}/sparks/platform/doco-cd/compose.yaml" ;;
        missing_shared_poll) yq -i '.services["doco-cd"].environment.POLL_CONFIG |= (from_yaml | [.[1]] | to_yaml)' "${TEMP_DIR}/sparks/platform/doco-cd/compose.yaml" ;;
    esac
    expect_failure "Spark polls: ${mutation}" 'retain the shared poll and its own'
done

echo 'Host deployer regression tests passed.'
