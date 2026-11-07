#!/usr/bin/env bash
# Read instance-manager pods (`kubectl get pods -o json`) on stdin and print one
# TSV row per pod that fails the rollout gates: name, node, reasons. Prints
# nothing when all pass. Shared by preflight.sh and verify-node.sh.
#
# instance-manager.jq checks the pod object. The network-status annotation can
# outlive the sandbox it describes, and a replacement sandbox can reuse the old
# pod IP, so a pod that passes is also asked, read-only, whether its lhnet1
# really carries the storage address the annotation names.
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
kubectl() { command kubectl --kubeconfig "${KUBECONFIG:-$ROOT_DIR/kubeconfig}" "$@"; }

jq -r -L "$SCRIPT_DIR" '
  include "instance-manager";
  .items[]
  | instance_manager_problems as $problems
  | [.metadata.namespace, .metadata.name, .spec.nodeName, ($problems | join("; ")),
     ([((.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // "[]") | fromjson? // [])[]?
        | select(.name == "longhorn-system/storage-network" and .interface == "lhnet1")
        | .ips[]? | select(startswith("10.32.25."))] | first // "")]
  | join("\u001f")
' | while IFS=$'\x1f' read -r namespace name node problems storage_ip; do
    if [[ -n "$problems" ]]; then
        printf '%s\t%s\t%s\n' "$name" "$node" "$problems"
    elif ! addresses="$(kubectl -n "$namespace" exec "$name" -c instance-manager -- ip -o -4 addr show dev lhnet1 2>&1 </dev/null)"; then
        printf '%s\t%s\t%s\n' "$name" "$node" "lhnet1 not present in the pod: ${addresses//$'\n'/ }"
    elif ! grep -qF "inet ${storage_ip}/" <<<"$addresses"; then
        printf '%s\t%s\t%s\n' "$name" "$node" "lhnet1 does not carry the annotated $storage_ip"
    fi
done
