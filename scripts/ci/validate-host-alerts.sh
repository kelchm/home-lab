#!/usr/bin/env bash
# Exercise host health, link speed, and local NTP rules. Other host rules use MetricsQL that promtool cannot parse.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
yq '{"groups": [{"name": "hosts", "rules": [.spec.groups[] | select(.name == "hosts") | .rules[] | select(.alert == "NodeCollectorDown" or .alert == "NTPNotSynchronized" or .alert == "NTPClockErrorHigh" or .alert == "NodeOOMKillDetected" or .alert == "NodeCPUTemperatureCritical" or .alert == "NodeNVMeCompositeTemperatureCritical" or .alert == "NodeSupplyVoltageLow" or .alert == "NodeSecurityUpdatesAvailable" or .alert == "NodeSecurityUpdatesOverdue" or .alert == "NodePackageListsStale" or .alert == "NodeLogDeliveryFailing" or .alert == "NodeLogsDropped" or .alert == "NodeLogsSilent" or .alert == "NodeBoardTemperatureCritical" or .alert == "InferenceEngineBroken" or .alert == "InferenceEngineStalled" or .alert == "NodeMemoryAvailableLow" or .alert == "NodeMemoryAvailableCritical")]}]}' \
    kubernetes/apps/observability/metrics-ingest/app/alerts.yaml > "$test_dir/rules.yaml"
yq 'select(.metadata.name == "vm-node-physical-link-speed") | {"groups": .spec.groups}' \
    kubernetes/apps/observability/victoria-metrics-k8s-stack/app/alerts.yaml > "$test_dir/node-link-rules.yaml"
promtool check rules "$test_dir/rules.yaml" "$test_dir/node-link-rules.yaml"
cp scripts/monitoring/host-alerts.test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
