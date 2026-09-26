#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
python3 scripts/logging/validate-config.py
python3 scripts/logging/render-dashboard.py --check
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
rules_file="$test_dir/rules.yaml"
yq '.spec' kubernetes/apps/observability/victoria-metrics-k8s-stack/app/platform-alerts.yaml > "$rules_file"
promtool check rules "$rules_file"
cp scripts/logging/alerts-test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
echo 'vlagent configuration and alert rules validated.'
