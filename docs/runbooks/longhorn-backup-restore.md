# Longhorn Backup & Restore

Runbook for the Longhorn-native backup MVP: scheduled backups to Synology NFS, restoring an individual PV, and the broader disaster-recovery shape.

## What this protects

- **PV data** — every Longhorn volume in the `default` recurring-job group, captured nightly to NFS.

## What this does NOT protect

- **Kubernetes manifests, Secrets, CRDs, HelmReleases.** Recovery rebuilds them from this repository with `task bootstrap:talos` and `task bootstrap:apps`; see [Disaster recovery](#disaster-recovery-rebuild-the-cluster-and-restore-data).
- **The sops `age.key`.** Without it, sops-encrypted Secrets in this repo cannot be decrypted. Stored in 1Password (`k8s-prod-sops-age-key`); a copy on a separate device is the recovery path. **If the 1Password entry and your laptop both burn, the cluster's secret state is lost.**
- **Anything not in git and not Longhorn-resident.** Manually-applied resources, drift in cluster-scoped Longhorn settings, cert-manager Order/Challenge state, etc.
- **NFS-backed PVs (csi-driver-nfs).** Bulk media on the Synology is the Synology's backup problem, not Longhorn's.

If any of those gaps grow load-bearing, layer Velero on top — the BackupTarget already has a `longhorn/` subdir, so Velero gets its own sibling subdir without disturbing this setup.

## Backup target

- BackupTarget URL: `nfs://10.32.25.5:/volume1/backups-k8s-prod/longhorn`
- Path on NAS: `/volume1/backups-k8s-prod/longhorn/` (Longhorn writes `backupstore/{volumes,backups}/...` underneath)
- Network path: instance-manager pods reach `10.32.25.5` via the `lhnet1` storage-network attachment. Longhorn peers use `10.32.25.128/28`, with an explicit `10.32.25.5/32` route to the NAS; backup transfer rides the 2.5GbE storage NIC. This filtered path completed an on-demand backup on 2026-10-29; see [storage-interface isolation](longhorn-storage-network-cutover.md#storage-interface-isolation-1120).
- Synology NFS export rules (verified against DSM 2026-08-02):

  | Client | Privilege | Squash | Async | Non-priv ports | Cross-mount |
  |---|---|---|---|---|---|
  | `10.32.25.128/28` | Read/Write | Map root to admin | Yes | Allowed | Denied |
  | `10.32.25.11`, `.12`, `.13`, `.14` | Read/Write | Map root to admin | Yes | Allowed | Denied |

  - The `/28` admits the Longhorn instance-manager pods, which mount the target from their Multus storage-VLAN IPs. `.11`–`.13` are the node storage NICs (longhorn-manager traffic SNATs to these); `.14` is from the host-expansion reservation (`architecture.md`).
  - Root squashes to admin because Longhorn writes as root in-pod; admin holds rights on the share ACL. Non-root UIDs pass through unmapped.
  - Contrast with the read-only `lemon-manuals-k8s-prod` share (all users → admin, reserved ports only): that share serves kubelet-initiated read-only mounts, this one takes root writes from in-pod clients.

## Schedule and retention

Defined in `kubernetes/apps/longhorn-system/longhorn/app/recurringjobs.yaml`:

Cron is evaluated in UTC, so the America/New_York wall time shifts an hour across daylight saving:

| Job | Cron (UTC) | Local | Retain | Group |
|---|---|---|---|---|
| `backup-daily` | `0 7 * * *` | 03:00 EDT / 02:00 EST | 7 | default |
| `backup-weekly` | `0 8 * * 0` | Sunday 04:00 EDT / 03:00 EST | 4 | default |

`retain` is a **count**, not a time window. If a job is unable to run for several days the count doesn't reset; you just have a sparser series until the schedule catches up.

## Group membership and opt-out

**Policy: backed up unless the manifest says otherwise.** Longhorn's `default` group is implicit — a Longhorn `Volume` carrying no recurring-job labels has `recurring-job-group.longhorn.io/default: enabled` added for it, so a PVC that says nothing is backed up. Keep it that way. Omission failing towards a backup costs NAS capacity; omission failing towards no backup costs data. Only volumes whose contents are regenerable should opt out, and they do it in their own manifest so the exclusion is reviewable in the diff.

Currently excluded:

| Volume | Why regenerable |
|---|---|
| `ai/lemon-manuals-mcp` `search` (100 GiB) | SQLite FTS index built lazily from the read-only `lemon-manuals-k8s-prod` NFS share |

### How to opt a volume out

Label the PVC with **both** of these:

```yaml
metadata:
  labels:
    recurring-job.longhorn.io/source: enabled
    recurring-job-group.longhorn.io/no-backup: enabled
```

- `recurring-job.longhorn.io/source: enabled` is what makes Longhorn read the PVC's recurring-job labels at all. Without it the volume controller logs `Ignoring recurring job labels ... due to missing source label` and the other label is inert.
- `recurring-job-group.longhorn.io/no-backup: enabled` is copied onto the `Volume`. No `RecurringJob` CR names `no-backup`, so it schedules nothing — but its presence is enough to stop Longhorn from adding the `default` group.

The source label itself is deliberately *not* copied to the `Volume`, so it cannot stand in for the group label. Source-only leaves the volume with zero recurring-job labels, and Longhorn re-adds `default`.

Verify after Flux reconciles:

```sh
kubectl -n longhorn-system get volumes.longhorn.io \
  -o custom-columns='PVC:.status.kubernetesStatus.pvcName,LABELS:.metadata.labels' \
  | grep <pvc-name>
```

`recurring-job-group.longhorn.io/default` must be gone and `no-backup` present.

To put it back, drop **only** `no-backup` and leave `source` in place. Sync then removes `no-backup` from the `Volume`, which leaves zero recurring-job labels, and Longhorn re-adds `default`. Removing `source` in the same edit strands the volume: sync stops running, `no-backup` is never cleared off the `Volume`, and `default` never returns. `source` alone is a stable steady state meaning "backed up", so there is no need to take it off afterwards.

### Existing backups survive the opt-out

Retention (`retain: N`) is enforced by the job as it runs, per volume. Once a volume leaves the group the job never visits it again, so whatever is already on the NAS stays there indefinitely. Reclaim it explicitly — Longhorn UI → **Backup** → select the backup volume → Delete, or:

```sh
kubectl -n longhorn-system delete backupvolumes.longhorn.io <backup-volume-name>
```

## Routine monitoring

vmalert evaluates the Longhorn rules in `kubernetes/apps/longhorn-system/longhorn/app/alerts.yaml`; VMAlertmanager delivers warnings and critical alerts through the `k8s-prod Alerts` Pushover application. The rules cover BackupTarget availability, cluster-wide backup staleness, per-volume stale or never-completed backups, volume robustness, Longhorn node readiness, disk capacity/schedulability, and missing metric coverage. See [alerting](alerting.md) for ownership, silence policy, and end-to-end tests.

Open [Longhorn / Operations in Grafana](https://grafana.home.kelch.io/d/longhorn-operations) for volume health, backup freshness, capacity, and I/O. Start with **Scrape coverage**: expect 100% of desired Longhorn manager pods; anything else is red because missing scrapes can undercount the other panels. Namespace and PVC selectors filter volume and backup panels; node capacity, node conditions, CPU, and BackupTarget availability remain cluster-wide. Missing metrics display **No data**, not a healthy zero. **Unknown health** is advisory (amber): detached volumes normally report unknown robustness. Check attachment state in the Longhorn UI before treating this as a fault.

Backup counts exclude PVCs explicitly labeled `no-backup`, using the same exemption rule as the alerts. **Stale backup** uses the per-volume 30-hour threshold; **Never backed up** shows new volumes immediately, while the alert grants 30 hours for a first backup. **Missing timestamps** identifies expected volumes with no timestamp metric. The separate cluster-wide stalled-backup alert still uses 26 hours. Backup age proves freshness only; use the restore drill below to verify recoverability.

Node disk usage measures physical storage, while scheduled replica sizes represent logical allocations. Volume actual size includes snapshots and is not the filesystem usage inside the PVC. The dashboard is provisioned from [versioned JSON](../../kubernetes/apps/observability/grafana/app/longhorn-dashboard.json), adapted from the pinned onzack upstream source linked in its description. Change it in Git; Grafana UI edits are not the source of truth.

Use these checks to investigate a notification or audit the automation:

- **Longhorn UI → Backup → Backup Volume** — every active PV except those listed under "Currently excluded" should show backups within the last ~24h.
- `kubectl -n longhorn-system get backuptargets.longhorn.io default -o yaml` — `status.available` must be `true` and `status.lastSyncedAt` recent.
- `kubectl -n longhorn-system get backups.longhorn.io --sort-by=.metadata.creationTimestamp` — last few entries should be recent.
- `longhorn_backup_target_available{backup_target="default"}` — must be `1`.
- `longhorn_volume_last_backup_at` — drives the 26-hour cluster-wide and 30-hour per-volume thresholds.
- Longhorn manager logs containing `backup failed` or `BackupTarget unavailable` — use when the CR status and timestamp alerts fire.
- Free space on the NAS share — DSM → Storage Manager → Volume 1 utilization.

## Review orphaned backup volumes

`LonghornBackupVolumeOrphaned` reports each backup volume whose source name has been absent from the observed live volume inventory continuously for 21 days. Each volume has its own timer; Alertmanager groups these by alert name and cluster into normal-priority `warning` notifications (12-hour repeats, no critical page). Backup presence uses a one-hour lookback to bridge brief scrape failures and collector ownership handoffs; a stale backup sample must not erase weeks of pending time. Live source presence stays instant, so a source recreated with the same name resets its timer immediately; a restore under a new name leaves the original backup volume a candidate. This is a review prompt, never permission to delete. Missing source-volume scrapes can make a live volume look absent. Backup-metric gaps of an hour or more and evaluator interruptions can reset the observed wait; it is not an authoritative deletion timestamp.

**Stop cleanup during a cluster rebuild, incomplete recovery, a restore drill, or a Talos rollout.** After a rebuild, every old backup volume can look orphaned: those backups are exactly what recovery needs. Coordinate with the owner of any off-site restore work (including #297) before touching the backup target. Keep backups needed for recovery even if the alert has fired; use a bounded, volume-specific silence with a reason and revisit date when appropriate.

1. **Confirm that the live volume list is complete before deleting anything.** Check the intended kube context, all nodes Ready, all Longhorn manager pods Ready, and full Longhorn scrape coverage in the Operations dashboard. Resolve `LonghornMetricsMissing`, failed scrapes, API/list errors, or controller errors first. Compare the complete Longhorn volume list with PV CSI handles, expected application PVCs, and the Longhorn UI → Volume list. Bound PVCs alone do not prove completeness after a rebuild: confirm that every expected application and recovery claim is present and restored, and that the owner has declared recovery complete.

   ```sh
   kubectl config current-context
   kubectl get nodes
   kubectl -n longhorn-system get pods -l app=longhorn-manager
   kubectl -n longhorn-system get volumes.longhorn.io \
     -o custom-columns='VOLUME:.metadata.name,STATE:.status.state,NAMESPACE:.status.kubernetesStatus.namespace,PVC:.status.kubernetesStatus.pvcName,RESTORE_REQUIRED:.status.restoreRequired'
   kubectl get pv -o json | jq -r '.items[] | select(.spec.csi.driver == "driver.longhorn.io") | [.metadata.name, .spec.csi.volumeHandle, .status.phase, .spec.claimRef.namespace, .spec.claimRef.name] | @tsv'
   kubectl get pvc -A
   kubectl -n longhorn-system get backuptargets.longhorn.io
   ```

2. **Find candidates, then inspect the backup metadata.** In Grafana Explore, use the same presence query as the rule; an empty result means no candidates, not a scalar zero. The metrics count Backup CRs, so a backup volume with no Backup CRs is outside this alert's coverage. State values must not be filtered: detached/restoring volumes still exist, `longhorn_volume_state` includes zero-valued states, and `longhorn_backup_state` can itself be zero.

   ```promql
   count by (volume) (last_over_time(longhorn_backup_state{cluster="k8s-prod"}[1h]))
   unless on (volume)
   count by (volume) (longhorn_volume_state{cluster="k8s-prod"})
   ```

   Cross-check each candidate against the complete API inventory and BackupTarget availability/sync status. The BackupVolume CR's name can differ from its source volume name; select it by `.spec.volumeName` and the intended target, not by a guessed CR name. Inspect its recorded namespace/PVC, backup dates, size, and messages:

   ```sh
   source_volume='<volume label from the alert>'
   kubectl -n longhorn-system get volumes.longhorn.io "$source_volume"
   kubectl -n longhorn-system get backupvolumes.longhorn.io -o json | jq --arg volume "$source_volume" '
     .items[] | select(.spec.volumeName == $volume) |
     {name: .metadata.name, target: .spec.backupTargetName, source: .spec.volumeName, status: .status}'
   kubectl -n longhorn-system get backups.longhorn.io -l "backup-volume=$source_volume" \
     --sort-by=.status.backupCreatedAt -o custom-columns='BACKUP:.metadata.name,CREATED:.status.backupCreatedAt,STATE:.status.state,URL:.status.url'
   ```

   Also list restore/standby references across all live volumes, including volumes with different names from the source. Compare `FROM_BACKUP` URLs with the candidate's backup URLs and source volume parameter/target; a matching standby volume or unfinished restore still depends on those backups. An empty list does not prove a rebuild is complete or remove the need to review recovery plans.

   ```sh
   kubectl -n longhorn-system get volumes.longhorn.io -o json | jq -r '
     .items[] | select((.spec.fromBackup // "") != "" or .spec.standby == true or .status.restoreRequired == true or .status.restoreInitiated == true) |
     [.metadata.name, .spec.fromBackup, .spec.standby, .status.restoreRequired, .status.restoreInitiated] | @tsv'
   ```

   Only an explicit `NotFound` for the source, after the completeness checks, supports absence; a timeout, forbidden response, or other API failure does not. Confirm why the original volume was deleted, whether a replacement now owns the same application's data, and whether any rollback, restore, retention, or off-site recovery plan still needs its backups. Age alone is insufficient. Record the decision and exact source, target, and BackupVolume CR name in the operational issue before proceeding.

3. **Delete one reviewed backup volume by hand.** Recheck source absence and that no rebuild/restore/rollout has begun immediately before deletion. In Longhorn UI → Backup, select the exact target and backup volume and choose Delete, or use the reviewed CR name:

   ```sh
   backup_volume_cr='<reviewed BackupVolume metadata.name>'
   kubectl -n longhorn-system delete backupvolumes.longhorn.io "$backup_volume_cr"
   ```

   This removes the backup volume and its backups from the target; it is destructive. Do not bulk-delete query output, delete the BackupTarget, or remove target files directly. Let Longhorn reconcile, then verify that the BackupVolume and its Backup CRs disappear, the target remains available with unrelated backups intact, and the candidate disappears from the query and alert once the one-hour backup-presence lookback expires and evaluation catches up. Investigate controller errors or a stuck deletion instead of stripping finalizers. Off-site retention may preserve older copies; this operation does not remove them.

Rule logic and the full 21-day wait are tested offline by `scripts/ci/validate-longhorn-alerts.sh`, using the committed production expression and delay. Fixtures cover pending before the boundary, firing at the boundary despite a stale backup sample midway through the wait, resolution after the backup-presence lookback expires, same-name restoration resetting the timer, detached/restoring/never-backed-up volumes, duplicate backups/scrapes, zero-valued states, and isolation from other clusters. This proves Prometheus rule semantics on synthetic samples; it does not prove production vmalert loading, a real orphan remaining for weeks, or Pushover delivery.

## Retained orphan replica directories

When replica data is left on a node disk with no volume using it, Longhorn records it as an `Orphan` (`orphans.longhorn.io`). These are not active replicas: no Volume, Replica, Engine, PV or PVC references them. They are also not orphaned backup volumes: [Review orphaned backup volumes](#review-orphaned-backup-volumes) and `LonghornBackupVolumeOrphaned` cover backups on the target, and deleting a backup volume leaves replica directories alone.

Two orphaned replica directories on k8s-prod-3 are kept on purpose following the 2026-10-21 inventory and the 2026-10-25 retention decision ([#925](https://github.com/kelchm/home-lab/issues/925)). Each is a copy of an original database volume, replaced when its successor instance was promoted on 2026-08-23, and holds named historical checkpoints paired with application-side recovery assets verified present in that inventory:

| Directory | GiB | Holds | Paired asset |
|---|---:|---|---|
| `pvc-9ce0f3c1-0579-44ad-8848-ef747d47a935-023042dc` | 5.34 | Original `iot/visionect-db-1` volume, with the `vss-cutover-db` snapshot | The `vss-cutover-data` backup |
| `pvc-c09ee0bc-9bfe-4eec-87a9-588f065e71be-d7c59992` | 2.61 | Original `printing/bambuddy-db-1` volume, with the pre- and post-historical-import snapshots | The post-import application backup and the pre-import local snapshot on the live `printing/bambuddy` volume |

They are not a verified backup. Their consistent recoverability has never been tested, and the Longhorn backups of their original volumes were deleted in [#297](https://github.com/kelchm/home-lab/issues/297). The running databases do not use them.

- Exclude both from any orphan cleanup, and do not enable automatic deletion of orphaned replica data (Longhorn's `orphan-resource-auto-deletion` setting) while they exist.
- Decide a directory's fate when its paired application-side asset is retired, and decide both before k8s-prod-3 is reinstalled or its disk is replaced: installing wipes the Longhorn data partition.
- Retention does not authorize deletion; any later cleanup needs an explicit decision and fresh reference and health checks as recorded in #925.

## Drill: restore a single PV

Use case: an app's data is corrupted/wiped and you want the previous night's copy back.

### 0. Identify the source backup

In the Longhorn UI: **Backup → Backup Volume → \<volume name\>** lists all backups for that volume. Pick the one you want by timestamp. Note the volume name (e.g. `pvc-7c2f...`) and the backup name.

CLI equivalent:

```sh
kubectl -n longhorn-system get backupvolumes
kubectl -n longhorn-system get backups -l backup-volume=<volume-name> \
  --sort-by=.metadata.creationTimestamp
```

### 1. Restore through an explicit Volume

Longhorn restores only into a `Volume` whose `spec.fromBackup` names the backup. Use the Volume/PV/claim recipe in [Restore one claim](#restore-one-claim) for every restore. That recipe is written for a rebuilt cluster; on a running cluster, ignore its step 1 warning about backup volumes named after a current PV: the live volume's own backup volume is the one you want, and the backup to pick is the last one before the damage. Shortcuts that do not work on Longhorn 1.12, and that this runbook used to suggest:

- **The UI's "Use Previous Name" restore over a live claim.** It needs the original volume name to be free, and the claim's own volume still holds it.
- **A claim annotated `longhorn.io/from-backup`.** The CSI driver ignores the annotation; it reads `fromBackup` only from StorageClass parameters, which our classes leave empty. The claim binds to a new, empty volume and looks like a successful restore.
- **Renaming a claim.** Kubernetes cannot rename a PVC. Point the workload at a new claim in Git, or recreate the claim under its old name against the restored PV.

Choose one shape:

- **Inspect a copy first.** Recommended for a first restore, and whenever you are not sure which backup is good. It leaves the live claim and its volume untouched. Follow [Restore one claim](#restore-one-claim) steps 1 and 3, skipping step 2, and give the Volume and PV a new name (for example `<claim>-restore`) and the claim a new name, in the application's namespace or a disposable one. Add the label `recurring-job-group.longhorn.io/no-backup: enabled` to the Volume's metadata so the copy does not start its own backup history. Wait for the restore as in step 4, then inspect it (step 2 below).
- **Replace the live claim.** Follow [Restore one claim](#restore-one-claim) steps 1–4. Deleting the claim destroys the current volume and its local snapshots, because the `longhorn` class reclaims with `Delete`. If anything on it may still matter, back it up first (Longhorn UI → **Volume** → select → **Create Backup**) or inspect a copy first. On a running cluster the backup volume's name is the live volume's name, so before step 3 wait until the old volume and PV are gone:

  ```sh
  kubectl -n longhorn-system get volumes.longhorn.io <backup-volume-name>   # until NotFound
  kubectl get pv <backup-volume-name>                                       # until NotFound
  ```

### 2. Inspect

Mount the restored claim from a debug pod while nothing else uses it:

```sh
kubectl -n <ns> run restore-inspect --rm -it --restart=Never \
  --image=alpine \
  --overrides='{"spec":{"containers":[{"name":"x","image":"alpine","command":["sh"],"stdin":true,"tty":true,"volumeMounts":[{"name":"d","mountPath":"/data"}]}],"volumes":[{"name":"d","persistentVolumeClaim":{"claimName":"<restored-claim>"}}]}}'
```

Check for content you know was there before the backup, not just that the filesystem mounts: an empty volume also mounts and binds.

### 3. Verify

- Workload comes back healthy
- Application-level smoke test (DB query, dashboard load, etc.)
- New backup runs that night and shows up alongside the restored volume

### 4. Cleanup

- An inspection copy uses a `Retain` PV, so deleting its claim leaves the volume behind. Delete the claim, then the PV, then the Longhorn volume:

  ```sh
  kubectl -n <ns> delete pvc <claim>-restore
  kubectl delete pv <claim>-restore
  kubectl -n longhorn-system delete volumes.longhorn.io <claim>-restore
  ```

- If you scaled down a workload, ensure it scales back to declared replicas

## Disaster recovery: rebuild the cluster and restore data

Use this when all three nodes are lost or wiped and the NAS backup share is intact. It rebuilds Talos and Flux from this repository, then restores volume data from the Longhorn backups.

**If the NAS is also lost,** the Longhorn backup store has an off-site copy in Backblaze B2, taken daily at 10:00 UTC. Restore it first by following [Off-site backup from Athena](../../synology/offsite-backup/README.md#restore), and serve the directory that contains `backupstore/` over NFS. In the step 4 commit, also change `backupTargetURL` in `kubernetes/apps/longhorn-system/longhorn/app/backuptarget.yaml` to that NFS URL, so the Flux-managed `default` target points at the restored store. Do not add it as a second target. Serving the restored store over NFS and restoring through a repointed `default` target are both **undrilled**. What was **drilled**, on 2026-10-05 ([restore drill](../../synology/offsite-backup/README.md#restore-drill-2026-10-05)): restoring one Kanidm volume's backups from B2 with Kopia, serving them over S3 from a throwaway versitygw pod, and rebuilding the volume as a disk image with the engine's `restore-to-file` command, outside Longhorn's control plane. A full restore of the store is **undrilled**. The media and manuals shares are not in the off-site copy and are gone with the NAS.

### What has been exercised

Do not read this section as a tested procedure. Each step below is tagged with one of these:

| Tag | Meaning |
|---|---|
| **Drilled** | Performed on this cluster, with the date and record linked. |
| **Undrilled** | Traced from the Taskfiles, scripts and manifests, or the intended procedure. Never run end to end. |

- The cluster was created on 2026-04-24. No rebuild from `task bootstrap:talos` and `task bootstrap:apps` is recorded since, and `scripts/bootstrap-apps.sh` has changed since then. Steps 1–7 are **undrilled**.
- One thing was **drilled**, on 2026-09-30 ([Visionect restore drill](visionect-migration.md#restore-drill-2026-09-30)): restoring the `visionect-db-1` and `visionect` backups into new single-replica volumes in a disposable namespace, and starting PostgreSQL on the restored database volume. Everything else, including the same restore for any other application, is **undrilled**.
- `task bootstrap:apps` could not finish on a clean cluster ([#758](https://github.com/kelchm/home-lab/issues/758)). The bootstrap now installs Multus itself. That was tested on 2026-10-05 on a disposable three-node Talos v1.13.11 cluster of PVE guests, which overrode the NIC selection, dropped the node patches and volume configs, skipped `sops-age` and never synced this repository. On this cluster's hardware it is **undrilled**; those checks and cold image-pull timing are acceptance criteria in [#224](https://github.com/kelchm/home-lab/issues/224). Step 5 says what to check if it stalls.
- Recovery time is unmeasured. Backups run daily at 07:00 UTC, so data loss is one day when the schedule was healthy and longer if backups were failing before the outage; record the timestamp of each backup you restore. The drill and its measurements are tracked in [#224](https://github.com/kelchm/home-lab/issues/224).

### Prerequisites

Confirm all of these before touching a node.

| Need | Where it comes from |
|---|---|
| `age.key` | 1Password item `k8s-prod-sops-age-key`. Save it as `age.key` in the repository root. It decrypts `talos/talsecret.sops.yaml` and every Secret in the repository. Without it nothing below works and every secret must be reissued. |
| This repository | `https://github.com/kelchm/home-lab`. Flux syncs `main` over anonymous HTTPS, so GitHub must be reachable and the repository public. |
| Tools and environment | `mise trust && mise install` installs the versions pinned in `.mise.toml`. Work in a shell with mise activated, or prefix each command with `mise exec --`: `.mise.toml` is what points `sops`, `kubectl`, `flux` and `talosctl` at `age.key`, `kubeconfig` and `talos/clusterconfig/talosconfig` in this repository. On macOS also `brew install bash`; `task bootstrap:apps` refuses to run without it. |
| Network | VLAN 30 on the node 1GbE ports, VLAN 25 on the 2.5GbE ports, and UniFi BGP, as in [architecture.md](../architecture.md#bootstrap-sequence) step 1. Node addresses and the API VIP `10.32.30.8` are static in `talos/talconfig.yaml`. |
| Matching hardware | `talos/talconfig.yaml` selects each node's install disk by serial number and its NIC by MAC address. If a disk or node was replaced, edit it first, together with that node's `talos/patches/<node>/network-extras.yaml` (primary and storage NIC MAC addresses and the storage bridge's interface name) and the `devices` pattern in the Cilium HelmRelease if the NIC is no longer named `eno*`. |
| NAS | Athena up at `10.32.25.5`, exporting `backups-k8s-prod` with the rules under [Backup target](#backup-target), plus the media and manuals shares. |
| Talos installer USB | `https://factory.talos.dev/image/<schematic-id>/<talos-version>/metal-amd64.iso`. The schematic ID is the last path segment of `talosImageURL` in `talos/talconfig.yaml`; the version is `talosVersion` in `talos/talenv.yaml`. |

### Procedure

Run every command from the repository root.

1. **Check the key.** `sops -d talos/talsecret.sops.yaml > /dev/null` must exit 0. *Undrilled.*
2. **Boot all three nodes from the USB installer** and leave them in maintenance mode. `talosctl -n 10.32.30.11 get disks --insecure` must answer for each node address. Installing wipes the whole system disk, including the Longhorn data partition, so do not continue on a node whose replicas you still need. *Undrilled.*
3. **`task bootstrap:talos`.** It generates the node configs from the committed `talsecret.sops.yaml`, applies them to the nodes in maintenance mode, bootstraps etcd, and writes `kubeconfig` to the repository root. The etcd and kubeconfig steps retry every 10 seconds without a limit; nodes need several minutes to install and reboot. Done when `kubectl get nodes` lists three nodes. They stay `NotReady` until step 5 installs the CNI. *Undrilled.*
4. **Hold back the paired applications before Flux first syncs.** Bambuddy's database refers to files on its `bambuddy` claim, and Visionect's server needs its `visionect` and `visionect-logs` claims. Each Cluster manifest restores its database from the CNPG archive the moment Flux first creates it, to the newest point unless `recoveryTarget` is set, and CloudNativePG reads that only at creation. Their volumes come back later, from a nightly Longhorn backup. So the database and file restore points must be chosen before either application is created. Push one commit to `main` that sets `spec.suspend: true` in `kubernetes/apps/printing/bambuddy/ks.yaml` and `kubernetes/apps/iot/visionect/ks.yaml`, and in `kubernetes/apps/printing/bambuddy/app/helmrelease.yaml` and `kubernetes/apps/iot/visionect/app/helmrelease.yaml`. Flux then creates neither Kustomization's objects: no database, no application and no empty claims. *Undrilled.*
5. **`task bootstrap:apps`.** Do not run `flux bootstrap`: Flux here is installed and owned by Flux Operator through the bootstrap Helmfile. The task waits up to 10 minutes for three nodes to register, creates the namespaces, applies the `sops-age` Secret to `flux-system`, applies the bootstrap CRDs, then installs Multus with its `cni-ready-untaint` DaemonSet, Cilium, CoreDNS, Spegel, cert-manager, Flux Operator and the FluxInstance in that order. It is safe to rerun after a failure. Done when it logs `The cluster is bootstrapped and Flux is syncing the Git repository`. *Undrilled.*

   **Multus step ([#758](https://github.com/kelchm/home-lab/issues/758)):** nodes register with the `node.multus.io/not-ready` taint and stay `NotReady` until Multus writes `/etc/cni/net.d/00-multus.conf`, so nothing that needs a pod network can start before Multus does. Three things in the repository cover that. The Helmfile applies `kubernetes/apps/kube-system/multus/app`, the same kustomization Flux owns afterwards, before it installs Cilium: the Multus pods are what create `/etc/cni/multus/net.d`, which the Cilium agents mount and the kubelet cannot create. `cilium-operator` tolerates the taint, so the Cilium release can finish. Before CoreDNS the Helmfile waits up to 10 minutes each for the `cni-ready-untaint` pods to start and for every node to be `Ready`. The `kube-multus-ds` pods exit and restart until Cilium has written its config, and the `cilium` pods log `mkdir /etc/cni/multus: read-only file system` until a Multus pod has started on their node; both are expected and clear on retry, so the Cilium step is allowed 10 minutes. *Tested on a disposable PVE cluster on 2026-10-05; undrilled on this hardware.*

   If the task stalls, find what is `Pending` or stuck in `Init` and which taints remain, then read the pod's events and the node's `Ready` message for the cause. The cases seen or expected: `cilium` pods still in `Init` after several minutes with `mkdir /etc/cni/multus: read-only file system` (Multus has not started on that node; check the `kube-multus-ds` pods, and apply the kustomization by hand if they are missing); `cilium-operator` Pending on the Multus taint (the toleration is not in effect); nodes `Ready` but still tainted `node.multus.io/not-ready` with `coredns` Pending (`cni-ready-untaint` has not started or cannot patch the node; check its pods and logs). Rerun the task afterwards. *Undrilled.*

   ```sh
   kubectl -n kube-system get pods -o wide
   kubectl get nodes -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,TAINTS:.spec.taints[*].key'
   kubectl describe node <node> | grep -A8 Conditions
   kubectl apply --server-side --force-conflicts -k kubernetes/apps/kube-system/multus/app
   ```
6. **Wait for Flux and Longhorn.** *Undrilled.*

   ```sh
   flux get kustomizations -A
   kubectl -n longhorn-system get backuptargets.longhorn.io default
   ```

   Continue when the `longhorn` Kustomization is Ready and the BackupTarget shows `AVAILABLE true`. Flux has by now started every application except the two held in step 4 on a new, empty volume. That is expected; step 8 replaces those volumes.
7. **Stage the paired applications**, one at a time, before releasing them. *Undrilled*, including CloudNativePG recovery to a `targetTime` taken from a Longhorn backup.
   1. **Choose the restore points.** For each of `printing/bambuddy`, `iot/visionect` and `iot/visionect-logs`, find the backup to restore as in [Restore one claim](#restore-one-claim) step 1. Take Visionect's two claims from the same night. Record each backup's name, URL and `snapshotCreatedAt`, the moment its data was captured; `backupCreatedAt` is when the upload finished, which can be minutes later:

      ```sh
      kubectl -n longhorn-system get backups.longhorn.io <backup-name> -o jsonpath='{.status.snapshotCreatedAt}{"\n"}'
      ```

   2. **Set the recovery target and create the database only.** In `kubernetes/apps/printing/bambuddy/app/cluster.yaml`, add `recoveryTarget.targetTime` under `bootstrap.recovery`, set to the `bambuddy` backup's `snapshotCreatedAt` in RFC 3339 form, for example `"2026-11-06T07:00:41Z"`. Remove `suspend: true` from the application's `ks.yaml` and leave it on its HelmRelease. Visionect needs no target unless its files must match a past database state; without one it takes the newest archived state. To match them, set `recoveryTarget.targetTime` the same way in `kubernetes/apps/iot/visionect/app/cluster.yaml` from the `visionect` backup's `snapshotCreatedAt`, and check and release it as below. Push. Flux creates the Cluster, which recovers to the target, while the suspended HelmRelease starts no application and creates no claim. The Kustomization's HelmRelease health check fails until release; that is expected.
   3. **Restore the claims** with [Restore one claim](#restore-one-claim) step 3, including the Helm adoption metadata, and wait for each restore with only the first command of step 4. Do not scale or resume anything yet: the release step below starts the application. Skip step 2: no claim exists yet.
   4. **Check the pair before the application starts.** The Cluster is healthy (`kubectl -n printing get clusters.postgresql.cnpg.io bambuddy-db`) and its recovery stopped at the target, not at the end of the archive: the newest timestamps in the application's tables are no later than the target. Mount the restored claim from a debug pod as in [Inspect](#2-inspect) and check that the files of the newest archive records in the database are present. Do not drop the target to get past a failure. If the recovery job fails because no completed base backup predates the target, an earlier target cannot help: choose a newer Longhorn backup taken after a completed base backup, or use the Longhorn database fallback under [Restore order](#restore-order) group 2. If PostgreSQL reports that recovery ended before the target, the archived WAL stops short of it: choose an earlier Longhorn backup.
   5. **Release.** Push one commit that removes `suspend: true` from the HelmRelease and removes `recoveryTarget` from `cluster.yaml`. CloudNativePG reads the target only at creation, so removing it changes nothing on the running Cluster; leaving it would send a future rebuild back to this moment. Check that the application is Ready and shows its old data.
8. **Restore every other claim** in the order under [Restore order](#restore-order), using [Restore one claim](#restore-one-claim). *Undrilled.*
9. **Verify.** Every Kustomization is Ready, each restored application shows its old data, and after the next 07:00 UTC run every restored volume has a new backup (`kubectl -n longhorn-system get volumes.longhorn.io -o custom-columns='PVC:.status.kubernetesStatus.pvcName,LAST:.status.lastBackupAt'`). `ai/lemon-manuals-mcp` `search` is excluded from backups and stays empty. *Undrilled.*

### Restore one claim

**Undrilled** as written. The 2026-09-30 drill used the same three kinds of object, but with one replica, a new volume name, a disposable namespace and a recurring-job label that kept the volume out of backups. Two replicas, reusing the backup volume's name, replacing a claim Flux already created, and the Helm ownership metadata have not been exercised.

1. List what the backup target holds. A backup volume is named after the old cluster's PV, so match on the claim it recorded:

   ```sh
   kubectl -n longhorn-system get backupvolumes.longhorn.io -o json | jq -r '
     .items[] | [((.status.labels.KubernetesStatus // "{}") | fromjson | "\(.namespace)/\(.pvcName)"),
     .spec.volumeName, .status.lastBackupAt, .status.size] | @tsv' | sort
   ```

   A claim can appear more than once. Rows whose volume name is a PV in the rebuilt cluster (`kubectl get pv`) are the new, empty volumes, which join the daily backup as soon as they exist: never restore from those. Of the remaining rows, take the one with the newest backup from before the outage; older ones belong to volumes deleted earlier. Note the volume name and size in bytes, then get the backup URL and pick a backup created before the outage:

   ```sh
   kubectl -n longhorn-system get backups.longhorn.io -l backup-volume=<backup-volume-name> \
     --sort-by=.status.backupCreatedAt -o custom-columns='CREATED:.status.backupCreatedAt,URL:.status.url'
   ```

2. Stop the application and remove its empty claim. Deleting the claim deletes the empty volume.

   ```sh
   flux suspend kustomization <app> -n <ns>
   flux suspend helmrelease <app> -n <ns>   # skip if the application has no HelmRelease
   kubectl -n <ns> scale <deployment|statefulset>/<name> --replicas=0
   kubectl -n <ns> get pods   # wait until the application's pods are gone
   kubectl -n <ns> delete pvc <claim>
   ```

   Note the replica count before scaling down. A workload owned by an operator (Kanidm, and the VictoriaMetrics `VMSingle` and `VMAlertmanager`) is scaled through its custom resource instead; the operator reverts a direct scale. VictoriaLogs is a plain StatefulSet.

3. Create the volume, PV and claim. Naming the volume after the backup volume keeps new backups in the existing backup history.

   ```yaml
   apiVersion: longhorn.io/v1beta2
   kind: Volume
   metadata:
     name: <backup-volume-name>
     namespace: longhorn-system
   spec:
     fromBackup: "<backup-url>"
     size: "<size-in-bytes>"
     numberOfReplicas: 2
     frontend: blockdev
   ---
   apiVersion: v1
   kind: PersistentVolume
   metadata:
     name: <backup-volume-name>
   spec:
     capacity:
       storage: <claim-size>
     accessModes: [ReadWriteOnce]
     persistentVolumeReclaimPolicy: Retain
     storageClassName: longhorn
     claimRef:
       namespace: <ns>
       name: <claim>
     csi:
       driver: driver.longhorn.io
       fsType: ext4
       volumeHandle: <backup-volume-name>
   ---
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: <claim>
     namespace: <ns>
   spec:
     accessModes: [ReadWriteOnce]
     storageClassName: longhorn
     volumeName: <backup-volume-name>
     resources:
       requests:
         storage: <claim-size>
   ```

   For a claim that a Helm chart creates (every `media`, `ai` and `iot` application claim except `broadsheet`, plus Grafana and Bambuddy's archive), also add the label `app.kubernetes.io/managed-by: Helm` and the annotations `meta.helm.sh/release-name: <release>` and `meta.helm.sh/release-namespace: <ns>`, so the release can adopt it.

4. Wait for the restore, then start the application:

   ```sh
   kubectl -n longhorn-system get volumes.longhorn.io <backup-volume-name> -w \
     -o custom-columns='STATE:.status.state,RESTORING:.status.restoreRequired'   # until detached and false
   kubectl -n <ns> scale <deployment|statefulset>/<name> --replicas=<previous-count>
   flux resume helmrelease <app> -n <ns>
   flux resume kustomization <app> -n <ns>
   kubectl -n <ns> get pods   # until Running and Ready
   ```

   Scale back explicitly: resuming does not undo the manual scale, because the Helm release itself has not changed.

### Restore order

Claims and sizes are the 2026-10-04 inventory, with the CNPG row updated from the 2026-10-29 read-back. Restore each group before the one below it.

| Order | Application | Claims | Notes |
|---|---|---|---|
| 1 | `identity/kanidm` | `kanidm-data-kanidm-default-0`, `-1`, `-2` (2Gi each) | Sign-in for every other application. Follow [All claims lost](kanidm-restore.md#all-claims-lost): restore replica 0's claim only, then let replicas 1 and 2 start empty and reload from it. Restoring all three from their own backups would let the replicas replicate inconsistent states into each other. **Undrilled.** |
| 2 | CNPG databases | `iot/visionect-db-*` (30Gi each), `printing/bambuddy-db-*` (20Gi each); inventory actual PVC UIDs | Procedure steps 4 and 7 hold the consumers (`visionect`, `bambuddy`) back until their claims are restored; otherwise they run against the recovered database with empty application volumes. Where the store has a completed base backup, do not restore these claims: each Cluster manifest bootstraps from the store on Athena, restoring the newest base backup and replaying archived WAL; see [cnpg-backup-restore](cnpg-backup-restore.md#2-after-the-cluster-or-the-whole-kubernetes-cluster-is-rebuilt) (**drilled** on scratch clusters only). It needs `s3-storage` on Athena, or a served copy of its share when Athena is lost too. **Before the first completed base backup, including during activation, that recovery path is unavailable:** WAL alone cannot bootstrap a database. Use the Longhorn fallback that follows, with `bootstrap.initdb` replacing `bootstrap.recovery` for the new empty destination before loading its recovered data. After validating that data, take a completed manual base backup and restore the permanent `bootstrap.recovery` stanza pointing to its verified archive; do not leave a future rebuild using `initdb`. The Longhorn backups remain an independent copy. With HA, backups exist for both instance volumes and do not reliably record which was primary: inspect the recovered PGDATA and use a primary copy without `standby.signal`; do not guess from its instance number or start an isolated standby expecting it to promote itself. If zero or multiple copies appear primary, or their backup-time roles and intended recovery point cannot be established, stop with consumers still off: absence of `standby.signal` alone does not select the authoritative copy. Prefer native recovery; otherwise reconcile the copies' timelines/checkpoints and capture history before choosing one. Standby-volume recovery is undrilled; prefer the verified CNPG-native path. Restore the selected primary backup to a scratch claim, start PostgreSQL on it as in the [2026-09-30 drill](visionect-migration.md#restore-drill-2026-09-30) (**drilled** to that point for `visionect-db` only), `pg_dump --format=custom` each application database, and load it with the `pg_restore` invocation in [visionect-migration](visionect-migration.md#phase-3-cold-cutover) (**undrilled**). |
| 3 | `iot/visionect`, `printing/bambuddy` | `visionect` (5Gi), `visionect-logs` (4Gi), `bambuddy` (100Gi) | Restored in procedure step 7, which pairs Bambuddy's archive with its database recovery target before either starts; see also [bambuddy-bootstrap](bambuddy-bootstrap.md#backup-and-restore-drill). |
| 4 | `iot/broadsheet` | `broadsheet` (20Gi) | Declared in Git, not by Helm. |
| 5 | `media` | `prowlarr` first, then `sonarr`, `radarr`, `lidarr`, `qbittorrent`, `sabnzbd`, `bazarr`, `seerr-config` (5Gi each), `jellyfin` (20Gi) | Needs the NAS media share. |
| 6 | `ai/digikey-mcp` | `digikey-mcp` (100Mi) | |
| 7 | `observability` | `vmsingle-victoria-metrics-k8s-stack` (50Gi), `server-volume-victoria-logs-single-server-0` (30Gi), the `vmalertmanager` claim (1Gi), `grafana` (10Gi) | History only. Alerting and dashboards work on the empty volumes, so this can wait or be skipped. |

Not restored: `ai/lemon-manuals-mcp` `search`, which is excluded from backups and rebuilds itself.

## Known gotchas

- **NFS server unavailable at backup time.** The job will fail; the next scheduled run retries. No automatic catch-up — if NFS was down for 3 days, you have a 3-day gap. The retain count is unaffected.
- **`retain` deletes the *backup*, not the local snapshot.** Local snapshots accumulate independently per Longhorn's snapshot retention. Keep an eye on Longhorn UI → Volume → Snapshots if disk pressure shows up.
- **Changing the backup target.** The target is the Flux-managed `default` BackupTarget CR in `backuptarget.yaml`, not the chart's `defaultSettings.backupTarget`, which Longhorn 1.7 and later ignore on an installed cluster. Changing its `backupTargetURL` is non-destructive: existing backups stay on the old store and new ones go to the new store. Longhorn re-syncs its BackupVolume and Backup records against the new store, so the backup list in the UI flaps briefly and then shows only what the new store holds.
- **Re-bootstrapped cluster sees old Backup Volumes.** This is the *point* — it's how DR works — but it can be confusing during routine drills if you delete a PVC expecting its backup history to disappear. The BackupVolume CR persists in the cluster's etcd until manually removed via UI/CRD.
- **Synology snapshot replication on the same volume is not a substitute.** It protects against bit rot on the NAS but not against Longhorn-side corruption (a bad app write that overwrites the volume gets faithfully snapshotted by both Longhorn and DSM). The independence of the backup chain is the value.
