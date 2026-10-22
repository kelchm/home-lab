#!/usr/bin/env bash
# Run the network health rule fixtures against the committed UniFi, Cilium BGP, host link and Traefik rules.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
yq '.spec' kubernetes/apps/observability/unpoller/app/alerts.yaml > "$test_dir/unifi.yaml"
yq '.spec' kubernetes/apps/kube-system/cilium/app/alerts.yaml > "$test_dir/cilium.yaml"
yq '.spec' kubernetes/apps/observability/metrics-ingest/app/network-alerts.yaml > "$test_dir/host-network.yaml"
yq '.spec' kubernetes/apps/network/traefik/app/alerts.yaml > "$test_dir/traefik.yaml"
promtool check rules "$test_dir/unifi.yaml" "$test_dir/cilium.yaml" "$test_dir/host-network.yaml" "$test_dir/traefik.yaml"
cp scripts/monitoring/unifi-alerts.test.yaml scripts/monitoring/cilium-alerts.test.yaml scripts/monitoring/host-network-alerts.test.yaml \
    scripts/monitoring/traefik-alerts.test.yaml "$test_dir/"
promtool test rules "$test_dir/unifi-alerts.test.yaml" "$test_dir/cilium-alerts.test.yaml" "$test_dir/host-network-alerts.test.yaml" \
    "$test_dir/traefik-alerts.test.yaml"
