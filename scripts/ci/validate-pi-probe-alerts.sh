#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
yq '.spec' kubernetes/apps/observability/metrics-ingest/app/probe-alerts.yaml > "$test_dir/rules.yaml"
# Only HostCollectorDown: other host rules use MetricsQL that promtool cannot parse.
yq '{"groups": [{"name": "hosts", "rules": [.spec.groups[] | select(.name == "hosts") | .rules[] | select(.alert == "HostCollectorDown")]}]}' \
  kubernetes/apps/observability/metrics-ingest/app/alerts.yaml > "$test_dir/host-rules.yaml"
promtool check rules "$test_dir/rules.yaml" "$test_dir/host-rules.yaml"
cp scripts/monitoring/pi-probe-alerts.test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
