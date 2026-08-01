#!/usr/bin/env bash
set -Eeuo pipefail

namespace="${QBITTORRENT_NAMESPACE:-media}"
network_namespace="${QBITTORRENT_NETWORK_NAMESPACE:-network}"
hostname="${QBITTORRENT_HOSTNAME:-qbittorrent.home.kelch.io}"
probe_observation_seconds="${PROBE_OBSERVATION_SECONDS:-35}"
suffix="${RANDOM}"
denied_pod="qbittorrent-boundary-denied-${suffix}"
target="http://qbittorrent.${namespace}.svc.cluster.local:8080/api/v2/app/version"

cleanup() {
    kubectl --namespace "${namespace}" delete pod "${denied_pod}" \
        --ignore-not-found --wait=false >/dev/null
}
trap cleanup EXIT

for command in kubectl jq curl awk grep; do
    command -v "${command}" >/dev/null || {
        printf 'ERROR: required command not found: %s\n' "${command}" >&2
        exit 1
    }
done

running_pod() {
    local pod_namespace="$1"
    local selector="$2"

    kubectl --namespace "${pod_namespace}" get pods \
        --selector="${selector}" --field-selector=status.phase=Running \
        --output=json | jq -r '.items[0].metadata.name // empty'
}

require_service_account() {
    local pod_namespace="$1"
    local pod="$2"
    local expected="$3"
    local actual

    actual="$(kubectl --namespace "${pod_namespace}" get pod "${pod}" \
        --output=jsonpath='{.spec.serviceAccountName}')"
    if [[ "${actual}" != "${expected}" ]]; then
        printf 'ERROR: %s/%s uses ServiceAccount %s, expected %s\n' \
            "${pod_namespace}" "${pod}" "${actual}" "${expected}" >&2
        exit 1
    fi
}

require_cilium_identity() {
    local pod_namespace="$1"
    local pod="$2"
    shift 2
    local endpoint label

    endpoint="$(kubectl --namespace "${pod_namespace}" get \
        ciliumendpoint "${pod}" --output=json)"
    for label in "$@"; do
        if ! jq -e --arg label "${label}" \
            '.status.identity.labels | index($label) != null' \
            <<<"${endpoint}" >/dev/null; then
            printf 'ERROR: Cilium identity for %s/%s lacks %s\n' \
                "${pod_namespace}" "${pod}" "${label}" >&2
            exit 1
        fi
    done
}

require_sa_cannot_mutate_pods() {
    local service_account="$1"
    local verb answer

    for verb in create patch; do
        answer="$(kubectl auth can-i "${verb}" pods \
            --namespace "${namespace}" \
            --as="system:serviceaccount:${namespace}:${service_account}" || true)"
        if [[ "${answer}" != "no" ]]; then
            printf 'ERROR: ServiceAccount %s can %s Pods in %s\n' \
                "${service_account}" "${verb}" "${namespace}" >&2
            exit 1
        fi
    done
}

check_approved_clients() {
    local client client_pod

    for client in sonarr radarr lidarr; do
        client_pod="$(running_pod "${namespace}" \
            "app.kubernetes.io/name=${client}")"
        if [[ -z "${client_pod}" ]]; then
            printf 'ERROR: no running %s pod matches the policy selector\n' \
                "${client}" >&2
            exit 1
        fi

        require_service_account "${namespace}" "${client_pod}" "${client}"
        require_cilium_identity "${namespace}" "${client_pod}" \
            "k8s:app.kubernetes.io/name=${client}" \
            "k8s:io.cilium.k8s.policy.serviceaccount=${client}"
        require_sa_cannot_mutate_pods "${client}"

        kubectl --namespace "${namespace}" exec "${client_pod}" -- \
            curl --fail --silent --show-error --max-time 5 "${target}"
    done
}

pod_restart_count() {
    kubectl --namespace "${namespace}" get pod "$1" --output=json | jq \
        '[.status.containerStatuses[]?.restartCount,
          .status.initContainerStatuses[]?.restartCount] | add // 0'
}

kubectl --namespace "${namespace}" wait \
    --for=condition=Valid ciliumnetworkpolicy/qbittorrent --timeout=2m
kubectl --namespace "${namespace}" wait \
    --for=condition=Available deployment/qbittorrent --timeout=5m

qbittorrent_pod="$(running_pod "${namespace}" \
    'app.kubernetes.io/name=qbittorrent')"
traefik_pod="$(running_pod "${network_namespace}" \
    'app.kubernetes.io/instance=traefik-admin-network')"
if [[ -z "${qbittorrent_pod}" || -z "${traefik_pod}" ]]; then
    printf 'ERROR: qBittorrent or admin Traefik has no running pod\n' >&2
    exit 1
fi

require_service_account "${namespace}" "${qbittorrent_pod}" qbittorrent
require_cilium_identity "${namespace}" "${qbittorrent_pod}" \
    'k8s:app.kubernetes.io/name=qbittorrent' \
    'k8s:io.cilium.k8s.policy.serviceaccount=qbittorrent'
require_service_account "${network_namespace}" "${traefik_pod}" traefik-admin
require_cilium_identity "${network_namespace}" "${traefik_pod}" \
    'k8s:app.kubernetes.io/instance=traefik-admin-network' \
    'k8s:io.cilium.k8s.policy.serviceaccount=traefik-admin'

initial_restart_count="$(pod_restart_count "${qbittorrent_pod}")"

