#!/usr/bin/env bash
# Check normalized cluster facts with fresh/down/missing API source fixtures.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
yq '.spec | {"groups": .groups}' "$root/kubernetes/apps/observability/pve-api-exporter/app/rules.yaml" > "$scratch/rules.yaml"
cp "$root/scripts/monitoring/pve-api/rules-test.yaml" "$scratch/test.yaml"
promtool check rules "$scratch/rules.yaml"
promtool test rules "$scratch/test.yaml"
