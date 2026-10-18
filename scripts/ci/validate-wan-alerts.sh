#!/usr/bin/env bash
# Run the Starlink and T-Mobile rule fixtures against the committed rules.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
yq '.spec' kubernetes/apps/observability/starlink-exporter/app/alerts.yaml > "$test_dir/starlink.yaml"
yq '.spec' kubernetes/apps/observability/json-exporter/app/alerts.yaml > "$test_dir/tmobile.yaml"
promtool check rules "$test_dir/starlink.yaml" "$test_dir/tmobile.yaml"
cp scripts/monitoring/wan-alerts.test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
