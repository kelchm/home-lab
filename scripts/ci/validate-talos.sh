#!/usr/bin/env bash
# Render the repository inputs without decrypting production secrets or contacting nodes.

set -o errexit
set -o nounset
set -o pipefail

readonly TALOS_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../talos" && pwd)}"

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

readonly TALOS_VERSION="$(yq --exit-status '.talosVersion' talenv.yaml)"
readonly KUBERNETES_VERSION="$(yq --exit-status '.kubernetesVersion' talenv.yaml)"
readonly EXPECTED_NODES="$(yq --exit-status '.nodes | length' talconfig.yaml)"

function check_image_version() {
    local config="$1" field="$2" expected="$3" image tag
    image="$(yq --exit-status "select(has(\"machine\")) | ${field}" "${config}")"
    # A digest can follow the tag; an image with only a digest has no matching version.
    tag="${image%@*}"
    tag="${tag##*:}"
    if [[ "${tag}" != "${expected}" ]]; then
        echo "${config##*/}: ${field} version ${tag} does not match talenv.yaml (${expected})" >&2
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
    check_image_version "${config}" '.machine.install.image' "${TALOS_VERSION}"
    check_image_version "${config}" '.machine.kubelet.image' "${KUBERNETES_VERSION}"
    if [[ "$(yq --exit-status 'select(has("machine")) | .machine.type' "${config}")" == controlplane ]]; then
        for component in apiServer controllerManager scheduler; do
            check_image_version "${config}" ".cluster.${component}.image" "${KUBERNETES_VERSION}"
        done
    fi
done

echo "Validated ${#node_configs[@]} Talos node configs and their Talos/Kubernetes versions using ephemeral secrets."