# Gluetun owns the shared pod network namespace. Its health check confirms the
# VPN is up before connectivity through that namespace is tested.
kubectl --namespace "${namespace}" exec "${qbittorrent_pod}" \
    --container=gluetun -- /gluetun-entrypoint healthcheck

# Bracket the negative test with every approved client. This catches selector
# drift and demonstrates that a failed request is not a qBittorrent outage.
check_approved_clients

kubectl --namespace "${namespace}" run "${denied_pod}" \
    --image=docker.io/library/busybox:1.37.0 \
    --labels=app.kubernetes.io/name=boundary-test \
    --overrides='{"spec":{"serviceAccountName":"default","automountServiceAccountToken":false}}' \
    --restart=Never --command -- sleep 300

kubectl --namespace "${namespace}" wait --for=condition=Ready \
    "pod/${denied_pod}" --timeout=2m

# Prove the test pod can resolve Services and reach a separate HTTP workload.
# This rules out DNS and general pod-network failures before testing the deny.
kubectl --namespace "${namespace}" exec "${denied_pod}" -- \
    nslookup "qbittorrent.${namespace}.svc.cluster.local" >/dev/null
kubectl --namespace "${namespace}" exec "${denied_pod}" -- \
    wget -q -T 5 -O /dev/null \
    "http://sonarr.${namespace}.svc.cluster.local:8989/ping"

set +e
denied_output="$(kubectl --namespace "${namespace}" exec "${denied_pod}" -- \
    wget -T 5 -O /dev/null "${target}" 2>&1)"
denied_status=$?
set -e

if ((denied_status == 0)); then
    printf 'ERROR: unrelated pod reached qBittorrent\n' >&2
    exit 1
fi
if grep -Eqi 'bad address|not found|connection refused|permission denied|not permitted' \
    <<<"${denied_output}"; then
    printf 'ERROR: denied test failed for the wrong reason: %s\n' \
        "${denied_output}" >&2
    exit 1
fi
if ! grep -Eqi 'timed out|timeout' <<<"${denied_output}"; then
    printf 'ERROR: denied test produced an unclassified failure: %s\n' \
        "${denied_output}" >&2
    exit 1
fi

check_approved_clients

# Verify both the configured and accepted Gateway attachment point at the
# admin edge, including the exact OIDC middleware reference.
route="$(kubectl --namespace "${namespace}" get httproute qbittorrent \
    --output=json)"
if ! jq -e --arg network_namespace "${network_namespace}" '
    def admin_parent:
      .group == "gateway.networking.k8s.io"
      and .kind == "Gateway"
      and .name == "gateway-admin"
      and .namespace == $network_namespace
      and .sectionName == "https";
    any(.spec.parentRefs[]?; admin_parent)
    and any(.spec.rules[]?.filters[]?;
      .type == "ExtensionRef"
      and .extensionRef.group == "traefik.io"
      and .extensionRef.kind == "Middleware"
      and .extensionRef.name == "kanidm-oidc")
    and any(.status.parents[]?;
      (.parentRef | admin_parent)
      and any(.conditions[]?; .type == "Accepted" and .status == "True")
      and any(.conditions[]?; .type == "ResolvedRefs" and .status == "True"))
  ' <<<"${route}" >/dev/null; then
    printf 'ERROR: qBittorrent HTTPRoute is not attached to the expected OIDC edge\n' >&2
    exit 1
fi

gateway_ip="$(kubectl --namespace "${network_namespace}" get \
    service traefik-admin --output=json | \
    jq -r '.status.loadBalancer.ingress[0].ip // empty')"
if [[ -z "${gateway_ip}" ]]; then
    printf 'ERROR: traefik-admin has no load-balancer IP\n' >&2
    exit 1
fi

edge_headers="$(curl --silent --show-error --dump-header - --output /dev/null \
    --max-time 10 --resolve "${hostname}:443:${gateway_ip}" \
    "https://${hostname}/")"
edge_status="$(awk '$1 ~ /^HTTP\// {status=$2} END {print status}' \
    <<<"${edge_headers}")"
if [[ "${edge_status}" != "401" ]]; then
    printf 'ERROR: qBittorrent OIDC edge returned HTTP %s\n' \
        "${edge_status}" >&2
    exit 1
fi
if ! grep -Eqi '^set-cookie:[[:space:]]*arr-suite\.Session=' \
    <<<"${edge_headers}"; then
    printf 'ERROR: OIDC edge response lacks the arr-suite session cookie\n' >&2
    exit 1
fi

# Observe a full 30-second app probe interval, then require the same pod to be
# Ready, healthy, and free of new container restarts.
sleep "${probe_observation_seconds}"
kubectl --namespace "${namespace}" wait --for=condition=Ready \
    "pod/${qbittorrent_pod}" --timeout=2m
kubectl --namespace "${namespace}" wait \
    --for=condition=Available deployment/qbittorrent --timeout=2m
kubectl --namespace "${namespace}" exec "${qbittorrent_pod}" \
    --container=gluetun -- /gluetun-entrypoint healthcheck
final_restart_count="$(pod_restart_count "${qbittorrent_pod}")"
if [[ "${final_restart_count}" != "${initial_restart_count}" ]]; then
    printf 'ERROR: qBittorrent pod restart count changed from %s to %s\n' \
        "${initial_restart_count}" "${final_restart_count}" >&2
    exit 1
fi

printf 'qBittorrent boundary verified (OIDC edge HTTP %s)\n' "${edge_status}"
