# PVE backup metrics alternative

Prepared for comparison with [the webhook PR #760](https://github.com/kelchm/home-lab/pull/760), for [#720](https://github.com/kelchm/home-lab/issues/720). This is intended behavior, not a deployed or receipt-tested configuration. The owner chooses the approach and merges the selected PR; installing anything on PVE requires explicit go-ahead.

## Data and delivery

A root-owned systemd timer runs the standard-library Python collector once a minute on each PVE node. It only calls local `pvesh get`: cluster identity, guest inventory, archived `vzdump` tasks from the last seven days (at most 1,000), and the backup storage's archive inventory. It writes an atomic node-exporter textfile. The existing vmagent → VMAuth → VictoriaMetrics → vmalert → VMAlertmanager → Pushover path carries the observations and alerts. Existing Pushover credentials, central silences, delivery metrics and the healthchecks.io dead-man check are reused.

Read-only checks against PVE 9.2.11 on 2026-10-04 confirmed the stock task API exposes completed task status and time, and the storage-content API exposes archive VMID and creation time. Exporter 3.10.1 provides backup-job coverage but no result or archive-age metric. Its existing PVEAuditor token can read task history but receives an empty archive list; archive access requires write-capable privileges. Local root collection avoids expanding that account or adding a credential. The new root service and its manual installation are the cost of this approach. See the [PVE API viewer](https://pve.proxmox.com/pve-docs/api-viewer/index.html) and [node-exporter textfile collector](https://github.com/prometheus/node_exporter#textfile-collector).

| Signal | Scope | Alert |
|---|---|---|
| Latest completed task status and completion time | Local guest VMID, or `batch` for a multi-guest task | `PVEBackupFailed`, warning after one minute of failed status |
| Newest archive creation time in `backups-pve-sbx` | Local, non-template guest tagged `persistent`; zero means a successful inventory found no archive | `PVEBackupStale`, critical after age exceeds 30 hours for ten minutes |
| Collection success and attempt time | Each node | `PVEBackupMetricsUnavailable`, warning after two minutes of failed/missing collection, or an attempt older than ten minutes |

Thirty hours allows six hours beyond the daily schedule; it detects an absent run as well as persistent failures. Archive age is evidence of an artifact, not proof of a restorable backup. The existing coverage rule continues to detect persistent guests omitted from all jobs.

This is a condition model, not an event ledger. A later success for the same task scope clears the failure; a failed task followed by a successful retry between polls may never alert. A VMID-specific retry does not clear a failed `batch` scope. Task conditions also disappear when they leave the seven-day window or their guest is removed/migrated; resolution alone does not prove a successful retry. Freshness follows guests to their current node using the shared archive inventory.

Collection failures preserve previous observations and publish health=0, so they cannot manufacture a zero archive time or falsely clear an existing failure/staleness condition. A timer that stops leaves an old attempt timestamp; a missing textfile is detected against each healthy PVE node-exporter target. Existing host-down and ingestion alerts cover loss of the whole host stream. Hard NFS storage can stall inventory; the command and service timeouts bound normal hangs, while stale-attempt monitoring catches a collector that stops making progress.

Host ingestion retains `job=pve-node`, `platform=pve` and its authoritative node `instance`. Only these backup observations carry the locally verified `cluster=pve-sbx` label. Their rules explicitly select the host job and platform; general Kubernetes rules remain restricted to `cluster=k8s-prod`. No group-level cluster label is applied to host series.

## Installation after the owner's decision

Install and verify the collector on **all three nodes before merging the selected metrics PR**, after explicit permission to make PVE changes. The textfiles can exist before node-exporter's textfile flag is deployed; merging activates that flag and the rules through the existing doco-cd and Flux flows. Merging first would intentionally produce missing-collector warnings until the manual installation finishes. Preserve any previous installed files before replacement.

From the repository root, repeat for each node, substituting its address for NODE:

```sh
scp proxmox/monitoring/backup-metrics/collect.py proxmox/monitoring/backup-metrics/pve-backup-metrics.service proxmox/monitoring/backup-metrics/pve-backup-metrics.timer kelchm@NODE:/tmp/
ssh kelchm@NODE 'sudo install -d -o root -g root -m 0755 /var/lib/node_exporter/textfile_collector && sudo install -o root -g root -m 0755 /tmp/collect.py /usr/local/sbin/pve-backup-metrics && sudo install -o root -g root -m 0644 /tmp/pve-backup-metrics.service /tmp/pve-backup-metrics.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now pve-backup-metrics.timer && sudo systemctl start pve-backup-metrics.service'
ssh kelchm@NODE 'systemctl list-timers pve-backup-metrics.timer --no-pager; sudo journalctl -u pve-backup-metrics.service -n 20 --no-pager; cat /var/lib/node_exporter/textfile_collector/pve-backups.prom'
```

Check health=1, the correct cluster, expected local persistent guests and archive timestamps on every node. After owner merge and both reconcilers settle, query these metrics centrally with `job="pve-node", platform="pve", cluster="pve-sbx"`; verify one healthy collector per node, recent attempts, all three persistent guest archive timestamps, and no `node_textfile_scrape_error`. Verify the existing dead-man and notification-delivery checks in the [alerting runbook](../../../docs/runbooks/alerting.md). Keep rollout and receipt evidence in #720, then update the PVE README to describe verified delivery.

## Acceptance after deployment

The owner has authorized creation and cleanup of an isolated disposable guest. Use a free VMID, no NIC, no boot, a small disk and the disposable tag. Run the test outside the scheduled backup window and remove the guest before that window; do not alter the production job, storage or an existing guest to cause a failure.

Create a temporary root-owned executable hook that exits nonzero only for `backup-start`, and run a manual `vzdump TEST_VMID --storage backups-pve-sbx --mode snapshot --compress zstd --script HOOK_PATH` on that guest. Check the archived task has a non-OK status and that the next collection emits `pve_backup_task_success{scope="TEST_VMID"} 0`. A command-line validation failure before a task is recorded is not a valid test.

Allow several minutes for the timer, scrape, one-minute rule hold and Alertmanager's grouping delay. Record the UPID, VMID/node, failed task status, central metric, firing alert and the owner's confirmation of Pushover receipt. Check central silencing using `alertname=PVEBackupFailed`, `cluster=pve-sbx`, `instance=NODE_NAME` and `scope=TEST_VMID`. Remove the hook, delete the verified disposable guest and any test archive, then confirm its VMID-specific metric disappears on the next collection. Guest cleanup may resolve that condition; it is not evidence of backup recovery. Do not claim end-to-end delivery until the operator confirms receipt.

Freshness and missing-collector behavior are exercised by rule fixtures without deleting real archives or stopping production telemetry. A real failed-backup receipt remains the acceptance gate.

## Removal and verification

Git revert removes the rules and textfile flag through Flux/doco-cd. It does **not** uninstall the root collector. After the owner's approval, disable the timer and remove only its installed service, timer, script and `pve-backups.prom` file, then reload systemd. Do not remove a shared textfile directory or other exporters' files.

```sh
sudo systemctl disable --now pve-backup-metrics.timer
sudo systemctl stop pve-backup-metrics.service
sudo rm /etc/systemd/system/pve-backup-metrics.service /etc/systemd/system/pve-backup-metrics.timer /usr/local/sbin/pve-backup-metrics /var/lib/node_exporter/textfile_collector/pve-backups.prom
sudo systemctl daemon-reload
```

Local regression checks: `scripts/ci/validate-pve-backups.sh` runs collector tests and Prometheus rule fixtures. No PVE configuration or notification target is changed by those checks.
