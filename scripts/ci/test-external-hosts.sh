#!/usr/bin/env bash
# Exercise disabled/enrolled health behavior and parse every dashboard query.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
yq -o=json '.spec | {"groups": .groups}' "$root/kubernetes/apps/observability/victoria-metrics-k8s-stack/app/external-host-alerts.yaml" > "$scratch/source.json"
python3 "$root/scripts/monitoring/external-hosts/tests.py" "$scratch" "$root"
promtool check rules "$scratch/source.json" "$scratch/dashboard-rules.json"
promtool test rules "$scratch/test.json" "$scratch/disabled-test.json" "$scratch/dashboard-test.json"
