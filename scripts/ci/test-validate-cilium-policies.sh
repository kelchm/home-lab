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

mkdir -p "${TEMP_DIR}/invalid" "${TEMP_DIR}/valid"

{
    echo '---'
    echo 'apiVersion: cilium.io/v2'
    echo 'kind: CiliumNetworkPolicy'
    echo 'metadata:'
    echo '  name: empty-policy'
    echo 'spec:'
    echo '  endpointSelector: {}'
    echo '  ingress: []'
    echo '  egress: []'
} >"${TEMP_DIR}/invalid/networkpolicy.yaml"

if "${ROOT_DIR}/scripts/ci/validate-cilium-policies.sh" "${TEMP_DIR}/invalid"; then
    echo 'Expected an empty Cilium policy to fail validation.' >&2
    exit 1
fi

{
    echo '---'
    echo 'apiVersion: cilium.io/v2'
    echo 'kind: CiliumNetworkPolicy'
    echo 'metadata:'
    echo '  name: default-deny'
    echo 'spec:'
    echo '  endpointSelector: {}'
    echo '  ingress:'
    echo '    - {}'
    echo '  egress:'
    echo '    - {}'
    echo '---'
    echo 'apiVersion: cilium.io/v2'
    echo 'kind: CiliumNetworkPolicy'
    echo 'metadata:'
    echo '  name: egress-only'
    echo 'spec:'
    echo '  endpointSelector: {}'
    echo '  ingress: []'
    echo '  egress:'
    echo '    - toEntities:'
    echo '        - kube-apiserver'
} >"${TEMP_DIR}/valid/networkpolicy.yaml"

"${ROOT_DIR}/scripts/ci/validate-cilium-policies.sh" "${TEMP_DIR}/valid"

echo 'Cilium policy validator regression tests passed.'
