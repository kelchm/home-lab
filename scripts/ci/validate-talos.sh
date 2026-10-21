#!/usr/bin/env bash
# Render the repository inputs without decrypting production secrets or contacting nodes.

set -o errexit
set -o nounset
set -o pipefail

readonly TALOS_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../talos" && pwd)}"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

umask 077
readonly FIXTURE_DIR="$(mktemp -d)"
trap 'rm -rf "${FIXTURE_DIR}"' EXIT

# Explicit input files replace talhelper's defaults, including *.sops.yaml.
cd "${TALOS_DIR}"
talhelper validate talconfig talconfig.yaml --env-file talenv.yaml
talhelper gensecret > "${FIXTURE_DIR}/talsecret.yaml"
talhelper genconfig \
    --config-file talconfig.yaml \
    --env-file talenv.yaml \
    --secret-file "${FIXTURE_DIR}/talsecret.yaml" \
    --out-dir "${FIXTURE_DIR}/clusterconfig" \
    --offline-mode
"${SCRIPT_DIR}/../talos/preserve-encryption.sh" "${FIXTURE_DIR}"/clusterconfig/*.yaml

readonly TALOS_VERSION="$(yq --exit-status '.talosVersion' talenv.yaml)"
readonly KUBERNETES_VERSION="$(yq --exit-status '.kubernetesVersion' talenv.yaml)"
readonly EXPECTED_NODES="$(yq --exit-status '.nodes | length' talconfig.yaml)"

function check_image_version() {
    local config="$1" field="$2" expected="$3" label="${4:-$2}" image tag
    image="$(yq --exit-status "${field}" "${config}")"
    # A digest can follow the tag; an image with only a digest has no matching version.
    tag="${image%@*}"
    tag="${tag##*:}"
    if [[ "${tag}" != "${expected}" ]]; then
        echo "${config##*/}: ${label} version ${tag} does not match talenv.yaml (${expected})" >&2
        return 1
    fi
}

shopt -s nullglob
node_configs=("${FIXTURE_DIR}"/clusterconfig/*.yaml)
if ((EXPECTED_NODES == 0 || ${#node_configs[@]} != EXPECTED_NODES)); then
    echo "Expected ${EXPECTED_NODES} node configs, rendered ${#node_configs[@]}" >&2
    exit 1
fi

for config in "${node_configs[@]}"; do
    talosctl validate --config "${config}" --mode metal
    check_image_version "${config}" 'select(has("machine")) | .machine.install.image' "${TALOS_VERSION}" '.machine.install.image'
    check_image_version "${config}" 'select(has("machine")) | .machine.kubelet.image' "${KUBERNETES_VERSION}" '.machine.kubelet.image'
    if [[ "$(yq --exit-status 'select(has("machine")) | .machine.type' "${config}")" == controlplane ]]; then
        for component in KubeAPIServerConfig KubeControllerManagerConfig KubeSchedulerConfig; do
            check_image_version "${config}" "select(.kind == \"${component}\") | .image" "${KUBERNETES_VERSION}" "${component}.image"
        done
        if ! yq -o=json 'select(.kind == "KubeEtcdEncryptionConfig")' "${config}" | jq --exit-status '.config.resources |
            length == 1 and .[0].resources == ["secrets"] and
            (.[0].providers | length == 2) and
            (.[0].providers[0].secretbox.keys | length == 1) and
            .[0].providers[0].secretbox.keys[0].name == "key2" and
            .[0].providers[1] == {"identity": {}}' >/dev/null; then
            echo "${config##*/}: encryption must preserve secretbox key2 and the identity fallback" >&2
            exit 1
        fi
        if [[ "$(yq --exit-status '.secrets.secretboxencryptionsecret' "${FIXTURE_DIR}/talsecret.yaml")" != \
            "$(yq --exit-status 'select(.kind == "KubeEtcdEncryptionConfig") | .config.resources[0].providers[0].secretbox.keys[0].secret' "${config}")" ]]; then
            echo "${config##*/}: encryption key material changed during rendering" >&2
            exit 1
        fi
        for san in 127.0.0.1 10.32.30.8 k8s-prod.home.kelch.io; do
            if ! SAN="${san}" yq --exit-status 'select(.kind == "KubeAPIServerConfig") | .certExtraSANs[] | select(. == strenv(SAN))' "${config}" >/dev/null; then
                echo "${config##*/}: missing API certificate SAN ${san}" >&2
                exit 1
            fi
        done
    fi
    forbidden_kinds="$(yq 'select(.kind == "FilesystemTrimConfig" or .kind == "SecurityProfileConfig") | .kind' "${config}")"
    if [[ -n "${forbidden_kinds}" ]]; then
        echo "${config##*/}: trim and workload isolation must remain disabled for this rollout" >&2
        exit 1
    fi
done

echo "Validated ${#node_configs[@]} Talos node configs and their Talos/Kubernetes versions using ephemeral secrets."
