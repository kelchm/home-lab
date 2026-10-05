#!/usr/bin/env bash
# Check the PVE backup webhook body against the sample the rules expect, then run the rule fixtures.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# PVE renders the body; this only substitutes its three expressions.
rendered="$(sed -e 's/{{#if fields\.\[job-id\]}}{{escape fields\.\[job-id\]}}{{else}}manual{{\/if}}/daily-backups/' \
    -e 's/{{escape severity}}/info/' -e 's/{{timestamp}}/1/' proxmox/monitoring/backup-metrics/body.hbs; echo .)"
expected=$'pve_backup_completed_timestamp_seconds{backup_job="daily-backups",outcome="info"} 1\n.'
if [[ "$rendered" != "$expected" ]]; then
    echo "proxmox/monitoring/backup-metrics/body.hbs no longer renders the one-line sample the rules select" >&2
    exit 1
fi

test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
yq '.spec' kubernetes/apps/observability/pve-exporter/app/backup-alerts.yaml > "$test_dir/rules.yaml"
promtool check rules "$test_dir/rules.yaml"
cp proxmox/monitoring/backup-metrics/alerts.test.yaml "$test_dir/tests.yaml"
promtool test rules "$test_dir/tests.yaml"
