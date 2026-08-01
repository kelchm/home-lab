#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly MANIFEST_DIR="${1:-${ROOT_DIR}/kubernetes}"
readonly KUBELET_PATCH="${2:-${ROOT_DIR}/talos/patches/global/machine-kubelet.yaml}"

readonly SAFE_SYSCTLS=(
    kernel.shm_rmid_forced
    net.ipv4.ip_local_port_range
    net.ipv4.ip_local_reserved_ports
    net.ipv4.ip_unprivileged_port_start
    net.ipv4.ping_group_range
    net.ipv4.tcp_fin_timeout
    net.ipv4.tcp_keepalive_intvl
    net.ipv4.tcp_keepalive_probes
    net.ipv4.tcp_keepalive_time
    net.ipv4.tcp_rmem
    net.ipv4.tcp_syncookies
    net.ipv4.tcp_wmem
)

function normalize_sysctl() {
    tr '/' '.' <<<"$1"
}

function is_safe_sysctl() {
    local requested="$1"
    local safe

    for safe in "${SAFE_SYSCTLS[@]}"; do
        if [[ "${requested}" == "${safe}" ]]; then
            return 0
        fi
    done

    return 1
}

function is_allowed_unsafe_sysctl() {
    local requested="$1"
    local allowed

    while IFS= read -r allowed; do
        [[ -n "${allowed}" ]] || continue
        allowed="$(normalize_sysctl "${allowed}")"

        if [[ "${allowed}" == "*" ]]; then
            return 0
        fi

        if [[ "${allowed}" == *'*' ]]; then
            if [[ "${requested}" == "${allowed%\*}"* ]]; then
                return 0
            fi
        elif [[ "${requested}" == "${allowed}" ]]; then
            return 0
        fi
    done < <(yq eval --unwrapScalar \
        '.machine.kubelet.extraConfig.allowedUnsafeSysctls[]?' \
        "${KUBELET_PATCH}")

    return 1
}

function main() {
    local failed=0
    local file
    local host_network
    local requested

    while IFS= read -r -d '' file; do
        # $document and $name below are yq variables, not shell variables.
        # shellcheck disable=SC2016
        while IFS=$'\t' read -r requested host_network; do
            [[ -n "${requested}" ]] || continue
            [[ "${host_network}" == true || "${host_network}" == false ]] || continue
            requested="$(normalize_sysctl "${requested}")"

            if [[ "${host_network}" == true && "${requested}" == net.* ]]; then
                printf 'Network sysctl %q in %s is forbidden when hostNetwork is enabled\n' \
                    "${requested}" "${file#"${ROOT_DIR}"/}" >&2
                failed=1
                continue
            fi

            if is_safe_sysctl "${requested}" || is_allowed_unsafe_sysctl "${requested}"; then
                continue
            fi

            printf 'Unsafe sysctl %q in %s is not allowed by %s\n' \
                "${requested}" "${file#"${ROOT_DIR}"/}" "${KUBELET_PATCH#"${ROOT_DIR}"/}" >&2
            failed=1
        done < <(yq eval --unwrapScalar \
            '. as $document |
             ($document | .. | select(tag == "!!map" and has("sysctls")) |
                .sysctls[]? | .name // "") as $name |
             [$name, ([$document | .. |
                select(tag == "!!map" and .hostNetwork == true)] | length > 0)] |
             @tsv' "${file}")
    done < <(find "${MANIFEST_DIR}" -type f \( -name '*.yaml' -o -name '*.yml' \) -print0)

    if ((failed != 0)); then
        return 1
    fi

    echo "All requested pod sysctls are compatible with pod networking and the Talos kubelet allowlist."
}

main "$@"
