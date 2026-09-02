#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly ALLOY_MANIFEST="${1:-${ROOT_DIR}/kubernetes/apps/observability/alloy/app/helmrelease.yaml}"
# These are Alloy relabel capture references, not shell parameters.
# shellcheck disable=SC2016
readonly EXPECTED_REPLACEMENT='replacement="/var/log/pods/${1}_${2}_${3}/${4}/*.log"'
# Whitespace is removed before comparison so formatting changes do not weaken
# the assertions about rule contents and order.
# shellcheck disable=SC2016
readonly UID_RULE='rule{source_labels=["__meta_kubernetes_namespace","__meta_kubernetes_pod_name","__meta_kubernetes_pod_uid","__meta_kubernetes_pod_container_name",]separator="/"action="replace"regex="(.+)/(.+)/(.+)/(.+)"replacement="/var/log/pods/${1}_${2}_${3}/${4}/*.log"target_label="__path__"}'
# shellcheck disable=SC2016
readonly STATIC_RULE='rule{source_labels=["__meta_kubernetes_namespace","__meta_kubernetes_pod_name","__meta_kubernetes_pod_annotation_kubernetes_io_config_hash","__meta_kubernetes_pod_container_name",]separator="/"action="replace"regex="(.+)/(.+)/([0-9a-f]{32})/(.+)"replacement="/var/log/pods/${1}_${2}_${3}/${4}/*.log"target_label="__path__"}'

function count_occurrences() {
    local haystack="$1"
    local needle="$2"
    local count=0

    while [[ "${haystack}" == *"${needle}"* ]]; do
        haystack="${haystack#*"${needle}"}"
        count=$((count + 1))
    done

    printf '%d\n' "${count}"
}

function main() {
    local compact
    local config
    local expected_count
    local path_count
    local static_rule_count
    local target_count
    local uid_rule_count

    # Simulate the owning Flux Kustomization's post-build envsubst pass. The
    # source must escape relabel references as $${n}, but Alloy must receive
    # ${n}; otherwise Flux or Go regexp expansion silently empties the path.
    config="$(yq eval --unwrapScalar '.spec.values.alloy.configMap.content' \
        "${ALLOY_MANIFEST}" | flux envsubst)"
    compact="$(tr -d '[:space:]' <<<"${config}")"

    expected_count="$(count_occurrences "${compact}" "${EXPECTED_REPLACEMENT}")"
    path_count="$(count_occurrences "${compact}" 'replacement="/var/log/pods/')"
    target_count="$(count_occurrences "${compact}" 'target_label="__path__"')"
    uid_rule_count="$(count_occurrences "${compact}" "${UID_RULE}")"
    static_rule_count="$(count_occurrences "${compact}" "${STATIC_RULE}")"

    if [[ "${expected_count}" != 2 || "${path_count}" != 2 || "${target_count}" != 2 ]]; then
        echo "Expected exactly two exact Alloy pod-log path rules after Flux substitution; found ${expected_count} exact replacements, ${path_count} pod-path replacements, and ${target_count} path targets." >&2
        return 1
    fi

    if [[ "${uid_rule_count}" != 1 || "${static_rule_count}" != 1 ]]; then
        echo "Alloy pod-log discovery must contain one exact API-UID rule and one exact static config-hash rule." >&2
        return 1
    fi

    case "${compact}" in
        *"${UID_RULE}"*"${STATIC_RULE}"*) ;;
        *)
            echo "The static config-hash rule must follow the ordinary API-UID rule so it can override mirror-pod paths." >&2
            return 1
            ;;
    esac

    echo "Alloy pod-log paths retain exact relabel captures through Flux substitution."
}

main "$@"
