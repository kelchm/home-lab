#!/usr/bin/env bash
# Exercise the host health and local NTP rules; the remaining host rules use MetricsQL that promtool cannot parse.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
yq '{"groups": [{"name": "hosts", "rules": [.spec.groups[] | select(.name == "hosts") | .rules[] | select(.alert == "NodeCollectorDown" or .alert == "NTPNotSynchronized" or .alert == "NTPClockErrorHigh" or .alert == "NodeOOMKillDetected" or .alert == "NodeCPUTemperatureCritical" or .alert == "NodeNVMeCompositeTemperatureCritical" or .alert == "NodeSupplyVoltageLow" or .alert == "NodeSecurityUpdatesPending" or .alert == "NodePackageListsStale" or .alert == "NodeLogDeliveryFailing" or .alert == "NodeLogsDropped" or .alert == "NodeLogsSilent")]}]}' \
    kubernetes/apps/observability/metrics-ingest/app/alerts.yaml > "$test_dir/rules.yaml"
promtool check rules "$test_dir/rules.yaml"
cp scripts/monitoring/host-alerts.test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
