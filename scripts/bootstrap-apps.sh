#!/usr/bin/env bash
set -Eeuo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

export LOG_LEVEL="debug"
export ROOT_DIR="$(git rev-parse --show-toplevel)"

# Reads `kubectl get nodes --output=json` on stdin and prints a summary.
# Succeeds once at least the expected number of nodes are registered and every
# registered node reports Ready=True or Ready=False; before the CNI exists,
# Talos nodes report Ready=False.
function node_readiness() {
    local -r expected="$1"
    local nodes
    nodes=$(cat)
    jq -r --argjson expected "${expected}" '
        [.items[] | ([.status.conditions[]? | select(.type == "Ready") | .status][0] // "Unknown")] as $ready
        | "registered=\($ready | length)/\($expected) ready=\([$ready[] | select(. == "True")] | length) not_ready=\([$ready[] | select(. == "False")] | length) unknown=\([$ready[] | select(. != "True" and . != "False")] | length)"
    ' <<<"${nodes}"
    jq -e --argjson expected "${expected}" '
        [.items[] | ([.status.conditions[]? | select(.type == "Ready") | .status][0] // "Unknown")]
        | length >= $expected and all(. == "True" or . == "False")
    ' <<<"${nodes}" >/dev/null
}

# Waits for every node in talconfig.yaml to register. BOOTSTRAP_NODE_COUNT
# lowers the count when bootstrapping with nodes deliberately offline.
function wait_for_nodes() {
    local -r expected="${BOOTSTRAP_NODE_COUNT:-$(yq '.nodes | length' "${ROOT_DIR}/talos/talconfig.yaml")}"
    local -r timeout="${BOOTSTRAP_NODE_TIMEOUT:-600}"
    local -r deadline=$((SECONDS + timeout))
    local summary

    log debug "Waiting for nodes to register" "expected=${expected}" "timeout=${timeout}s"
    while true; do
        if summary=$(kubectl get nodes --output=json 2>/dev/null | node_readiness "${expected}"); then
            log info "Nodes are registered" "${summary}"
            return
        fi
        if ((SECONDS >= deadline)); then
            log error "Nodes did not register before the timeout" "${summary:-api=unreachable}" "timeout=${timeout}s"
        fi
        log info "Waiting for nodes to register; retrying in 10 seconds" "${summary:-api=unreachable}"
        sleep 10
    done
}

# Namespaces to be applied before the SOPS secrets are installed
function apply_namespaces() {
    log debug "Applying namespaces"

    local -r apps_dir="${ROOT_DIR}/kubernetes/apps"

    if [[ ! -d "${apps_dir}" ]]; then
        log error "Directory does not exist" "directory=${apps_dir}"
    fi

    for app in "${apps_dir}"/*/; do
        namespace=$(basename "${app}")

        # Check if the namespace resources are up-to-date
        if kubectl get namespace "${namespace}" &>/dev/null; then
            log info "Namespace resource is up-to-date" "resource=${namespace}"
            continue
        fi

        # Apply the namespace resources
        if kubectl create namespace "${namespace}" --dry-run=client --output=yaml \
            | kubectl apply --server-side --filename - &>/dev/null;
        then
            log info "Namespace resource applied" "resource=${namespace}"
        else
            log error "Failed to apply namespace resource" "resource=${namespace}"
        fi
    done
}

# SOPS secrets to be applied before the helmfile charts are installed
function apply_sops_secrets() {
    log debug "Applying secrets"

    local -r secrets=(
        "${ROOT_DIR}/bootstrap/sops-age.sops.yaml"
    )

    for secret in "${secrets[@]}"; do
        if [ ! -f "${secret}" ]; then
            log error "File does not exist" "file=${secret}"
        fi

        # Check if the secret resources are up-to-date
        if sops exec-file "${secret}" "kubectl --namespace flux-system diff --filename {}" &>/dev/null; then
            log info "Secret resource is up-to-date" "resource=$(basename "${secret}" ".sops.yaml")"
            continue
        fi

        # Apply secret resources
        if sops exec-file "${secret}" "kubectl --namespace flux-system apply --server-side --filename {}" &>/dev/null; then
            log info "Secret resource applied successfully" "resource=$(basename "${secret}" ".sops.yaml")"
        else
            log error "Failed to apply secret resource" "resource=$(basename "${secret}" ".sops.yaml")"
        fi
    done
}

# CRDs to be applied before the helmfile charts are installed
function apply_crds() {
    log debug "Applying CRDs"

    local -r helmfile_file="${ROOT_DIR}/bootstrap/helmfile.d/00-crds.yaml"

    if [[ ! -f "${helmfile_file}" ]]; then
        log error "File does not exist" "file=${helmfile_file}"
    fi

    if ! crds=$(helmfile --file "${helmfile_file}" template --quiet | yq eval-all --exit-status 'select(.kind == "CustomResourceDefinition")') || [[ -z "${crds}" ]]; then
        log error "Failed to render CRDs from Helmfile" "file=${helmfile_file}"
    fi

    if echo "${crds}" | kubectl diff --filename - &>/dev/null; then
        log info "CRDs are up-to-date"
        return
    fi

    if ! echo "${crds}" | kubectl apply --server-side --filename - &>/dev/null; then
        log error "Failed to apply CRDs from Helmfile" "file=${helmfile_file}"
    fi

    log info "CRDs applied successfully"
}

# Sync Helm releases
function sync_helm_releases() {
    log debug "Syncing Helm releases"

    local -r helmfile_file="${ROOT_DIR}/bootstrap/helmfile.d/01-apps.yaml"

    if [[ ! -f "${helmfile_file}" ]]; then
        log error "File does not exist" "file=${helmfile_file}"
    fi

    if ! helmfile --file "${helmfile_file}" sync --hide-notes; then
        log error "Failed to sync Helm releases"
    fi

    log info "Helm releases synced successfully"
}

function main() {
    check_env KUBECONFIG TALOSCONFIG
    check_cli helmfile jq kubectl kustomize sops talhelper yq

    # Apply resources and Helm releases
    wait_for_nodes
    apply_namespaces
    apply_sops_secrets
    apply_crds
    sync_helm_releases

    log info "Congrats! The cluster is bootstrapped and Flux is syncing the Git repository"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
