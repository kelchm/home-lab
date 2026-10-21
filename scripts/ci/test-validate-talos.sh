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

printf 'machine: [\n' > "${TEST_DIR}/talos/patches/global/machine-install.yaml"
expect_failure 'malformed patch' 'yaml: line'

reset_fixture
yq -i '.filesystem.invalidField = true' "${TEST_DIR}/talos/patches/global/user-volume-longhorn.yaml"
expect_failure 'unknown UserVolumeConfig field' 'invalidField'

reset_fixture
yq -i '.machine.install.image = "factory.talos.dev/installer:v1.13.1"' "${TEST_DIR}/talos/patches/global/machine-install.yaml"
expect_failure 'Talos version drift' '.machine.install.image version v1.13.1 does not match talenv.yaml'

reset_fixture
yq -i '.machine.kubelet.image = "ghcr.io/siderolabs/kubelet:v1.36.1"' "${TEST_DIR}/talos/patches/global/machine-kubelet.yaml"
expect_failure 'kubelet version drift' '.machine.kubelet.image version v1.36.1 does not match talenv.yaml'

for component in KubeAPIServerConfig KubeControllerManagerConfig KubeSchedulerConfig; do
    reset_fixture
    COMPONENT="${component}" yq -i '(select(.kind == strenv(COMPONENT)) | .image) = "registry.k8s.io/component:v1.36.1"' "${TEST_DIR}/talos/patches/controller/cluster.yaml"
    expect_failure "${component} version drift" "${component}.image version v1.36.1 does not match talenv.yaml"
done

reset_fixture
yq -i '(select(.kind == "KubeAPIServerConfig") | .certExtraSANs) -= ["k8s-prod.home.kelch.io"]' "${TEST_DIR}/talos/patches/controller/cluster.yaml"
expect_failure 'API certificate SAN drift' 'missing API certificate SAN k8s-prod.home.kelch.io'

reset_fixture
yq -i '.patches -= ["@./patches/global/upgrade-compatibility.yaml"]' "${TEST_DIR}/talos/talconfig.yaml"
expect_failure 'unintended filesystem trim' 'trim and workload isolation must remain disabled'

reset_fixture
cat >> "${TEST_DIR}/talos/patches/controller/cluster.yaml" <<'PATCH'
---
apiVersion: v1alpha1
kind: KubeEtcdEncryptionConfig
config:
  resources:
    - resources: [secrets]
      providers:
        - secretbox:
            keys:
              - name: future-key
                secret: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
PATCH
expect_failure 'unexpected encryption key layout' 'unexpected encryption providers; refusing to rewrite them'

reset_fixture
cat >> "${TEST_DIR}/talos/patches/controller/cluster.yaml" <<'PATCH'
---
apiVersion: v1alpha1
kind: KubeEtcdEncryptionConfig
$$patch: delete
PATCH
expect_failure 'missing encryption document in renderer' 'etcd encryption config is required for control plane machines'

# The renderer already rejects a missing document. Also guard a direct helper
# invocation on incomplete input, independently of talhelper's validation.
printf 'machine:\n  type: controlplane\n' > "${TEST_DIR}/missing-encryption.yaml"
if "${SCRIPT_DIR}/../talos/preserve-encryption.sh" "${TEST_DIR}/missing-encryption.yaml" > "${TEST_DIR}/output.log" 2>&1; then
    echo 'FAIL: missing encryption document passed the preservation helper' >&2
    exit 1
fi
grep -Fq 'missing encryption document in control-plane config' "${TEST_DIR}/output.log"
echo 'ok: direct helper rejects a missing control-plane encryption document'
