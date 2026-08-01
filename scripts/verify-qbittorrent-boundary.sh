#!/usr/bin/env bash
set -Eeuo pipefail

namespace="${QBITTORRENT_NAMESPACE:-media}"
suffix="${RANDOM}"
allowed_pod="qbittorrent-boundary-allowed-${suffix}"
denied_pod="qbittorrent-boundary-denied-${suffix}"
target="http://qbittorrent.${namespace}.svc.cluster.local:8080/api/v2/app/version"

cleanup() {
    kubectl --namespace "${namespace}" delete pod \
        "${allowed_pod}" "${denied_pod}" \
        --ignore-not-found --wait=false >/dev/null
}
trap cleanup EXIT

kubectl --namespace "${namespace}" wait \
    --for=condition=Valid ciliumnetworkpolicy/qbittorrent --timeout=2m
kubectl --namespace "${namespace}" wait \
    --for=condition=Available deployment/qbittorrent --timeout=5m

# Gluetun owns the shared pod network namespace. Its health check confirms the
# VPN is up before connectivity through that namespace is tested.
kubectl --namespace "${namespace}" exec deployment/qbittorrent \
    --container=gluetun -- /gluetun-entrypoint healthcheck

kubectl --namespace "${namespace}" run "${allowed_pod}" \
    --image=docker.io/library/busybox:1.37.0 \
    --labels=app.kubernetes.io/name=sonarr \
    --restart=Never --command -- sleep 300
kubectl --namespace "${namespace}" run "${denied_pod}" \
    --image=docker.io/library/busybox:1.37.0 \
    --labels=app.kubernetes.io/name=boundary-test \
    --restart=Never --command -- sleep 300

kubectl --namespace "${namespace}" wait --for=condition=Ready \
    "pod/${allowed_pod}" "pod/${denied_pod}" --timeout=2m

# A pod carrying an approved workload identity can use qBittorrent's API.
kubectl --namespace "${namespace}" exec "${allowed_pod}" -- \
    wget -q -T 5 -O - "${target}"

# An unrelated pod in the same namespace must time out at the Cilium boundary.
if kubectl --namespace "${namespace}" exec "${denied_pod}" -- \
    wget -q -T 5 -O /dev/null "${target}"; then
    printf 'ERROR: unrelated pod reached qBittorrent\n' >&2
    exit 1
fi

# An unauthenticated request should still reach the OIDC middleware at the
# admin edge. Depending on middleware state it returns a challenge or redirect.
status="$({ curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --max-time 10 \
    https://qbittorrent.home.kelch.io; } || true)"
case "${status}" in
    200 | 302 | 401) ;;
    *)
        printf 'ERROR: qBittorrent admin route returned HTTP %s\n' "${status}" >&2
        exit 1
        ;;
esac

printf 'qBittorrent boundary verified (edge HTTP %s)\n' "${status}"
