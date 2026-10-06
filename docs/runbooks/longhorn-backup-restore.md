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

## Disaster recovery: rebuild the cluster and restore data

Use this when all three nodes are lost or wiped and the NAS backup share is intact. It rebuilds Talos and Flux from this repository, then restores volume data from the Longhorn backups.

**If the NAS is also lost,** the Longhorn backup store has an off-site copy in Backblaze B2, taken daily at 10:00 UTC. Restore it first by following [Off-site backup from Athena](../../synology/offsite-backup/README.md#restore) and serve the restored store over NFS. Before step 5, change `backupTargetURL` in `kubernetes/apps/longhorn-system/longhorn/app/backuptarget.yaml` on `main` to that NFS URL, so the Flux-managed `default` target points at the restored store. Do not add it as a second target. One volume has been restored this way; a full restore has not. The media and manuals shares are not in the off-site copy and are gone with the NAS.

### What has been exercised

Do not read this section as a tested procedure. Each step below is tagged with one of these:

| Tag | Meaning |
|---|---|
| **Drilled** | Performed on this cluster, with the date and record linked. |
| **Undrilled** | Traced from the Taskfiles, scripts and manifests, or the intended procedure. Never run end to end. |

- The cluster was created on 2026-04-24. No rebuild from `task bootstrap:talos` and `task bootstrap:apps` is recorded since, and `scripts/bootstrap-apps.sh` has changed since then. Steps 1–5 are **undrilled**.
- One thing was **drilled**, on 2026-09-30 ([Visionect restore drill](visionect-migration.md#restore-drill-2026-09-30)): restoring the `visionect-db-1` and `visionect` backups into new single-replica volumes in a disposable namespace, and starting PostgreSQL on the restored database volume. Everything else, including the same restore for any other application, is **undrilled**.
- `task bootstrap:apps` was traced, not observed, to deadlock on a clean cluster behind the `node.multus.io/not-ready` taint ([#758](https://github.com/kelchm/home-lab/issues/758)). The bootstrap now installs Multus itself; that fix is **undrilled**, and step 4 says what to check if it still stalls.
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
3. **`task bootstrap:talos`.** It generates the node configs from the committed `talsecret.sops.yaml`, applies them to the nodes in maintenance mode, bootstraps etcd, and writes `kubeconfig` to the repository root. The etcd and kubeconfig steps retry every 10 seconds without a limit; nodes need several minutes to install and reboot. Done when `kubectl get nodes` lists three nodes. They stay `NotReady` until step 4 installs the CNI. *Undrilled.*
4. **`task bootstrap:apps`.** Do not run `flux bootstrap`: Flux here is installed and owned by Flux Operator through the bootstrap Helmfile. The task waits up to 10 minutes for three nodes to register, creates the namespaces, applies the `sops-age` Secret to `flux-system`, applies the bootstrap CRDs, then installs Cilium, Multus with its `cni-ready-untaint` DaemonSet, CoreDNS, Spegel, cert-manager, Flux Operator and the FluxInstance in that order. It is safe to rerun after a failure. Done when it logs `The cluster is bootstrapped and Flux is syncing the Git repository`. *Undrilled.*

   **Multus step ([#758](https://github.com/kelchm/home-lab/issues/758), traced, not observed):** nodes register with the `node.multus.io/not-ready` taint and stay `NotReady` until Multus writes `/etc/cni/net.d/00-multus.conf`, so nothing that needs a pod network can start before Multus does. Two things in the repository cover that: `cilium-operator` tolerates the taint, so the Cilium release can finish, and the Helmfile applies `kubernetes/apps/kube-system/multus/app`, the same kustomization Flux owns afterwards, before CoreDNS, then waits up to 10 minutes each for the `cni-ready-untaint` pods to start and for every node to be `Ready`. *Undrilled.*

   If the task still stalls, find what is `Pending` and which taints remain, then read the pod's events and the node's `Ready` message for the cause. The cases this change is meant to cover: `cilium-operator` Pending on the Multus taint (the toleration is not in effect); nodes `NotReady` for a missing CNI config with no `kube-multus-ds` pods (the hook did not apply the kustomization; apply it by hand); nodes `Ready` but still tainted `node.multus.io/not-ready` with `coredns` Pending (`cni-ready-untaint` has not started or cannot patch the node; check its pods and logs). Rerun the task afterwards. *Undrilled.*

   ```sh
   kubectl -n kube-system get pods -o wide
   kubectl get nodes -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,TAINTS:.spec.taints[*].key'
   kubectl describe node <node> | grep -A8 Conditions
   kubectl apply --server-side --force-conflicts -k kubernetes/apps/kube-system/multus/app
   ```
5. **Wait for Flux and Longhorn.** *Undrilled.*

   ```sh
   flux get kustomizations -A
   kubectl -n longhorn-system get backuptargets.longhorn.io default
   ```

   Continue when the `longhorn` Kustomization is Ready and the BackupTarget shows `AVAILABLE true`. Flux has by now started every application on a new, empty volume. That is expected; step 6 replaces those volumes.
6. **Restore each claim** in the order under [Restore order](#restore-order), using [Restore one claim](#restore-one-claim). *Undrilled.*
7. **Verify.** Every Kustomization is Ready, each restored application shows its old data, and after the next 07:00 UTC run every restored volume has a new backup (`kubectl -n longhorn-system get volumes.longhorn.io -o custom-columns='PVC:.status.kubernetesStatus.pvcName,LAST:.status.lastBackupAt'`). `ai/lemon-manuals-mcp` `search` is excluded from backups and stays empty. *Undrilled.*

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

Claims and sizes are the live set on 2026-10-04. Restore each group before the one below it.

| Order | Application | Claims | Notes |
|---|---|---|---|
| 1 | `identity/kanidm` | `kanidm-data-kanidm-default-0`, `-1`, `-2` (2Gi each) | Sign-in for every other application. Follow [kanidm-restore](kanidm-restore.md#pvc-loss). Its commands scale the replica group to zero and Flux returns it to the declared count; it was written when Kanidm had one replica, and it now runs three. Restoring three replicas from separate backups is **undrilled**. |
| 2 | CNPG databases | `iot/visionect-db-1` (30Gi), `printing/bambuddy-db-2` (5Gi) | **First stop the consumer** (`visionect`, `bambuddy`) as in [Restore one claim](#restore-one-claim) step 2, and keep it stopped until group 3 is done; otherwise it writes to the empty database during the import. Do not use Restore one claim for the database itself: the new cluster's instance has its own claim and CNPG will not adopt a foreign one. Restore the backup to a scratch claim, start PostgreSQL on it as in the [2026-09-30 drill](visionect-migration.md#restore-drill-2026-09-30) (**drilled** to that point for `visionect-db` only), `pg_dump --format=custom` each application database, and load it into the new primary with the `pg_restore` invocation in [visionect-migration](visionect-migration.md#phase-3-cold-cutover) (**undrilled**). A CNPG-native backup waits on [#297](https://github.com/kelchm/home-lab/issues/297). |
| 3 | `iot/visionect`, `printing/bambuddy` | `visionect` (5Gi), `visionect-logs` (4Gi), `bambuddy` (100Gi) | Restore these claims, then start the consumers stopped in group 2. Take Bambuddy's archive and database from the same night; see [bambuddy-bootstrap](bambuddy-bootstrap.md#backup-and-restore-drill). |
| 4 | `iot/broadsheet` | `broadsheet` (20Gi) | Declared in Git, not by Helm. |
| 5 | `media` | `prowlarr` first, then `sonarr`, `radarr`, `lidarr`, `qbittorrent`, `sabnzbd`, `bazarr`, `seerr-config` (5Gi each), `jellyfin` (20Gi) | Needs the NAS media share. |
| 6 | `ai/digikey-mcp` | `digikey-mcp` (100Mi) | |
| 7 | `observability` | `vmsingle-victoria-metrics-k8s-stack` (50Gi), `server-volume-victoria-logs-single-server-0` (30Gi), the `vmalertmanager` claim (1Gi), `grafana` (10Gi) | History only. Alerting and dashboards work on the empty volumes, so this can wait or be skipped. |

Not restored: `ai/lemon-manuals-mcp` `search`, which is excluded from backups and rebuilds itself.

## Known gotchas

- **NFS server unavailable at backup time.** The job will fail; the next scheduled run retries. No automatic catch-up — if NFS was down for 3 days, you have a 3-day gap. The retain count is unaffected.
- **`retain` deletes the *backup*, not the local snapshot.** Local snapshots accumulate independently per Longhorn's snapshot retention. Keep an eye on Longhorn UI → Volume → Snapshots if disk pressure shows up.
- **Cluster-scoped BackupTarget setting.** Changing `defaultSettings.backupTarget` in the HelmRelease is non-destructive (existing backups stay where they are; future backups go to the new target), but every existing volume's `BackupVolume` reconciler has to re-list against the new endpoint. Brief UI flap is normal.
- **Re-bootstrapped cluster sees old Backup Volumes.** This is the *point* — it's how DR works — but it can be confusing during routine drills if you delete a PVC expecting its backup history to disappear. The BackupVolume CR persists in the cluster's etcd until manually removed via UI/CRD.
- **Synology snapshot replication on the same volume is not a substitute.** It protects against bit rot on the NAS but not against Longhorn-side corruption (a bad app write that overwrites the volume gets faithfully snapshotted by both Longhorn and DSM). The independence of the backup chain is the value.
