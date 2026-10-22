#!/usr/bin/env bash
# Exercise the CloudNativePG cluster rules kept next to each database cluster.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
yq '.spec' kubernetes/apps/iot/visionect/app/alerts.yaml > "$test_dir/visionect.yaml"
yq '.spec' kubernetes/apps/printing/bambuddy/app/alerts.yaml > "$test_dir/bambuddy.yaml"
promtool check rules "$test_dir/visionect.yaml" "$test_dir/bambuddy.yaml"
cp scripts/monitoring/cnpg-alerts.test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
