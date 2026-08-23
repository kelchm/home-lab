#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    echo "usage: $0 <k8s-prod-1|k8s-prod-2|k8s-prod-3|node-ip>" >&2
    exit 2
}

case "${1:-}" in
    k8s-prod-1|10.32.30.11) node_name=k8s-prod-1; node_ip=10.32.30.11 ;;
    k8s-prod-2|10.32.30.12) node_name=k8s-prod-2; node_ip=10.32.30.12 ;;
    k8s-prod-3|10.32.30.13) node_name=k8s-prod-3; node_ip=10.32.30.13 ;;
    *) usage ;;
esac

ROOT_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT_DIR"

if command -v mise >/dev/null 2>&1 \
    && { [[ -z "${KUBECONFIG:-}" || ! -f "${KUBECONFIG}" ]] \
      || [[ -z "${TALOSCONFIG:-}" || ! -f "${TALOSCONFIG}" ]]; }; then
    eval "$(mise env -s bash)"
fi

for bin in jq kubectl talosctl yq; do
    command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
done

readonly timeout_secs="${VERIFY_TIMEOUT_SECS:-1800}"
readonly deadline=$(( $(date +%s) + timeout_secs ))

until talos_version="$(talosctl -n "$node_ip" version --short 2>/dev/null)"; do
    (( $(date +%s) < deadline )) || { echo "timed out waiting for Talos API on $node_name" >&2; exit 1; }
    echo "waiting for Talos API on $node_name..."
    sleep 10
done
printf '%s\n' "$talos_version"

expected_version="$(yq '.talosVersion' talos/talenv.yaml)"
server_version="$(awk '
    /^Server:/ { server=1; next }
    server && /^[[:space:]]*Tag:/ { print $2; exit }
    server && /Talos v/ { print $2; exit }
' <<<"$talos_version")"
if [[ "$server_version" != "$expected_version" ]]; then
    printf 'server version %s does not match target %s\n' "${server_version:-unknown}" "$expected_version" >&2
    exit 1
fi

kubectl wait --for=condition=Ready "node/$node_name" --timeout="${timeout_secs}s"

talosctl -n "$node_ip" read /etc/cni/net.d/00-multus.conf >/dev/null

echo "waiting for an instance-manager with lhnet1 on $node_name..."
while true; do
    instance_json="$(kubectl -n longhorn-system get pods \
        -l longhorn.io/component=instance-manager \
        --field-selector "spec.nodeName=$node_name" -o json)"
    bad_count="$(jq '[
      .items[]
      | (.status.containerStatuses // []) as $containers
      | (.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // "[]") as $raw
      | ($raw | fromjson? // []) as $networks
      | select(.status.phase != "Running"
          or ($containers | length) == 0
          or (all($containers[]; .ready == true) | not)
          or ([$networks[]
            | select(.name == "longhorn-system/storage-network"
                and .interface == "lhnet1"
                and any(.ips[]?; startswith("10.32.25.")))] | length) == 0)
    ] | length' <<<"$instance_json")"
    if (( $(jq '.items | length' <<<"$instance_json") > 0 && bad_count == 0 )); then
        break
    fi
    (( $(date +%s) < deadline )) || { echo "timed out waiting for a healthy instance-manager attachment on $node_name" >&2; exit 1; }
    sleep 10
done

jq -r '.items[] | [.metadata.name, .spec.nodeName, .status.phase, ((.status.containerStatuses // []) | all(.ready == true))] | @tsv' <<<"$instance_json"

echo 'waiting for every Longhorn volume to become healthy...'
while true; do
    volume_json="$(kubectl -n longhorn-system get volumes.longhorn.io -o json)"
    bad_volumes="$(jq -r '.items[] | select(.status.robustness != "healthy") | [.metadata.name, (.status.state // "unknown"), (.status.robustness // "unknown")] | @tsv' <<<"$volume_json")"
    if [[ -z "$bad_volumes" && $(jq '.items | length' <<<"$volume_json") -gt 0 ]]; then
        break
    fi
    printf '%s\n' "${bad_volumes:-no Longhorn volumes found yet}"
    (( $(date +%s) < deadline )) || { echo 'timed out waiting for Longhorn volumes' >&2; exit 1; }
    sleep 15
done

echo 'checking every Kubernetes node and Longhorn instance-manager...'
node_json="$(kubectl get nodes -o json)"
not_ready="$(jq -r '.items[] | select(any(.status.conditions[]?; .type == "Ready" and .status == "True") | not) | .metadata.name' <<<"$node_json")"
if [[ -n "$not_ready" ]]; then
    printf 'nodes not Ready:\n%s\n' "$not_ready" >&2
    exit 1
fi

instance_json="$(kubectl -n longhorn-system get pods -l longhorn.io/component=instance-manager -o json)"
bad_instances="$(jq -r '
  .items[]
  | (.status.containerStatuses // []) as $containers
  | (.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // "[]") as $raw
  | ($raw | fromjson? // []) as $networks
  | select(.status.phase != "Running"
      or ($containers | length) == 0
      or (all($containers[]; .ready == true) | not)
      or ([$networks[]
        | select(.name == "longhorn-system/storage-network"
            and .interface == "lhnet1"
            and any(.ips[]?; startswith("10.32.25.")))] | length) == 0)
  | [.metadata.name, .spec.nodeName, (.status.phase // "unknown")] | @tsv
' <<<"$instance_json")"
if (( $(jq '.items | length' <<<"$instance_json") == 0 )); then
    echo 'no Longhorn instance-manager pods found' >&2
    exit 1
fi
if [[ -n "$bad_instances" ]]; then
    printf 'instance-managers not Ready or missing storage-network/lhnet1:\n%s\n' "$bad_instances" >&2
    exit 1
fi

kubectl get nodes -o wide
kubectl -n tailscale get pods -o wide
printf 'NODE CHECKS PASSED: %s is Ready on %s, every Kubernetes node and instance-manager is Ready, every instance-manager has lhnet1, and all Longhorn volumes are healthy. Run talosctl health with one healthy control-plane node before the go/no-go decision.\n' "$node_name" "$server_version"
