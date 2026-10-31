#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
python3 scripts/logging/validate-config.py
python3 scripts/logging/test-fluent-bit.py
python3 scripts/logging/test-fluent-bit-events.py
python3 scripts/logging/test-logsql-rules.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/logging/test-unifi-flows.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/logging/test-unifi-flows-queries.py
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
rules_file="$test_dir/rules.yaml"
yq '.spec' kubernetes/apps/observability/victoria-metrics-k8s-stack/app/platform-alerts.yaml > "$rules_file"
promtool check rules "$rules_file"
cp scripts/logging/alerts-test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
echo 'Log and event collector configuration and mapping, the UniFi flow collector, logging alert rules and LogsQL rules validated.'
