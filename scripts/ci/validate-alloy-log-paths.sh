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
readonly EXPECTED_VL_URL='url="http://victoria-logs-single-server.observability.svc.cluster.local:9428/insert/loki/api/v1/push?message_fields_prefix=msg.&_msg_field=msg.message,msg.msg,msg.log,msg.event,msg.record.message&_stream_fields=cluster,namespace,pod,container,node"'
readonly EXPECTED_EXTERNAL_LABELS='external_labels={cluster="k8s-prod",}'

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
    local value

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

    if [[ "$(count_occurrences "${compact}" "${EXPECTED_VL_URL}")" != 1 || \
          "$(count_occurrences "${compact}" "${EXPECTED_EXTERNAL_LABELS}")" != 1 || \
          "$(count_occurrences "${compact}" 'max_backoff_retries=15')" != 1 ]]; then
        echo "Alloy must use the reviewed VictoriaLogs field mapping, cluster label, and bounded retry count." >&2
        return 1
    fi

    if [[ "$(count_occurrences "${compact}" 'on_positions_file_error="restart_from_end"')" != 1 || \
          "${compact}" == *'tail_from_end='* ]]; then
        echo "Alloy must resume valid positions, skip replay after position corruption, and retain the default behavior for newly discovered files." >&2
        return 1
    fi

    value="$(yq eval --unwrapScalar '.spec.values.alloy.mounts.varlog // false' "${ALLOY_MANIFEST}")"
    if [[ "${value}" != false ]]; then
        echo "Alloy must not mount the host's entire /var/log tree." >&2
        return 1
    fi

    value="$(yq eval --unwrapScalar '.spec.values.controller.volumes.extra[] | select(.name == "pod-logs") | .hostPath.path' "${ALLOY_MANIFEST}")"
    if [[ "${value}" != /var/log/pods ]]; then
        echo "Alloy's pod-logs hostPath must be exactly /var/log/pods." >&2
        return 1
    fi

    value="$(yq eval --unwrapScalar '.spec.values.alloy.mounts.extra[] | select(.name == "pod-logs") | [.mountPath, .readOnly] | @tsv' "${ALLOY_MANIFEST}")"
    if [[ "${value}" != $'/var/log/pods\ttrue' ]]; then
        echo "Alloy must mount only /var/log/pods and must mount it read-only." >&2
        return 1
    fi

    value="$(yq eval --unwrapScalar '.spec.values.alloy.securityContext | [.runAsUser, .runAsGroup, .readOnlyRootFilesystem, .allowPrivilegeEscalation, .seccompProfile.type] | @tsv' "${ALLOY_MANIFEST}")"
    if [[ "${value}" != $'0\t0\ttrue\tfalse\tRuntimeDefault' ]]; then
        echo "Alloy's root owner-read exception must retain the reviewed container hardening." >&2
        return 1
    fi

    value="$(yq eval --unwrapScalar '.spec.values.alloy.securityContext.capabilities.drop | join(",")' "${ALLOY_MANIFEST}")"
    if [[ "${value}" != ALL ]]; then
        echo "Alloy must drop every Linux capability." >&2
        return 1
    fi

    value="$(yq eval --unwrapScalar '.spec.values.controller.volumes.extra[] | select(.name == "tmp") | .emptyDir.sizeLimit' "${ALLOY_MANIFEST}")"
    if [[ "${value}" != 64Mi ]]; then
        echo "Alloy's read-only root filesystem must retain its bounded writable temporary volume." >&2
        return 1
    fi

    value="$(yq eval --unwrapScalar '.spec.values.alloy.mounts.extra[] | select(.name == "tmp") | .mountPath' "${ALLOY_MANIFEST}")"
    if [[ "${value}" != /tmp ]]; then
        echo "Alloy's bounded temporary volume must be mounted at /tmp." >&2
        return 1
    fi

    echo "Alloy logging retains exact paths, stable VictoriaLogs fields, bounded replay, a narrow host mount, and container hardening."
}

main "$@"
