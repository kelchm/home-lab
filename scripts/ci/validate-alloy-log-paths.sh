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
readonly SERVICE_CONTAINER_RULE='rule{source_labels=["__meta_kubernetes_pod_container_name"]regex="(.+)"target_label="service_name"}'
readonly SERVICE_LEGACY_APP_RULE='rule{source_labels=["__meta_kubernetes_pod_label_app"]regex="(.+)"target_label="service_name"}'
readonly SERVICE_RECOMMENDED_APP_RULE='rule{source_labels=["__meta_kubernetes_pod_label_app_kubernetes_io_name"]regex="(.+)"target_label="service_name"}'
readonly EXPECTED_VL_URL='url="http://victoria-logs-single-server.observability.svc.cluster.local:9428/insert/loki/api/v1/push?message_fields_prefix=msg.&_msg_field=msg.message,msg.msg,msg.log,msg.event,msg.record.message&_stream_fields=cluster,namespace,service_name,pod,container,node"'
readonly EXPECTED_EXTERNAL_LABELS='external_labels={cluster="k8s-prod",}'
# This is an Alloy selector, not a shell expression.
# shellcheck disable=SC2016
readonly EXPECTED_LEVEL_SELECTOR='selector=`{level_candidate=~"trace|debug|info|warning|error|critical"}`'
# These preserve an inner Alloy template through the chart's Helm tpl pass and
# distinguish a logfmt envelope from prose containing an incidental key=value.
# shellcheck disable=SC2016
readonly EXPECTED_TEMPLATE_ESCAPE='template={{printf"%q"`'
# shellcheck disable=SC2016
readonly EXPECTED_LOGFMT_GATE='{{-$is_logfmt:=and(regexMatch"^[[:space:]]*[A-Za-z_][A-Za-z0-9_.-]*=".Entry)$has_logfmt_envelope-}}'
readonly EXPECTED_DUAL_WRITE='forward_to=[loki.write.loki.receiver,loki.write.vl.receiver,]'
# shellcheck disable=SC2016
readonly EXPECTED_LEVEL_ALLOWLIST_CHAIN='stage.labels{values={level_candidate="level",}}stage.match{selector=`{level_candidate=~"trace|debug|info|warning|error|critical"}`stage.labels{values={level="level",}}}stage.label_drop{values=["level_candidate"]}'

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

    if [[ "$(count_occurrences "${compact}" 'target_label="service_name"')" != 3 || \
          "$(count_occurrences "${compact}" 'target_label="app_instance"')" != 1 || \
          "$(count_occurrences "${compact}" "${SERVICE_CONTAINER_RULE}")" != 1 || \
          "$(count_occurrences "${compact}" "${SERVICE_LEGACY_APP_RULE}")" != 1 || \
          "$(count_occurrences "${compact}" "${SERVICE_RECOMMENDED_APP_RULE}")" != 1 ]]; then
        echo "Alloy must derive service_name through the reviewed label precedence and preserve app_instance separately." >&2
        return 1
    fi

    case "${compact}" in
        *"${SERVICE_CONTAINER_RULE}"*"${SERVICE_LEGACY_APP_RULE}"*"${SERVICE_RECOMMENDED_APP_RULE}"*) ;;
        *)
            echo "Alloy service_name precedence must be container, then legacy app, then app.kubernetes.io/name." >&2
            return 1
            ;;
    esac

    if [[ "$(count_occurrences "${compact}" 'stage.decolorize{}')" != 1 || \
          "$(count_occurrences "${compact}" 'stage.json{')" != 1 || \
          "$(count_occurrences "${compact}" 'stage.logfmt{')" != 1 || \
          "$(count_occurrences "${compact}" "${EXPECTED_LEVEL_SELECTOR}")" != 1 || \
          "$(count_occurrences "${compact}" 'values=["level_candidate"]')" != 1 || \
          "$(count_occurrences "${compact}" "${EXPECTED_TEMPLATE_ESCAPE}")" != 1 || \
          "$(count_occurrences "${compact}" "${EXPECTED_LOGFMT_GATE}")" != 1 || \
          "$(count_occurrences "${compact}" "${EXPECTED_LEVEL_ALLOWLIST_CHAIN}")" != 1 ]]; then
        echo "Alloy must retain ANSI cleanup and bounded explicit JSON/logfmt severity normalization." >&2
        return 1
    fi

    case "${compact}" in
        *'stage.cri{}'*'stage.decolorize{}'*'stage.json{'*'stage.logfmt{'*'stage.template{'*) ;;
        *)
            echo "Alloy parsing stages must preserve CRI, ANSI cleanup, structured extraction, and normalization order." >&2
            return 1
            ;;
    esac

    if [[ "$(count_occurrences "${compact}" "${EXPECTED_DUAL_WRITE}")" != 1 ]]; then
        echo "Alloy must keep exactly one Loki and VictoriaLogs dual-write fan-out until convergence is accepted." >&2
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

    echo "Alloy logging retains exact paths, normalized service/severity fields, bounded replay, a narrow host mount, and container hardening."
}

main "$@"
