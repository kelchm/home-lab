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

function write_fixture() {
    local host_network="$1"

    mkdir -p "${TEMP_DIR}/manifests"
    {
        echo '---'
        echo 'apiVersion: v1'
        echo 'kind: Pod'
        echo 'metadata:'
        echo '  name: sysctl-test'
        echo 'spec:'
        echo "  hostNetwork: ${host_network}"
        echo '  securityContext:'
        echo '    sysctls:'
        echo '      - name: net.ipv4.ip_unprivileged_port_start'
        echo '        value: "0"'
        echo '  containers:'
        echo '    - name: pause'
        echo '      image: registry.k8s.io/pause:3.10.1'
    } >"${TEMP_DIR}/manifests/pod.yaml"
}

write_fixture false
{
    echo '---'
    echo 'apiVersion: v1'
    echo 'kind: Pod'
    echo 'metadata:'
    echo '  name: unrelated-host-network-pod'
    echo 'spec:'
    echo '  hostNetwork: true'
    echo '  containers:'
    echo '    - name: pause'
    echo '      image: registry.k8s.io/pause:3.10.1'
} >>"${TEMP_DIR}/manifests/pod.yaml"

# hostNetwork in a separate YAML document must not taint the first Pod.
"${ROOT_DIR}/scripts/ci/validate-unsafe-sysctls.sh" \
    "${TEMP_DIR}/manifests" \
    "${ROOT_DIR}/talos/patches/global/machine-kubelet.yaml"

write_fixture true
if "${ROOT_DIR}/scripts/ci/validate-unsafe-sysctls.sh" \
    "${TEMP_DIR}/manifests" \
    "${ROOT_DIR}/talos/patches/global/machine-kubelet.yaml"; then
    echo 'Expected hostNetwork plus net.* sysctl validation to fail.' >&2
    exit 1
fi

echo 'Unsafe sysctl validator regression tests passed.'
