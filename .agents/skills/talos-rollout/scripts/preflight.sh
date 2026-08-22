#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT_DIR"

if command -v mise >/dev/null 2>&1 \
    && { [[ -z "${KUBECONFIG:-}" || ! -f "${KUBECONFIG}" ]] \
      || [[ -z "${TALOSCONFIG:-}" || ! -f "${TALOSCONFIG}" ]]; }; then
    eval "$(mise env -s bash)"
fi

for bin in jq kubectl talosctl; do
    command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
done

readonly NODES=(k8s-prod-1 k8s-prod-2 k8s-prod-3)
readonly IPS=(10.32.30.11 10.32.30.12 10.32.30.13)

section() {
    printf '\n== %s ==\n' "$1"
}

section "Talos versions"
for index in "${!NODES[@]}"; do
    printf '%s (%s)\n' "${NODES[$index]}" "${IPS[$index]}"
    talosctl -n "${IPS[$index]}" version --short
done

section "etcd members"
talosctl -n "${IPS[0]}" etcd members

section "Kubernetes nodes"
kubectl get nodes -o wide
not_ready="$(kubectl get nodes -o json | jq -r '[.items[] | select(.status.conditions[]? | select(.type == "Ready" and .status != "True")) | .metadata.name] | unique | .[]')"
if [[ -n "$not_ready" ]]; then
    printf 'nodes not Ready:\n%s\n' "$not_ready" >&2
    exit 1
fi

section "Multus safeguards"
kubectl -n kube-system get ds kube-multus-ds whereabouts cni-ready-untaint
unready_ds="$(kubectl -n kube-system get ds kube-multus-ds whereabouts cni-ready-untaint -o json \
    | jq -r '.items[] | select((.status.numberReady // 0) != (.status.desiredNumberScheduled // 0)) | .metadata.name')"
if [[ -n "$unready_ds" ]]; then
    printf 'unready networking DaemonSets:\n%s\n' "$unready_ds" >&2
    exit 1
fi

section "Longhorn volumes"
kubectl -n longhorn-system get volumes.longhorn.io \
    -o custom-columns='NAME:.metadata.name,STATE:.status.state,ROBUSTNESS:.status.robustness,NODE:.status.currentNodeID'
volume_json="$(kubectl -n longhorn-system get volumes.longhorn.io -o json)"
volume_count="$(jq '.items | length' <<<"$volume_json")"
bad_volumes="$(jq -r '.items[] | select(.status.robustness != "healthy") | [.metadata.name, (.status.state // "unknown"), (.status.robustness // "unknown")] | @tsv' <<<"$volume_json")"
if (( volume_count == 0 )); then
    echo 'no Longhorn volumes found' >&2
    exit 1
fi
if [[ -n "$bad_volumes" ]]; then
    printf 'Longhorn volumes are not healthy:\n%s\n' "$bad_volumes" >&2
    exit 1
fi

section "Longhorn instance-manager storage attachments"
instance_json="$(kubectl -n longhorn-system get pods -l longhorn.io/component=instance-manager -o json)"
bad_instances="$(jq -r '
  .items[]
  | (.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // "[]") as $raw
  | ($raw | fromjson? // []) as $networks
  | select(([$networks[]
      | select(.name == "longhorn-system/storage-network"
          and .interface == "lhnet1"
          and any(.ips[]?; startswith("10.32.25.")))] | length) == 0)
  | [.metadata.name, .spec.nodeName] | @tsv
' <<<"$instance_json")"
jq -r '.items[] | [.metadata.name, .spec.nodeName] | @tsv' <<<"$instance_json"
if (( $(jq '.items | length' <<<"$instance_json") == 0 )); then
    echo 'no Longhorn instance-manager pods found' >&2
    exit 1
fi
if [[ -n "$bad_instances" ]]; then
    printf 'instance-managers missing storage-network/lhnet1:\n%s\n' "$bad_instances" >&2
    exit 1
fi

section "Tailscale access path"
kubectl -n tailscale get pods -o wide

section "PodDisruptionBudgets"
kubectl get pdb -A

printf '\nPASS: etcd responded, all nodes are Ready, networking safeguards are Ready, and %s Longhorn volumes are healthy.\n' "$volume_count"
