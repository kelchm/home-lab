# PVE backup completion metrics

Alerts the operator when a PVE backup fails or the daily job stops completing, for [#720](https://github.com/kelchm/home-lab/issues/720). **State: prepared.** The rules, the webhook body and these procedures are in Git; nothing is installed on PVE and no alert has been received. Installing the webhook and running the acceptance test each need the owner's explicit go-ahead.

| File | Purpose |
|---|---|
| [body.hbs](body.hbs) | Body of the PVE webhook: one Prometheus sample per completed backup run. |
| [alerts.test.yaml](alerts.test.yaml) | Fixtures for the [rules](../../../kubernetes/apps/observability/pve-exporter/app/backup-alerts.yaml), run by `scripts/ci/validate-pve-backups.sh`. |

## How it works

For runs using `notification-mode=notification-system`, PVE sends every finished `vzdump` run, success or failure, through a stock webhook target to the vmagent already running on the same node. The production `daily-backups` job already uses that mode. Manual runs must use it too; `auto` with an email recipient or `legacy-sendmail` bypasses this target:

```text
POST http://127.0.0.1:8429/api/v1/import/prometheus?extra_label=job%3Dpve-backup&extra_label=cluster%3Dpve-sbx
Content-Type: text/plain
```

The body is one sample of `pve_backup_completed_timestamp_seconds`. Its value is PVE's notification timestamp, which is when the run finished. `backup_job` is the scheduled job's ID, or `manual` for a run started from the UI, the CLI or the job's Run now button, none of which carry a job ID. `outcome` is PVE's severity: `info` for a clean run, `error` when any guest in it failed. The URL adds `job` and `cluster`; the node's ingestion token adds `instance` and `platform` as for every other [host series](../../../docs/runbooks/host-monitoring.md#identity). vmagent queues the sample on disk while the ingestion endpoint is unreachable and delivers it with its original time.

Nothing new runs on the nodes: no script, service, credential, ingestion user, Alertmanager route or Pushover application. The alerts route like every other vmalert alert, described in the [alerting runbook](../../../docs/runbooks/alerting.md). The existing `mail-to-root` target and `default-matcher` are left as they are.

## Alerts

| Alert | Severity | Fires when |
|---|---|---|
| `PVEBackupFailed` | warning | A run of a job finished with errors on a node. Labelled with `instance` and `backup_job`. |
| `PVEBackupStale` | critical | A node in the expected list has no clean `daily-backups` completion within 30 hours, held for ten minutes. |

Thirty hours is the daily schedule plus six hours, so one missed or failed 05:00 run pages by about 11:00.

**Expected nodes.** `PVEBackupStale` names `pve-sbx-1`, `pve-sbx-2` and `pve-sbx-3` in its expression. The list is deliberate: a node that has never reported, whose webhook was removed, or whose samples have aged out alerts exactly like one whose last success is old, and no other exporter has to be up for that to hold. Edit the list when a node joins or leaves the cluster, and keep the fixtures in step. Every listed node needs at least one guest in `daily-backups`; whether PVE sends a notification from a node with nothing to back up has not been checked.

**What a failure alert means.** The rules read the latest failure and the latest success for each node and job over 48 hours. A failure alerts for at least one hour, so one that is retried successfully before vmalert next evaluates still notifies. After that hour it resolves once the same job has succeeded on the same node, or when the failure is 48 hours old. A resolved notification therefore does not prove the guests were backed up; check the job. A failed `daily-backups` run stays firing until the next scheduled run succeeds, even after a successful manual retry, because a manual run cannot stand in for the schedule: silence it once the retry is confirmed. All manual runs on a node share `backup_job="manual"`, so a manual success for one guest clears a manual failure for another.

## Limits

- This is per-node job completion, not per-guest archive age or integrity. A clean run says PVE reported no error for the guests that job selected on that node; it does not show which guests those were, that an archive is retained, or that it restores. `PVEPersistentGuestNotBackedUp` still covers guests left out of every job, and restore drills remain the only evidence of restorability.
- PVE makes one delivery attempt to the local vmagent, with a ten-second timeout, when the run finishes. If vmagent is down at that moment the sample is lost. A lost `daily-backups` result, success or failure, surfaces as `PVEBackupStale` about six hours later. A lost manual failure is not reported.
- It reports conditions, not a ledger of events. Consecutive failures of one job continue one alert. If the ingestion path is down for over an hour and a failure and its successful retry both replay afterwards, the failure never alerts.
- A node missing from the expected list still alerts on failures but never on absence.
- vmagent's loopback port takes samples from any local process, as it always has. The token fixes `instance` and `platform`; `job` and `cluster` are only what the URL says.

## Install

Notification configuration lives in `/etc/pve` and is cluster-wide, so create it once from any node. Each node then posts to its own vmagent. **Git does not manage or revert it.** Do this before the rules merge, and only after the owner's go-ahead.

```sh
scp proxmox/monitoring/backup-metrics/body.hbs kelchm@pve-sbx-1:/tmp/pve-backup-metrics.hbs
ssh kelchm@pve-sbx-1
```

Record the starting state, then create the target and its matcher. Do not print `/etc/pve/priv/notifications.cfg`.

```sh
sudo pvesh get /cluster/notifications/targets
sudo pvesh get /cluster/notifications/matchers
sudo cat /etc/pve/notifications.cfg

sudo pvesh create /cluster/notifications/endpoints/webhook --name pve-backup-metrics --method post \
  --url 'http://127.0.0.1:8429/api/v1/import/prometheus?extra_label=job%3Dpve-backup&extra_label=cluster%3Dpve-sbx' \
  --header "name=Content-Type,value=$(printf 'text/plain' | base64 -w0)" \
  --body "$(base64 -w0 /tmp/pve-backup-metrics.hbs)" \
  --comment 'Backup completion samples to the local vmagent'

sudo pvesh create /cluster/notifications/matchers --name pve-backup-metrics --mode all \
  --match-field exact:type=vzdump --match-severity info,error \
  --target pve-backup-metrics \
  --comment 'Every finished vzdump run'
```

Check the result: the new target and matcher exist, `mail-to-root` and `default-matcher` are unchanged, and the stored body decodes to the file in Git.

```sh
sudo pvesh get /cluster/notifications/endpoints/webhook/pve-backup-metrics
sudo pvesh get /cluster/notifications/matchers/pve-backup-metrics
sudo pvesh get /cluster/notifications/matchers/default-matcher
sudo awk '/^webhook: / {selected = ($2 == "pve-backup-metrics")} /^[^[:space:]]/ && !/^webhook: / {selected = 0} selected && $1 == "body" {print $2}' /etc/pve/notifications.cfg | base64 -d | diff - /tmp/pve-backup-metrics.hbs
```

Then send PVE's target test from **each** node. It bypasses the matcher and carries no job ID, so it is expected to write one `backup_job="manual"`, `outcome="info"` sample from that node. That proves the URL, header, body, local vmagent and ingestion path, not the matcher or the scheduled label. Do not run it while a manual failure is firing on that node: its synthetic success would clear that condition once the failure's one-hour visibility window ends.

```sh
sudo pvesh create /cluster/notifications/targets/pve-backup-metrics/test
```

```promql
max_over_time(pve_backup_completed_timestamp_seconds{job="pve-backup", platform="pve", cluster="pve-sbx"}[48h])
```

Expect one series per node, each with that node's `instance`. If a sample is missing, inspect the task log, node journal and local vmagent before proceeding.

### Merge after the first scheduled run

Leave the rules unmerged until the next 05:00 run has written a `backup_job="daily-backups"`, `outcome="info"` sample from all three nodes. That run is the first real check of the matcher and the job ID, and it means `PVEBackupStale` starts out satisfied. Merged any earlier, the rule correctly reports three nodes with no success and pages critical ten minutes later. If the owner wants to merge sooner, first add a silence for `alertname=PVEBackupStale`, `cluster=pve-sbx` that expires an hour after the next scheduled run, with #720 in its comment.

If a node's run fails that morning, it has no success sample: fix the backup, and expect that node to page until its next clean scheduled run.

## Acceptance test

Run after the install and the merge, with the owner's go-ahead for these steps. The owner has authorized creating and removing one isolated disposable guest for it. Never use an existing guest, and do not edit `daily-backups`, its storage or its exclusions.

The test uses its own **scheduled** job so the sample carries a job ID; Run now would report `manual`. Its `backup_job` differs from `daily-backups`, so it cannot satisfy or disturb daily freshness. `daily-backups` selects every guest, so the test guest must be gone well before 05:00: run this at midday and finish the cleanup the same day.

1. Pick a node, confirm a free VMID with `sudo pvesh get /cluster/nextid`, and create a guest with no NIC that is never started:

   ```sh
   sudo qm create TEST_VMID --name backup-alert-test --memory 512 --scsi0 local-lvm:1 --tags disposable --onboot 0
   ```

2. On that node, install a hook that fails only the `backup-start` phase:

   ```sh
   printf '%s\n' '#!/bin/sh' '[ "$1" != backup-start ]' | sudo tee /usr/local/sbin/backup-alert-test-hook >/dev/null
   sudo chmod 0755 /usr/local/sbin/backup-alert-test-hook
   ```

3. Create the temporary job for a minute at least three minutes ahead, in the node's local time (`date`):

   ```sh
   sudo pvesh create /cluster/backup --id backup-alert-test --node NODE --vmid TEST_VMID \
     --storage backups-pve-sbx --mode snapshot --compress zstd --notification-mode notification-system \
     --schedule HH:MM --script /usr/local/sbin/backup-alert-test-hook --enabled 1 \
     --comment 'Temporary: backup alert acceptance for #720'
   ```

   If no `vzdump` task appears within three minutes of that time, do not use Run now. Move the schedule forward with `sudo pvesh set /cluster/backup/backup-alert-test --schedule HH:MM`.

4. Confirm the failure end to end: the task in `sudo pvesh get /nodes/NODE/tasks --typefilter vzdump --limit 3` has a non-OK status; the query above shows `backup_job="backup-alert-test"`, `outcome="error"` from that node; `PVEBackupFailed` is firing in Alertmanager with `severity=warning` and `evaluator=vmalert`; and the owner confirms the Pushover notification arrived.

5. Confirm the success path. Remove the hook from the job and schedule another run:

   ```sh
   sudo pvesh set /cluster/backup/backup-alert-test --delete script --schedule HH:MM
   ```

   Expect an `outcome="info"` sample, and the alert to resolve one hour after the failed run finished, not immediately.

6. Clean up, job first so it cannot run again. Recheck the VMID and name before destroying the guest, and free only archives that name it.

   ```sh
   sudo pvesh delete /cluster/backup/backup-alert-test
   sudo rm /usr/local/sbin/backup-alert-test-hook
   sudo pvesm list backups-pve-sbx --vmid TEST_VMID
   sudo pvesm free VOLID
   sudo qm config TEST_VMID
   sudo qm destroy TEST_VMID --purge
   ```

   Confirm with `sudo pvesh get /cluster/backup` that only the production job remains. The test samples stay in the store and age out of the rules on their own. If the success run could not be completed, silence `alertname=PVEBackupFailed`, `backup_job=backup-alert-test` for 48 hours instead of leaving it to repeat.

7. Record in #720 both task UPIDs, the node, the job ID, the two samples, the alert and the owner's confirmation of receipt. Then change the state at the top of this file and in the [PVE README](../../README.md) to verified.

Staleness and a node that never reports are exercised by the rule fixtures and by the first scheduled run after install, not by stopping production backups.

## Remove

Reverting the rules in Git does not touch PVE, and removing the webhook while the rules are live makes `PVEBackupStale` page 30 hours after the last success. Revert the rules first, then remove the matcher before the target it references:

```sh
sudo pvesh delete /cluster/notifications/matchers/pve-backup-metrics
sudo pvesh delete /cluster/notifications/endpoints/webhook/pve-backup-metrics
sudo pvesh get /cluster/notifications/matchers
```
