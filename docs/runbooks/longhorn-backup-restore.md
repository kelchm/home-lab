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
- Synology NFS export: scoped to `10.32.25.0/24`, all-users squashed to admin (Longhorn writes as root in-pod; squash maps to NAS admin UID, share-level ACL restricts access to one dedicated `k8s-backup` user).

## Schedule and retention

Defined in `kubernetes/apps/longhorn-system/longhorn/app/recurringjobs.yaml`:

| Job | Cron | Retain | Group |
|---|---|---|---|
| `backup-daily` | `0 3 * * *` (03:00) | 7 | default |
| `backup-weekly` | `0 4 * * 0` (Sunday 04:00) | 4 | default |

`retain` is a **count**, not a time window. If a job is unable to run for several days the count doesn't reset; you just have a sparser series until the schedule catches up.

## Group membership and opt-out

Longhorn's `default` group is implicit: a volume with **no** recurring-job labels automatically receives `default`'s jobs. Adding *any* recurring-job label removes it from `default` — there is no explicit "disable" label.

To opt a volume out of default backups:

1. **Cleanest:** create a no-op group and label the PVC into it.

   ```yaml
   # add to the PVC manifest (or label the live PVC)
   metadata:
     labels:
       recurring-job-group.longhorn.io/no-backup: enabled
   ```

   No `RecurringJob` CR references `no-backup`, so the volume gets nothing. Document in the chart values why it's excluded (e.g. ephemeral metric store).

2. **Quick-and-dirty:** label the PVC with any individual job that doesn't apply (e.g. a hypothetical `recurring-job.longhorn.io/snapshot-only: enabled`). The label removes it from `default`; the referenced job not existing is fine.

To put it back: `kubectl label pvc <name> recurring-job-group.longhorn.io/no-backup-` (trailing hyphen removes the label).

## Routine monitoring

Until OpenObserve / VictoriaMetrics alerting is wired up:

- **Longhorn UI → Backup → Backup Volume** — every active PV should show backups within the last ~24h.
- `kubectl -n longhorn-system get backups.longhorn.io --sort-by=.metadata.creationTimestamp` — last few entries should be recent.
- Free space on the NAS share — DSM → Storage Manager → Volume 1 utilization.

When metrics land, alert on:

- `longhorn_volume_actual_size_bytes` for the BackupTarget (treat the BackupTarget volume as a regular monitored object — it shows up in Longhorn metrics)
- Longhorn manager logs containing `backup failed` / `BackupTarget unavailable`
- `RecurringJob` last-success timestamp staleness > 30 hours

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
