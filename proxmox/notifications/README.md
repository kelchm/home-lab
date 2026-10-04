# Proxmox backup notifications

## State and delivery path

[#720](https://github.com/kelchm/home-lab/issues/720) prepares central delivery: PVE → the existing VMAuth proxy → VMAlertmanager → the existing `k8s-prod Alerts` Pushover application. The repository configuration is prepared for review; the PVE webhook has not been applied or delivery-tested. The live PVE cluster still has only `mail-to-root`.

The [ingestion configuration](../../kubernetes/apps/observability/metrics-ingest/app/alert-ingest.yaml) exposes only `POST https://alert-ingest.home.kelch.io/api/v2/alerts`. The dedicated VMUser accepts only that hostname and backend path. It cannot use the metrics-ingest hostname or access silences, query metrics, or remotely write metrics. The normal Alertmanager UI keeps its OIDC authentication. The operator generates the ingestion password into the `vmuser-pve-backup-notifications` Secret in `observability`; no credential is stored in Git.

The [body template](alertmanager.json.hbs) sends `PVEBackupFailed` with `source=pve`, `cluster=pve-sbx`, `severity=warning`, the originating node and backup job, and an event timestamp. It does not claim `evaluator=vmalert`. VMAlertmanager's dedicated route reuses the existing Pushover application at normal priority, respecting quiet hours. Central silences and notification-delivery metrics apply.

These are one-shot events, not continuously evaluated conditions. Each node/job/timestamp identifies a separate event, so the next failed run can notify again despite the normal twelve-hour repeat interval. The route sends immediately and the event expires after the global five-minute `resolve_timeout`. Its receiver has `send_resolved: false`: expiry is not evidence that a backup recovered. The event is visible in Alertmanager while active; it does not create an `ALERTS` time series or a durable incident record. Full details remain in PVE task history. A short summary avoids Pushover's 1,024-character message limit.

The existing healthchecks.io Watchdog monitors the shared evaluation and delivery pipeline. It does not prove that PVE submitted an event. API availability monitoring and future backup-freshness collection cover the source side; this change does not add another dead-man check. A PVE webhook is not a durable queue: unsuccessful submission needs investigation in PVE's logs, and the end-to-end acceptance test remains essential.

## Apply after review

The owner merges the PR. Do not apply PVE configuration until the owner explicitly approves it; a merge or Git revert does not change configuration under `/etc/pve`.

After Flux reconciles, confirm the VMUser is Ready, its generated Secret exists, the HTTPRoute is Accepted with ResolvedRefs, and Alertmanager has successfully reloaded. From a PVE node, verify the endpoint presents a valid certificate and rejects missing/incorrect credentials. With valid credentials, verify GET on the alert endpoint, POST to silences, and requests through the metrics-ingest hostname cannot access the Alertmanager backend. Do not display credentials or put them in shell history or process arguments.

Create a stock webhook target named `pve-sbx-alertmanager` in Datacenter → Notifications:

| Setting | Value |
|---|---|
| Method | POST |
| URL | `https://alert-ingest.home.kelch.io/api/v2/alerts` |
| Header | `Content-Type: application/json` |
| Authorization header | `Basic {{ secrets.authorization }}` |
| Body | Contents of [alertmanager.json.hbs](alertmanager.json.hbs) |
| Secret `authorization` | Base64 encoding of the generated VMUser's `username:password` |

Transfer the generated credential into the PVE secret through a protected channel without printing it or writing plaintext files. Base64 is encoding, not encryption. PVE stores target configuration in `/etc/pve/notifications.cfg` and secrets in root-only `/etc/pve/priv/notifications.cfg`. The encrypted [configuration recovery archive](../README.md#configuration-recovery-archive) captures both files. Losing or rotating the Kubernetes credential requires updating PVE's secret as well.

Create a matcher named `backup-failures` with mode `all`, field match `exact:type=vzdump`, severity `error`, and target `pve-sbx-alertmanager`. Both conditions must match. Leave `daily-backups` on `notification-mode=notification-system`; legacy `mailto` and `mailnotification` settings are ignored in that mode. The existing `default-matcher` may continue routing other notifications to `mail-to-root`.

## Failed-backup acceptance test

The owner has authorized creating and cleaning up an isolated disposable guest. Applying the PVE notification configuration still requires explicit approval.

1. Confirm no scheduled backup is about to run, obtain an unused VMID, and create a stopped VM named `backup-notification-test` with a disposable tag, no disks and no network interfaces. Record its VMID and node. Do not change production guests, shared storage, or `daily-backups`.
2. Set a temporary `backup` lock on that guest, then run a real `vzdump` task selecting only that VMID, using `backups-pve-sbx` and `notification-mode=notification-system`. Confirm the task fails inside the backup worker due to the guest lock; a command-line validation failure is insufficient.
3. Verify `PVEBackupFailed` in VMAlertmanager with `source=pve`, `cluster=pve-sbx`, the correct node, `backup_job=manual` and `event_id`. Have the operator confirm the normal-priority Pushover notification. Check Alertmanager's Pushover failure counter, then wait beyond the five-minute expiry and confirm no recovery notification is sent.
4. Recheck the test guest's identity and stopped state, remove its temporary lock, destroy that guest, and confirm the VMID is gone. Record the failed task status/log, notification time, operator receipt confirmation and cleanup in #720. Leave the issue open for the owner.

A built-in target test bypasses matchers and cannot prove failure-only selection. Use the deliberately failed backup as the acceptance evidence; also verify the matcher configuration excludes successful `vzdump` events and errors from other notification types.

## Missed-run monitoring decision

Failure events cannot detect a job that never starts. The installed `prometheus-pve-exporter` 3.10.1 reports selection coverage through `pve_not_backed_up_info`, not backup completion or age; current upstream collectors also lack an archive timestamp metric. Read-only inspection on 2026-10-04 found that PVE's `GET /nodes/{node}/storage/backups-pve-sbx/content?content=backup` returns archive `vmid` and `ctime` to root. Persistent guests 101, 102 and 200 each had archives from that day's 05:00 run.

The existing `metrics@pve` auditor token returns an empty archive list. PVE 9.2.11 filters archive access through privileges including `Datastore.AllocateSpace` and `VM.Backup`, or `Datastore.Allocate`. Do not grant those write-capable privileges just for freshness monitoring. An empty response from that token does not mean no backups exist.

Freshness is worth monitoring. The recommended follow-up is a small bounded host-local `pvesh` collector feeding the existing node-exporter textfile collector, with rules evaluated by vmalert and delivered through the existing Alertmanager route. This reuses the stock API, exporters, metrics pipeline and Watchdog, but requires a collection script and manual host installation; it is not part of the notification configuration in this PR.

Before implementation, decide how to select covered persistent guests, publish the newest archive time per guest, distinguish missing archives from collection failure, bound command execution, and detect a stopped collector. A proposed threshold is 30 hours for this daily job. Collection errors must not publish zero timestamps as if all backups vanished. Archive time measures freshness, not restore validity. Any resulting rules must select PVE series explicitly, preserving the Kubernetes rule filters for `cluster="k8s-prod"`. This unfinished work remains tracked in #720.

Notification behavior was checked against installed documentation package 9.2.4 and the VZDump implementation on PVE 9.2.11. See the [Proxmox notification documentation](https://pve.proxmox.com/pve-docs/chapter-notifications.html), [VMUser authentication documentation](https://docs.victoriametrics.com/operator/resources/vmuser/), [Alertmanager client contract](https://github.com/prometheus/alertmanager/blob/main/docs/alerts_api.md), and [alert delivery runbook](../../docs/runbooks/alerting.md).
