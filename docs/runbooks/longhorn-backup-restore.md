# Longhorn Backup & Restore

Runbook for the Longhorn-native backup MVP: scheduled backups to Synology NFS, restoring an individual PV, and the broader disaster-recovery shape.

## What this protects

- **PV data** — every Longhorn volume in the `default` recurring-job group, captured nightly to NFS.

## What this does NOT protect

- **Kubernetes manifests, Secrets, CRDs, HelmReleases.** Recovery assumes the cluster is rebuilt from `flux bootstrap` against this git repo.
- **The sops `age.key`.** Without it, sops-encrypted Secrets in this repo cannot be decrypted. Stored in 1Password (`sops age key — home-lab`); a copy on a separate device is the recovery path. **If the 1Password entry and your laptop both burn, the cluster's secret state is lost.**
- **Anything not in git and not Longhorn-resident.** Manually-applied resources, drift in cluster-scoped Longhorn settings, cert-manager Order/Challenge state, etc.
- **NFS-backed PVs (csi-driver-nfs).** Bulk media on the Synology is the Synology's backup problem, not Longhorn's.

If any of those gaps grow load-bearing, layer Velero on top — the BackupTarget already has a `longhorn/` subdir, so Velero gets its own sibling subdir without disturbing this setup.

## Backup target

- BackupTarget URL: `nfs://10.32.25.5:/volume1/backups-k8s-prod/longhorn`
- Path on NAS: `/volume1/backups-k8s-prod/longhorn/` (Longhorn writes `backupstore/{volumes,backups}/...` underneath)
- Network path: instance-manager pods reach `10.32.25.5` via the `lhnet1` storage-network attachment (same /24, kernel routes out the secondary interface). Backup transfer rides 2.5GbE, not the 1GbE node NIC.
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

Prometheus and vmalert evaluate the Longhorn rules in `kubernetes/apps/longhorn-system/longhorn/app/alerts.yaml`; Alertmanager delivers warnings and critical alerts through the `k8s-prod Alerts` Pushover application. The rules cover BackupTarget availability, cluster-wide backup staleness, per-volume stale or never-completed backups, volume robustness, Longhorn node readiness, disk capacity/schedulability, and missing metric coverage. See [alerting](alerting.md) for ownership, silence policy, and end-to-end tests.

Use these checks to investigate a notification or audit the automation:

- **Longhorn UI → Backup → Backup Volume** — every active PV except those listed under "Currently excluded" should show backups within the last ~24h.
- `kubectl -n longhorn-system get backuptargets.longhorn.io default -o yaml` — `status.available` must be `true` and `status.lastSyncedAt` recent.
- `kubectl -n longhorn-system get backups.longhorn.io --sort-by=.metadata.creationTimestamp` — last few entries should be recent.
- `longhorn_backup_target_available{backup_target="default"}` — must be `1`.
- `longhorn_volume_last_backup_at` — drives the 26-hour cluster-wide and 30-hour per-volume thresholds.
- Longhorn manager logs containing `backup failed` or `BackupTarget unavailable` — use when the CR status and timestamp alerts fire.
- Free space on the NAS share — DSM → Storage Manager → Volume 1 utilization.

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

### 1. Decide the restore shape

Two paths, pick based on what the workload looks like:

- **Restore in-place over the existing PVC** — workload stays on the same PVC name. Requires scaling the workload to 0 first (PV must be detached). Best when the app's manifests are immutable and you want to keep its identity.
- **Restore as a new PVC** — workload is reconfigured to point at a new PVC name, or you compare before promoting. Safer for first-time drills; doesn't touch the live PVC.

### 2A. Restore in-place

```sh
# Scale the workload to 0 (StatefulSet, Deployment, whatever owns the PVC)
kubectl -n <ns> scale statefulset/<name> --replicas=0
# wait for pods gone
kubectl -n <ns> wait --for=delete pod -l app=<label> --timeout=120s

# In Longhorn UI: Backup → select backup → Restore Latest Backup
# - "Use Previous Name": YES (matches the existing PV's name)
# - This wipes the existing volume's content and replaces with the backup
```

