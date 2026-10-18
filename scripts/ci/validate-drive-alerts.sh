#!/usr/bin/env bash
# Exercise shared drive rules with cluster and host series, including absent data.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
yq '.spec' kubernetes/apps/observability/smartctl-exporter/app/rules.yaml > "$test_dir/rules.yaml"
yq '{"groups": [{"name": "smartctl-chart", "rules": [.spec.values.prometheusRules.rules[] | select(.enabled) | del(.enabled)]}]}' \
    kubernetes/apps/observability/smartctl-exporter/app/helmrelease.yaml > "$test_dir/chart.yaml"
promtool check rules "$test_dir/rules.yaml" "$test_dir/chart.yaml"
cp scripts/monitoring/drive-alerts.test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
