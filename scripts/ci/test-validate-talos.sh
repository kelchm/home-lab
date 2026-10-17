#!/usr/bin/env bash
# Exercise the real pinned renderer/validator and verify representative regressions fail.

set -o errexit
set -o nounset
set -o pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly TALOS_DIR="${SCRIPT_DIR}/../../talos"

umask 077
readonly TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

"${SCRIPT_DIR}/validate-talos.sh" "${TALOS_DIR}"

function reset_fixture() {
    rm -rf "${TEST_DIR}/talos"
    mkdir "${TEST_DIR}/talos"
    cp "${TALOS_DIR}/talconfig.yaml" "${TALOS_DIR}/talenv.yaml" "${TEST_DIR}/talos/"
    cp -R "${TALOS_DIR}/patches" "${TEST_DIR}/talos/patches"
    # These default inputs must never be consulted by the CI render.
    printf 'not a usable secret or env file: [\n' > "${TEST_DIR}/talos/talsecret.sops.yaml"
    cp "${TEST_DIR}/talos/talsecret.sops.yaml" "${TEST_DIR}/talos/talenv.sops.yaml"
}

function expect_failure() {
    local name="$1" diagnostic="$2"
    if "${SCRIPT_DIR}/validate-talos.sh" "${TEST_DIR}/talos" > "${TEST_DIR}/output.log" 2>&1; then
        echo "FAIL: ${name} unexpectedly passed" >&2
        return 1
    fi
    if ! grep -Fq "${diagnostic}" "${TEST_DIR}/output.log"; then
        echo "FAIL: ${name} failed for an unexpected reason" >&2
        cat "${TEST_DIR}/output.log" >&2
        return 1
    fi
    echo "ok: ${name} rejected (${diagnostic})"
}

reset_fixture
# First prove that unusable SOPS defaults do not affect rendering.
"${SCRIPT_DIR}/validate-talos.sh" "${TEST_DIR}/talos" > "${TEST_DIR}/output.log" 2>&1
echo "ok: SOPS default inputs ignored"

printf 'machine: [\n' > "${TEST_DIR}/talos/patches/global/machine-files.yaml"
expect_failure 'malformed patch' 'yaml: line'

reset_fixture
yq -i '.filesystem.invalidField = true' "${TEST_DIR}/talos/patches/global/user-volume-longhorn.yaml"
expect_failure 'unknown UserVolumeConfig field' 'invalidField'

reset_fixture
yq -i '.machine.install.image = "factory.talos.dev/installer:v1.13.1"' "${TEST_DIR}/talos/patches/global/machine-network.yaml"
expect_failure 'Talos version drift' '.machine.install.image version v1.13.1 does not match talenv.yaml'

reset_fixture
yq -i '.machine.kubelet.image = "ghcr.io/siderolabs/kubelet:v1.36.1"' "${TEST_DIR}/talos/patches/global/machine-kubelet.yaml"
expect_failure 'kubelet version drift' '.machine.kubelet.image version v1.36.1 does not match talenv.yaml'

reset_fixture
yq -i '.cluster.apiServer.image = "registry.k8s.io/kube-apiserver:v1.36.1"' "${TEST_DIR}/talos/patches/controller/cluster.yaml"
expect_failure 'control-plane version drift' '.cluster.apiServer.image version v1.36.1 does not match talenv.yaml'