Then scale the workload back up. New pods will mount the restored data.

### 2B. Restore as a new PVC (Recommended for first drill)

The cleanest UI path is to use a StorageClass with the `fromBackup` parameter. Easier: declare a fresh PVC that references the backup URL.

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <original-name>-restore-test
  namespace: <ns>
  annotations:
    # Backup URL from Longhorn UI (Backup → select → "..." → Get URL),
    # shape: nfs://<server>:<path>?backup=<backup-name>&volume=<volume-name>
    longhorn.io/from-backup: "<backup-url>"
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: longhorn
  resources:
    requests:
      storage: <same-as-original>
```

Apply, wait for `Bound`, then mount it from a debug pod and inspect:

```sh
kubectl -n <ns> run restore-inspect --rm -it --restart=Never \
  --image=alpine \
  --overrides='{"spec":{"containers":[{"name":"x","image":"alpine","command":["sh"],"stdin":true,"tty":true,"volumeMounts":[{"name":"d","mountPath":"/data"}]}],"volumes":[{"name":"d","persistentVolumeClaim":{"claimName":"<original-name>-restore-test"}}]}}'
```

If the data looks right: either swap the workload's PVC reference to the restore PVC (cleanest GitOps), or stop the workload and promote the restore PVC over the original by deleting the original and renaming.

### 3. Verify

- Workload comes back healthy
- Application-level smoke test (DB query, dashboard load, etc.)
- New backup runs that night and shows up alongside the restored volume

### 4. Cleanup

- Delete any `*-restore-test` PVCs once the real workload is settled
- If you scaled down a workload, ensure it scales back to declared replicas

## Disaster recovery: full-cluster sketch

This is the "all three nodes are gone" path. Not a drill yet — write the full version once tested. Outline:

1. **Reprovision Talos cluster** per `docs/architecture.md` bootstrap sequence. Cluster comes up empty.
2. **Restore `age.key`** to the operator laptop from 1Password. `flux bootstrap` against this repo. Flux pulls in CRDs, namespaces, HelmReleases, sops-encrypted Secrets (now decryptable).
3. **Wait for Longhorn to install and the BackupTarget to reconcile.** Cluster has no volumes yet. The BackupTarget is shared, so the Backup Volume list in Longhorn UI immediately shows everything from the previous cluster's history.
4. **For each app** (in dependency order — databases before consumers):
   - Scale the workload to 0 (Flux already created it; it's pending a PVC)
   - Use the "restore as new PVC" pattern above to materialize the volume from backup
   - Patch or recreate the workload's PVC reference to the restored volume's PV
   - Scale back up
5. **Verify and re-snapshot.** The next night's backup confirms the BackupTarget round-trip is healthy.

The fragile step is #4 — it's per-app and benefits from a per-app cheat sheet. Build one as you do the first real restore drill against a low-stakes app (Grafana is a good candidate: small PVC, restoration is visually obvious).

## Known gotchas

- **NFS server unavailable at backup time.** The job will fail; the next scheduled run retries. No automatic catch-up — if NFS was down for 3 days, you have a 3-day gap. The retain count is unaffected.
- **`retain` deletes the *backup*, not the local snapshot.** Local snapshots accumulate independently per Longhorn's snapshot retention. Keep an eye on Longhorn UI → Volume → Snapshots if disk pressure shows up.
- **Cluster-scoped BackupTarget setting.** Changing `defaultSettings.backupTarget` in the HelmRelease is non-destructive (existing backups stay where they are; future backups go to the new target), but every existing volume's `BackupVolume` reconciler has to re-list against the new endpoint. Brief UI flap is normal.
- **Re-bootstrapped cluster sees old Backup Volumes.** This is the *point* — it's how DR works — but it can be confusing during routine drills if you delete a PVC expecting its backup history to disappear. The BackupVolume CR persists in the cluster's etcd until manually removed via UI/CRD.
- **Synology snapshot replication on the same volume is not a substitute.** It protects against bit rot on the NAS but not against Longhorn-side corruption (a bad app write that overwrites the volume gets faithfully snapshotted by both Longhorn and DSM). The independence of the backup chain is the value.
