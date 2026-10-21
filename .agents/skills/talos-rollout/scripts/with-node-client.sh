#!/usr/bin/env bash
# Run a command with talosctl matched to the node's Kubernetes-reported OS.
# Use only while the node is healthy; after reboot, verify the expected target.
set -Eeuo pipefail

case "${1:-}" in
    k8s-prod-1|10.32.30.11) node_name=k8s-prod-1 ;;
    k8s-prod-2|10.32.30.12) node_name=k8s-prod-2 ;;
    k8s-prod-3|10.32.30.13) node_name=k8s-prod-3 ;;
    *) echo 'usage: with-node-client.sh <node-name-or-ip> <command> [args...]' >&2; exit 2 ;;
esac
shift
(( $# > 0 )) || { echo 'a command is required' >&2; exit 2; }

ROOT_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
node_kubeconfig="${KUBECONFIG:-$ROOT_DIR/kubeconfig}"
node_talosconfig="${TALOSCONFIG:-$ROOT_DIR/talos/clusterconfig/talosconfig}"
for credential in "$node_kubeconfig" "$node_talosconfig"; do
    [[ -s "$credential" ]] || { echo "missing or empty credential file: $credential" >&2; exit 1; }
done

os_image="$(kubectl --kubeconfig "$node_kubeconfig" get node "$node_name" -o jsonpath='{.status.nodeInfo.osImage}')"
readonly os_pattern='^Talos \(v([0-9]+\.[0-9]+\.[0-9]+)\)$'
[[ "$os_image" =~ $os_pattern ]] || { echo "cannot select a client from $node_name OS image: $os_image" >&2; exit 1; }
node_version="${BASH_REMATCH[1]}"

export KUBECONFIG="$node_kubeconfig" TALOSCONFIG="$node_talosconfig"
exec mise exec "aqua:siderolabs/talos@$node_version" -- "$@"
