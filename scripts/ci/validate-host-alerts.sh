#!/usr/bin/env bash
# Exercise the host out-of-memory, CPU and NVMe heat, Pi undervoltage and pending-update rules; the other host rules use MetricsQL that promtool cannot parse.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
yq '{"groups": [{"name": "hosts", "rules": [.spec.groups[] | select(.name == "hosts") | .rules[] | select(.alert == "NodeOOMKillDetected" or .alert == "NodeCPUTemperatureCritical" or .alert == "NodeNVMeCompositeTemperatureCritical" or .alert == "NodeSupplyVoltageLow" or .alert == "NodeSecurityUpdatesPending" or .alert == "NodePackageListsStale")]}]}' \
    kubernetes/apps/observability/metrics-ingest/app/alerts.yaml > "$test_dir/rules.yaml"
promtool check rules "$test_dir/rules.yaml"
cp scripts/monitoring/host-alerts.test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
