# CloudNativePG backup and restore

How the two PostgreSQL clusters, `iot/visionect-db` and `printing/bambuddy-db`, are backed up, what to do when an alert fires, and how to restore. The design decisions are in [home-lab#224](https://github.com/kelchm/home-lab/issues/224).

Last verified acceptance, 2026-10-29 UTC: **both live database backup paths and the [real-data alongside restores](#real-data-alongside-restores-2026-10-29) accepted.** After NAS foundation acceptance (#1077), #816 merged as `653afc37` with Bambuddy suspended. Visionect’s backup and WAL checks passed first. #1095 (`ce7d9079`) then added all-IPv6 login rejection by reload; after confirming Visionect’s rejection, Bambuddy resumed and passed all checks. Both have the plugin sidecar and storage interface, completed manual backups, nonzero last-backup metrics, advancing WAL objects on Athena and at least 15 minutes of WAL-queue observation with a maximum of 0. Both applications were healthy at acceptance, and their pods were not restarted. CloudNativePG is 1.30.1, plugin 0.15.1 and versitygw 1.8.0. See [What has been exercised](#what-has-been-exercised) for the scope and remaining limits.

| Live database | Storage address | Accepted manual backup | Completed UTC | Compressed base object |
|---|---|---|---|---|
| Visionect, PostgreSQL 17.11 | `10.32.25.160` | `visionect-db-manual-tshbc`, ID `20261029T013554` | 01:39:38 | 300,539,253 bytes |
| Bambuddy, PostgreSQL 18.6 | `10.32.25.161` | `bambuddy-db-manual-dk4rs`, ID `20261029T020738` | 02:08:01 | 17,238,263 bytes |

The initial `immediate: true` requests failed on the old pods before their sidecars were available; the accepted manual backups ran after Ready. Both databases rejected no-credential probes to their own storage IPv4 and IPv6 link-local addresses; the rules had no parse errors, Visionect logged its reload, and neither had IPv6 sessions. This verifies the loaded HBA decision, not an external L2 probe. The four backup-related alerts are loaded and healthy in vmalert; their fault conditions were not deliberately induced.

## First normal object-storage snapshot, 2026-10-29

No completed Kopia snapshot of `/sources/object-storage` was observed at the earlier database sign-off. Subsequent read-only verification at 11:22:58–11:25:05 UTC accepted the first normal 10:00 UTC run for this source: Longhorn completed first at 10:19:50 UTC, then object-storage ran from `2026-10-29T10:19:50.637317066Z` to `2026-10-29T10:32:48.981769139Z`. The manifest and normal-run log identify snapshot `7f745fefefe1b8424ce2c0c76bbe5da5`, immutable root catalog `k75f8b039bbe6a328c7375ae05cf3b0fa`, source `offsite@athena:/sources/object-storage`, with 1,397 files, 655,641,874 bytes and 601 directories; reported errors, ignored errors, failed entries and exclusions were all zero.

Catalog traversal of that exact immutable root found both accepted base archives and these selected WAL objects. Paths are relative to the snapshot root:

| Catalog path | Bytes | Catalog mtime UTC |
|---|---:|---|
| `data/k8s-prod-visionect-db/visionect-db-pg17/base/20261029T013554/data.tar.gz` | 300,539,253 | 2026-10-29 01:39:42 |
| `data/k8s-prod-bambuddy-db/bambuddy-db-pg18/base/20261029T020738/data.tar.gz` | 17,238,263 | 2026-10-29 02:08:04 |
| `data/k8s-prod-visionect-db/visionect-db-pg17/wals/000000040000004C/000000040000004C000000C5.gz` | 59,212 | 2026-10-29 10:18:52 |
| `data/k8s-prod-bambuddy-db/bambuddy-db-pg18/wals/0000000600000054/000000060000005400000024.gz` | 52,352 | 2026-10-29 10:15:26 |

The two base directories also contain `backup.info`, 1,450 bytes for Visionect and 1,440 bytes for Bambuddy. Base payload sizes match the earlier database acceptance record. Verification only read existing records; it triggered no snapshot, maintenance or restart.

**Still unverified for this snapshot:** payload readability/checksums, complete required WAL continuity and an exact recovery point. This catalog check exercised no PostgreSQL recovery/PITR, B2/full-store restore or bootstrap. The earlier disposable restore attempts remain failed and fully cleaned up; this snapshot adds no restore success credit. The later live-Athena restores below do not verify B2 recovery. The configured 30-day Athena recovery window is intended policy; first actual retention deletion, storage use and large-catalog behavior remain unverified.

## Real-data alongside restores, 2026-10-29

CNPG-native recovery from the live Athena archive passed for Visionect, then Bambuddy, with one attempt each and no overlap. Both used the inclusive target `2026-10-29T02:30:00Z` and `targetTLI: latest`. All times below are UTC; these disposable-clone timings are not production RPO/RTO measurements.

| Database | Backup ID | Source → restored timeline | Created | Ready | Verified | Cleanup complete |
|---|---|---|---|---|---|---|
| Visionect, PostgreSQL 17.11 | `20261029T013554` | 4 → 5 | 18:02:14 | 18:04:35 | 18:06:42 | 18:07:18 |
| Bambuddy, PostgreSQL 18.6 | `20261029T020738` | 6 → 7 | 18:08:10 | 18:09:50 | 18:10:53 | 18:11:33 |

Visionect restored database/owner `koala`/`visionect`, system identifier `7677042350900830233`; Bambuddy restored `bambuddy`/`bambuddy`, system identifier `7673729494179692568`. Persistent `backup_label.old` matched each selected base backup and its source timeline. The new timeline histories named parents 4 and 6, stopping “before” `02:30:03.620747+00` and `02:30:24.356232+00` respectively, supporting the configured inclusive 02:30 recovery point.

Before installing amcheck, each clone’s public-schema SHA-256 matched the source reference captured at 02:43:56 UTC. Two fixed 1,000-row historical cohorts per database also matched their reference counts, ID bounds and hashes: Visionect’s `device_events` and `status`, and Bambuddy’s `printer_sensor_history` and `ams_sensor_history`. `pg_amcheck --jobs=1 --heapallindexed --parent-check --install-missing=pg_catalog` exited 0 without diagnostics on each clone, checking supported heap/TOAST and btree structures.

Both clones had archiving off, error-free HBA rejection rules, no remote clients and attached/healthy three-replica Longhorn storage. Cleanup removed every owned Cluster, ObjectStore, policy, consumer, PVC/PV, Longhorn volume, attachment, Service, Secret and Cilium endpoint; Whereabouts allocation key `162` and reservation `10.32.25.162` were absent. Controllers completed storage cleanup without forced finalizers or direct PV/Longhorn/IPAM deletion. Final independent production checks at 18:12:26 UTC found unchanged source identities, timelines and database/application Pod UIDs, zero restarts, application HTTP 200, advancing archiving with historical failure counts unchanged, three Ready nodes, all 24 production volumes attached/healthy and all instance managers Ready with `lhnet1`.

**Limits:** this verifies the selected physical recovery point, public schema, sampled cohorts and supported amcheck coverage. Whole-database equality, application behavior against clones, full-cluster/B2 recovery, complete archive continuity and production RPO/RTO remain unverified. Captured main requests/limits matched `250m/512Mi` → `1 CPU/1Gi` and sidecar `100m/128Mi` → `500m/512Mi`, with no observed OOM or failed Job. Brief controller-replaced instance windows were not all captured: lifetime/peak sizing evidence is **partial**, not a full sizing pass. Earlier failed attempts receive no retroactive credit.

[#224](https://github.com/kelchm/home-lab/issues/224)’s database-restore criterion is met; its broader full-cluster disaster recovery work remains open. [#782](https://github.com/kelchm/home-lab/pull/782)’s backup/restore prerequisite is met. Its refreshed HA configuration and procedure below require independent review and separate rollout approval; the successful restores do not authorize that change.

## Two-instance HA rollout

The desired topology is two instances per database, on distinct Kubernetes nodes, with asynchronous streaming replication and two Longhorn replicas per volume. `primaryUpdateMethod: switchover` makes CNPG promote a streaming standby before replacing a primary for a Pod-spec update. PostgreSQL images, 30 GiB/20 GiB sizes, memory/CPU resources, application connections, backup credentials, archive names and HBA rejects are unchanged. The `longhorn-3replica` StorageClass remains available for recovery; do not remove it while existing claims still name it.

**Last live baseline, 2026-10-30 UTC:** both databases still had one Ready instance, healthy archiving and completed backups. Both primaries were named `*-db-2` on node 2. Visionect's current claim is `visionect-db-2` (30 GiB, `longhorn-3replica`); Bambuddy's is `bambuddy-db-2` (20 GiB, `longhorn`). Both underlying volumes have three replicas. This is a dated baseline, not rollout acceptance. Follow #782 for the accepted revision and operational outcome.

Replication is not a backup. An abrupt primary loss can lose transactions not yet received by the standby. Both database instances and their storage still share the three nodes, NAS, network and power. `max_slot_wal_keep_size` limits slot-retained WAL to 10 GB for Visionect and 2 GB for Bambuddy at checkpoints; it does not cap total WAL or replace archiving/space alerts. A standby offline beyond that budget may need re-cloning. See [CNPG's HA-slot behavior](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/docs/src/replication.md#replication-slots-for-high-availability).

### Before the approved merge

1. Require both Clusters Ready, `ContinuousArchiving=True`, fresh successful backups, no backup/volume/replication alert, healthy nodes and attached healthy Longhorn volumes. Keep both database PDBs enabled. Use the [Talos preflight](../../.agents/skills/talos-rollout/SKILL.md#preflight-the-cluster) to inspect shared health and access without draining anything.
2. Save Cluster/Pod/Service/PVC/PV/Longhorn identities, each PostgreSQL system ID, active primary and replica counts outside Git. Resolve each current PVC through its PV's matching claim UID and CSI volume handle. Do not copy an old volume name into a patch.
3. Check capacity for one extra 30 GiB volume and one extra 20 GiB volume, each initially with two storage replicas: 100 GiB additional scheduled space before reducing the old volumes, 50 GiB net afterwards. The extra database requests total 350m CPU and 1,280 MiB memory, plus two unchanged backup sidecars (100m CPU/128 MiB each). Verify placement capacity and free addresses in the existing CNPG storage pool.
4. Record application HTTP/database health and start a simple HTTP sample log for the two applications. Expect a short reconnect interruption, not zero downtime. No node drain, PDB removal, forced eviction, PostgreSQL version upgrade or volume resize is part of this rollout.

### After merge

CNPG 1.30.1 [creates missing instances before rolling Pod-spec changes](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/internal/controller/cluster_controller.go#L1084-L1201). Required anti-affinity changes the existing Pod spec, so [the rolling update can promote the new standby](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/internal/controller/cluster_upgrade.go#L179-L264) before recreating the former primary. The expected source behavior is not a measured interruption bound. With the captured `*-db-2` primaries, the expected final layout is a new `*-db-1` primary and the recreated `*-db-2` standby. Record the actual transition; do not issue an extra promotion simply to force a test. Keep PostgreSQL image bumps separate from parameter changes: CNPG rejects an update that combines both under `switchover`.

For each database, wait for the rolling update to finish before SQL checks or a backup. Two Ready instances can appear before the switchover. Require the Cluster Ready, its current and target primary equal, both Pods using the new required anti-affinity, and the expected primary change/former-primary Pod recreation recorded. If the observed sequence differs, inspect the rollout before accepting it. Read the primary from the Cluster rather than retaining the old name:

```bash
set -euo pipefail
# Choose the database being accepted; repeat for printing/bambuddy-db.
NS=iot
CLUSTER=visionect-db
kubectl -n "$NS" wait "cluster/$CLUSTER" --for=jsonpath='{.status.readyInstances}'=2 --timeout=15m
kubectl -n "$NS" wait "cluster/$CLUSTER" --for=condition=Ready --timeout=15m
kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER" -o wide
PRIMARY=$(kubectl -n "$NS" get cluster "$CLUSTER" -o jsonpath='{.status.currentPrimary}')
TARGET=$(kubectl -n "$NS" get cluster "$CLUSTER" -o jsonpath='{.status.targetPrimary}')
test -n "$PRIMARY" && test "$PRIMARY" = "$TARGET"
kubectl -n "$NS" exec "$PRIMARY" -c postgres -- psql -X -qAt -v ON_ERROR_STOP=1 -c \
  "SELECT pg_is_in_recovery(); SELECT system_identifier FROM pg_control_system(); SELECT application_name,state,sync_state,sent_lsn,replay_lsn,pg_wal_lsn_diff(sent_lsn,replay_lsn) AS lag_bytes FROM pg_stat_replication;"
kubectl -n "$NS" get endpointslices -l "kubernetes.io/service-name=$CLUSTER-rw" -o jsonpath='{range .items[*].endpoints[*]}{.targetRef.name}{"\n"}{end}'
kubectl -n "$NS" get pdb
```

Require the unchanged system ID, primary out of recovery, exactly one streaming standby with replay caught up, and the write endpoint pointing only at the current primary. Query `pg_is_in_recovery()` in the standby too; it must be true. Inspect both Pods' storage attachments and HBA configuration, advancing archiving on the current primary, and fresh metrics from both instances. Existing namespace/cluster peer policy already permits replication and instance-manager traffic; do not broaden it or the storage-network HBA rules.

Scheduled backups default to [prefer-standby](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/docs/src/backup.md#backup-from-a-standby): HA changes which instance can run them. After replication and storage settle, take one ordinary plugin backup per database with `target: prefer-standby`; require completed Backup status on the standby, a readable archive record and healthy backup metrics from both instances. Keep the current schedule, archive and retention policy. Check both applications recover through their existing endpoints without changing connection configuration. Stop on a failed check; inspect CNPG events and existing logs rather than deleting/recreating instances on a guess.

Only after both clusters and backups pass, reduce the two captured original Longhorn volumes from three replicas to two, **one volume at a time**. Revalidate the saved PVC UID → PV claim UID → CSI handle → Longhorn UID chain, require its current count still equals three, and use a JSON patch with tests of `metadata.uid` and `spec.numberOfReplicas` before replacing the latter with `2`. Wait for attached/healthy with two running RW replicas on distinct nodes before changing the other volume. Changing `Cluster.spec.storage.storageClass` affects new claims; it does not modify an existing volume's replica count or immutable PVC storage class. Do not recreate a database PVC for this conversion.

Final acceptance requires two Ready, streaming instances per database on distinct nodes; all four volumes attached/healthy with two storage replicas each; healthy archiving and backup metrics; successful standby backups and application recovery; enabled PDBs; Ready, unsuspended Flux Kustomizations; and no failed join Jobs or unexpected consumers. Record system IDs, the actual primary/timeline changes, interruptions and volume identities in #782. A promotion changes timelines normally. Initial update/switchover acceptance does not establish an abrupt-node-loss RPO/RTO; observe planned-drain behavior during the next authorized maintenance.

### Rollback and a lost standby

If scale-up fails before a switchover, the existing primary remains the recovery starting point. If a switchover has happened, preserve the current primary and its writes; do not force the old primary back. Before returning to one instance, restore **the current primary's** Longhorn volume to three replicas and wait for all three healthy copies. CNPG 1.30.1 scale-down deletes the removed non-primary instance's PVC; the `Delete` reclaim policy then removes its Longhorn volume. After the expected switchover, this is the original `*-db-2` copy. If that copy must be retained, resolve its PVC/PV/CSI ownership chain and change that PV's reclaim policy to `Retain` before reverting, using a JSON patch that tests its UID and existing policy. Then revert the HA change. Reverting the Pod-spec settings also restarts the remaining primary, so expect an interruption. Verify one Ready primary, its write endpoint, archiving, application recovery and PDB protection. A failed join or not-Ready standby can prevent scale-down; inspect and deliberately remove only the confirmed non-primary instance with the matching CNPG tooling. Inventory leftover PVCs, retained PVs/volumes and inactive slots; never delete the current primary's claim or clear finalizers. Do not leave Flux suspended.

If an offline standby's slot becomes `lost`, leave the healthy primary alone. Check archive reachability and disk space, then re-clone only the identified standby with the matching CNPG plugin's [instance destroy operation](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/docs/src/kubectl-plugin.md#destroy), which deletes that instance's PVCs. Verify the selected instance is not current primary immediately before the operation. Allow CNPG to provision its replacement and require streaming/healthy storage again, with its replacement slot active and `wal_status` not `lost`; never disable HA replication slots or remove their WAL cap to hide a failed standby.

## How it works

| Layer | What | Where | Recovery point |
|---|---|---|---|
| 1 | WAL archive and daily base backup, written by the [Barman Cloud plugin](https://cloudnative-pg.io/plugin-barman-cloud/) sidecar in each database pod over its storage-VLAN interface | versitygw on Athena, [`synology/s3-storage`](../../synology/s3-storage/README.md) | 5 minutes or better while Athena is reachable |
| 2 | Kopia snapshot of that share | Backblaze B2, under the [off-site job](../../synology/offsite-backup/README.md)'s 30-day lock | Up to about a day behind layer 1 |
| Independent | Longhorn's daily and weekly volume backups | Athena over NFS, then B2 by the same job | A day; crash-consistent; see [longhorn-backup-restore](longhorn-backup-restore.md) |

PostgreSQL closes a WAL segment at least every 5 minutes while the database is being written to, and the sidecar uploads it. A base backup runs daily. Restoring replays WAL over a base backup and can stop at any moment inside the retained window.

The storage interface is outside Cilium's policy in both directions. A database pod can reach every host on the storage VLAN, and those hosts can reach the pod's `10.32.25.160/28` address. Both Clusters reject password-based PostgreSQL logins from `10.32.25.0/24` and all IPv6 sources (`::/0`) in `pg_hba`. The cluster uses IPv4 for application connections; the IPv6 rule covers both today’s link-local addresses on `net1` and any address it might later learn. CloudNativePG’s generated certificate-authenticated replication and pooler rules precede these rejects. Plain `psql` uses the Unix socket and is unaffected; `psql -h localhost` can resolve to `::1` and be rejected, so use the socket or explicit `127.0.0.1`. `pg_hba` filters only logins: the exporter's metrics on port 9187 and the instance manager's status on port 8000 stay readable from that VLAN. The instance manager's backup, upgrade, partial-WAL and controldata endpoints require the operator's client certificate in CloudNativePG 1.30.1, so being reachable there does not give control of the database. Verified on a scratch cluster: an IPv4 login from the storage VLAN was refused by `pg_hba` while one through the pod address worked. During live activation both databases also refused no-credential self-probes to their storage IPv4 and IPv6 link-local addresses; no external L2 probe was performed.

B2 is the copy of last resort. The databases never write to it, the cluster holds no B2 credential, and every restore starts with the copy on Athena when Athena exists.

| Thing | `visionect-db` | `bambuddy-db` |
|---|---|---|
| Bucket | `k8s-prod-visionect-db` | `k8s-prod-bambuddy-db` |
| Archive (`serverName`) | `visionect-db-pg17` | `bambuddy-db-pg18` |
| Base backup | 08:30 UTC daily | 08:40 UTC daily |
| Kept | 30 days | 30 days |
| Volume | 30Gi | 20Gi |

Retention is the plugin's own `retentionPolicy` on each `ObjectStore`. The sidecar applies it every 5 minutes: it deletes base backups older than the newest one that still covers the window, the WAL before that backup, and the matching `Backup` objects. The window is a recovery window, not an age limit on objects: the base backup that anchors its oldest point is kept even when it is older than 30 days.

The off-site job's own retention is unchanged: 14 daily and 4 weekly snapshots of the share, each holding the 30 days the share held that day. How much space 30 days takes on Athena has not been measured on the real data; check `du` on the share after the first weeks.

Base backups are scheduled after Longhorn's 07:00 and 08:00 UTC jobs and before the off-site run at 10:00 UTC. This leaves time for them to finish, but does not guarantee that each off-site snapshot contains that day's completed base backup and required WAL. Verify the contents of a completed Kopia snapshot before relying on it for recovery. A scheduled base backup waits for PostgreSQL's next checkpoint, up to 5 minutes, before it copies anything.

## Turn backups on

Merging the database side changes both Clusters at once: each `ks.yaml` waits for the plugin, but nothing makes one database wait for the other, and each restarts once to gain the sidecar and the storage interface. Neither has a standby, so each is down for its restart. Turn them on one at a time, so that a problem shows on one database while the other is untouched.

Before merging:

1. The object store has passed its acceptance on Athena ([First deployment](../../synology/s3-storage/README.md#first-deployment), step 6), and both `backup-secret.sops.yaml` files hold that store's real per-database keys.
2. The share exists: `ssh kelchm@10.32.20.5 'ls -ld /volume1/object-storage/data/*'` lists both bucket directories. The same merge adds the share to the [off-site job](../../synology/offsite-backup/README.md), whose container does not start if the share is missing.
3. Both databases are healthy, no CloudNativePG alert is firing, and no node is being drained or upgraded. Confirm the off-site container is running neither a Kopia snapshot nor maintenance: this merge recreates it with `SIGKILL`. The 10:00 UTC run can last hours; wait for it to finish rather than merging during it. Before activation, run a server-side dry run of each rendered Cluster with `--field-manager=kustomize-controller` and confirm that `bootstrap.recovery` replaces `bootstrap.initdb` without leaving both methods. This passed for both live Clusters on 2026-10-29 without changing either spec; repeat if the manifests or live bootstrap ownership change.
4. Hold Bambuddy back: `flux suspend kustomization bambuddy -n printing`. Flux leaves a suspended Kustomization alone until it is resumed.

After merging, while Bambuddy is held, `CNPGBackupMetricsMissing` for `printing/bambuddy-db` is expected after 30 minutes: the new rules see its existing CNPG exporter but its sidecar has not been installed. It sends a normal-priority notification and should clear after Bambuddy starts exporting backup metrics. This named warning does not invalidate Visionect's acceptance; investigate other alerts rather than reusing the pre-merge "no CloudNativePG alert" check during the hold.

Merge. Flux installs the plugin and the attachment, then changes `visionect-db`. Accept it before going on:

5. The instance pod restarted with the sidecar and a storage-VLAN address: `kubectl -n iot get pod -l cnpg.io/cluster=visionect-db -o jsonpath='{.items[0].metadata.annotations.k8s\.v1\.cni\.cncf\.io/network-status}'` lists `cnpg-system/storage-network` with an address in `10.32.25.160/28`, and the pod has an init container named `plugin-barman-cloud`. Record when the new pod becomes Ready in UTC. Inspect `/proc/net/if_inet6` in its `postgres` container and every row of `pg_hba_file_rules` in PostgreSQL, including rows with a null `type`; require error-free `::/0` and `10.32.25.0/24` rejections. The rules view reads the file, so also require a configuration-reload log entry with no HBA reload failure and a no-credential TCP probe to the pod’s own `net1` link-local address that reports `pg_hba.conf rejects connection`, rather than a password challenge. Confirm no IPv6 sessions exist. If these checks fail, pause acceptance sign-off and Bambuddy’s resume for review. Continue the WAL checks and manual backup for the already-started database while that review is pending; do not leave it without a base backup.
6. Prove WAL reaches Athena, not just that the Cluster reports success. **No sooner than 15 minutes after the new pod is Ready**, `kubectl -n iot get cluster visionect-db -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}'` must print `True`, and `cnpg_collector_pg_wal_archive_status{namespace="iot", value="ready"}` must stay at 0 or 1. Those signals already passed before activation and are not sufficient alone. Require new WAL objects written after the restart under the archive's `wals/` directory: `ssh kelchm@10.32.20.5 'sudo find /volume1/object-storage/data/k8s-prod-visionect-db/visionect-db-pg17/wals -type f -mmin -15'` must list new segment objects, with subsequent observations showing the archive advances. Record the object names and observation times.
7. After the new pod is Ready, take [one explicit manual backup](#take-a-backup-by-hand) and require it to complete within an hour of the sidecar's start. First inspect `kubectl -n iot get backups.postgresql.cnpg.io` and let any running backup finish rather than overlapping it. The `immediate: true` request can race the rollout and fail on the old pod with `requested plugin is not available`, or run before the Cluster's plugin configuration is applied; the manual request after Ready avoids that race. Require `completed`, its objects on Athena, and a nonzero `barman_cloud_cloudnative_pg_io_last_available_backup_timestamp`. That metric can stay 0 after the first completion because of a status-update conflict; one further manual backup corrected it in the scratch test. Do not wait for the next daily schedule: with no recorded backup, `CNPGBackupStale` fires at critical priority an hour after the sidecar starts.
8. Visionect still serves its displays.

If any check fails, leave Bambuddy suspended and fix or revert `visionect-db` first. Diagnose a failed or unrecorded base backup under [No completed backup](#no-completed-backup), and a missing WAL upload or sustained queue under [WAL archiving is stalled](#wal-archiving-is-stalled). Do not mistake the initial missing-plugin race for an endpoint failure.

Then turn on Bambuddy:

9. `flux resume kustomization bambuddy -n printing`, and repeat steps 5 to 8 for `printing/bambuddy-db`, bucket `k8s-prod-bambuddy-db` and archive `bambuddy-db-pg18`. Its immediate backup can race its restart too: after the new pod is Ready, take an explicit manual backup and require completion within the hour. This is the first real PostgreSQL 18.6 backup acceptance; the scratch drills used 17.11.
10. After the next off-site run, `kopia snapshot list --all`, run as in the [off-site README](../../synology/offsite-backup/README.md), includes `/sources/object-storage`. Verify that the completed snapshot contains the accepted base backups and required WAL before claiming an off-site recovery point.
11. Record the dated per-database acceptance and Kopia evidence in this runbook's opening record and [What has been exercised](#what-has-been-exercised), the [S3 storage README](../../synology/s3-storage/README.md), the architecture's backup status and #224. Keep partial acceptance explicit while one database or Kopia remains unverified. The real-data restore and #782's hold remain separate.

## Alerts

Rules are in [`alerts.yaml`](../../kubernetes/apps/cnpg-system/plugin-barman-cloud/app/alerts.yaml). The Cluster's own status is not enough: it kept reporting `Cluster in healthy state` and `Continuous archiving is working` for 5 minutes after the store was stopped.

### Streaming standby is missing

`CNPGStreamingStandbyMissing` is a **warning** when a reachable primary cannot confirm its required streaming standby for **10 minutes**. It also warns if a reachable database exporter loses the role or streaming series needed to establish redundancy. Check replication and metrics collection before another node is maintained: losing the primary while its standby is unavailable puts application availability and unreceived transactions at risk.

The rules live beside the [Visionect Cluster](../../kubernetes/apps/iot/visionect/app/alerts.yaml) and [Bambuddy Cluster](../../kubernetes/apps/printing/bambuddy/app/alerts.yaml). Each currently desires two instances and requires one streaming standby. The expectation is a Git configuration value, not a live replica-count metric; `test-cnpg-monitoring.py` rejects disagreement with `spec.instances - 1`. A planned return to one instance must remove that Cluster's standby-loss rule. Manual live scale-down does not change the alert expectation. Other single-instance workloads are outside these selectors. Longhorn's storage replica count does not enter the calculation.

#### Metric semantics and freshness

The stock [`CNPGClusterHACritical` and `CNPGClusterHAWarning` rules](https://github.com/cloudnative-pg/charts/tree/f8b22d4e0041/charts/cluster/prometheus_rules) do not fit this condition: they require one and two standbys respectively, with a five-minute hold, and both use `cnpg_pg_replication_streaming_replicas`. In [CNPG 1.30.1's default query](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/config/manager/default-monitoring.yaml), that metric counts every row of `pg_stat_replication`, without filtering `state`. A connected sender in `catchup` can therefore look healthy. The stock receiver metric only establishes that a WAL receiver row exists; Pod readiness and backup success establish neither sender state nor redundancy.

The per-Cluster `monitoring.yaml` adds one [CNPG user-defined monitoring query](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/docs/src/monitoring.md#user-defined-metrics), `ha`, through the existing exporter. It counts physical replication slots whose active sender is in PostgreSQL's [`streaming` state](https://www.postgresql.org/docs/17/monitoring-stats.html#MONITORING-PG-STAT-REPLICATION-VIEW), with CNPG's `streaming_replica` user and this Cluster's application-name prefix. It returns `cnpg_ha_streaming_replicas=0` when no such sender exists, including a connected but non-streaming standby. `primary: true` limits emission to the current primary. The ConfigMap's `cnpg.io/reload` label enables automatic monitoring reload; the default queries are retained. It reads only catalogs with the existing exporter role and adds no database objects or privileges.

Read-only inspection on 2026-10-30 found `*-db-1` primary (`cnpg_pg_replication_in_recovery=0`) and `*-db-2` standby (`=1`) for both Clusters. Both exporters emit the stock sender metric: primary `1`, standby `0`; both have `up=1` and `cnpg_collector_up=1`. The sender labels identify `streaming_replica` and the matching `*-db-2` application name. Series carry `cluster=k8s-prod`, `namespace`, `pod`, `instance` and `job=<namespace>/<Cluster>`; only `cnpg_collector_up` also carries the database name in `exported_cluster`. The query is new and was **not installed or executed against production during implementation**; this baseline verifies the existing metric identities, not the new query's live result.

vmagent scrapes and vmalert evaluates every 30 seconds. CNPG caches SQL query results for 30 seconds by default; observed query-cache ages were 4–33 seconds. The rule requires scrape, database-up and role samples younger than **120 seconds**, plus `time() - cnpg_last_update_timestamp < 120` for SQL-cache freshness. Thus a cached positive count that is merely re-scraped cannot establish redundancy indefinitely. A healthy confirmation also needs a fresh `cnpg_ha_streaming_replicas >= 1` from the same primary's `(cluster, namespace, pod, instance)` identity. The `exported_cluster` difference does not break those joins.

Read-only queries of the exact proposed expressions were accepted by the existing VMSingle MetricsQL API during implementation. Because the new metric is not deployed, each expression returned one unconfirmed-redundancy series, as intended. Substituting only the stock sender-count metric made the same identity/freshness joins quiet for both live Clusters; that is a compatibility check, not proof that the new streaming-state query has run.

All output is aggregated to one `(cluster, namespace)` result per scoped database, with constant `cnpg_cluster` and `severity=warning` labels. Pod/instance changes and duplicate exporters do not create additional alerts. A brief switchover or maintenance interval is tolerated; sustained loss across a role transition retains the database's alert identity. Ten minutes is an operational allowance for a short restart/switchover, not a tested upper bound for every rebuild. Use an ordinary scoped maintenance silence when a longer interruption is planned.

Missing data has explicit limits:

- A reachable database with a primary/unknown role and absent, stale or zero streaming data warns after the hold. A stale SQL cache or missing role also prevents a healthy confirmation. Check `cnpg_last_error` and `cnpg_collector_last_collection_error` alongside replication before diagnosing a real standby loss.
- A failed/unobserved primary scrape with only a freshly identified standby remaining does not produce a second redundancy warning. Existing `TargetDown` covers discovered targets whose scrapes fail; `CNPGClusterOffline` covers all database-up series being absent/down, and Kubernetes availability alerts remain in effect. These signals describe availability/visibility, not proof of streaming. A missing primary while a known standby still answers is deliberately outside this warning's diagnosis; a removed discovery target is not necessarily covered by `TargetDown`.
- Losing only the standby exporter does not warn if the primary still freshly confirms its physical streaming sender. Losing replication itself while the primary answers does warn, even if the standby Pod/exporter remains Ready.
- If vmalert, vmagent or storage of metrics fails, this rule cannot independently report that failure; the existing monitoring/watchdog alerts remain responsible. The rule proves a physical connection's sampled streaming state, not replay progress, synchronous durability, distinct physical failure domains or a zero-loss failover. Sustained replay lag and invalidated-slot recovery remain separate concerns.

#### Activation, verification and rollback

**Prepared, not live-accepted:** merging the #1125 implementation enables the query references and warning via the normal Flux path. It changes only monitoring ConfigMaps, Cluster monitoring references and alert rules; it does not change instance counts, replication slots/settings, databases, storage or notification routes. No deliberate replication failure or notification test is part of acceptance.

After an approved merge, require Flux's `iot/visionect` and `printing/bambuddy` Kustomizations to be Ready at the merged revision. Confirm each Cluster references its `<name>-monitoring` ConfigMap. The existing PrometheusRule-to-VMRule conversion should load exactly one `CNPGStreamingStandbyMissing`, `for: 10m`, `severity: warning` in each of these groups:

| VMRule | Namespace | vmalert group | Database identity |
|---|---|---|---|
| `visionect-db-alert-rules` | `iot` | `cloudnative-pg/visionect-db` | `cnpg_cluster=visionect-db` |
| `bambuddy-db-alert-rules` | `printing` | `cloudnative-pg/bambuddy-db` | `cnpg_cluster=bambuddy-db` |

Compare the loaded expression and labels with each checked-in `alerts.yaml`; require healthy evaluation on `vmalert-victoria-metrics-k8s-stack`. Through the existing VMSingle query API, inspect `cnpg_ha_streaming_replicas{cluster="k8s-prod",namespace=~"iot|printing"}`, role, scrape/database-up and query-cache freshness. Expected quiet state: one fresh primary sample equal to `1` per Cluster, current primary role `0`, healthy scrapes/database-up and a SQL cache age below 120 seconds; the full alert expressions return no series and `ALERTS{alertname="CNPGStreamingStandbyMissing"}` is absent after the observation window of at least 10 minutes. Standby exporters should not emit this primary-only query. Check `cnpg_last_error=0` and no new collection errors. These live query/load checks are still required; fixture execution is not production PostgreSQL acceptance.

The existing notifier adds `evaluator=vmalert`; together with `severity=warning` this uses the [normal-priority Pushover route](alerting.md#ownership-and-routing), with the existing grouping, 12-hour repeat and quiet resolution. Confirm the loaded route and notifier labels without exposing receiver credentials or sending a test notification. No Alertmanager route or receiver is added by this change.

Rollback is an ordinary **Git revert of the implementation commit**, followed by normal Flux reconciliation. Verify the new rule is gone from both loaded groups, the custom query references and ConfigMaps are removed, and existing offline/backup rules remain loaded. The unused new time series may remain in metrics retention. This rollback removes the redundancy warning and query only; it needs no database, PVC, replication or storage operation.

### WAL archiving is stalled

`CNPGWALArchivingStalled`: the queued WAL count has remained above zero for 15 minutes. This measures a sustained backlog, not the age of an individual segment. Check whether archiving and the recovery point are advancing before diagnosing a stopped archiver.

```sh
kubectl -n <ns> get cluster <name> -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'
kubectl -n <ns> logs <pod> -c plugin-barman-cloud --tail=50 | grep -E 'ERROR|error'
# From a host on the storage VLAN, such as a PVE node; 403 means the endpoint and certificate are fine.
curl -sS -o /dev/null -w '%{http_code}\n' https://s3-storage.home.kelch.io:9000/
```

| Seen | Cause |
|---|---|
| `curl` fails or times out | Athena, the `s3-storage` project or the network path. See its [README](../../synology/s3-storage/README.md#check-on-it). |
| `curl` works but the pod times out | The pod's storage interface. `kubectl -n <ns> get pod <pod> -o jsonpath='{.metadata.annotations.k8s\.v1\.cni\.cncf\.io/network-status}'` must list a `net1` address in `10.32.25.160/28`. |
| `curl` reports a certificate error | Traefik did not renew the certificate. Check its log on Athena. |
| `exit status 4` in the sidecar log within seconds, endpoint fine | Credentials or bucket ownership. Compare `backup-secret.sops.yaml` with the account on Athena. |
| `Expected empty archive` | The Cluster is missing the `cnpg.io/skipEmptyWalArchiveCheck` annotation. |

Archiving resumes on its own once the store answers; the queue drained within a minute in the test. No WAL is lost while the volume has room.

**Do not restart the database pod or drain its node while the store is unreachable.** PostgreSQL waits for the archiver while shutting down, and each attempt against a dead address takes about 5 minutes to give up. In the test one restart took 8.5 minutes with the database down throughout, and another 80 seconds. CloudNativePG's `stopDelay` allows up to 30 minutes.

How long there is: measured on 2026-10-27 over the previous 7 days, `visionect-db` writes about 113 MiB of WAL a day (272 MiB on its busiest day in 30) and `bambuddy-db` about 42 MiB, but both write in every 5-minute window and `archive_timeout` is 5 minutes, so each closes a 16 MiB segment every 5 minutes: about 4.5 GiB a day of unarchived WAL while the store is down. `visionect-db` has 26.5 GiB free on 30Gi, about 6 days. `bambuddy-db` has 18.9 GiB free on 20Gi (grown on 2026-10-28 in [home-lab#1076](https://github.com/kelchm/home-lab/pull/1076)), about 4 days, with `CNPGVolumeFillingUp` firing about 2 days in.

### The database volume is filling

`CNPGVolumeFillingUp`: less than half the volume is free. PostgreSQL stops accepting writes when it fills.

1. If `CNPGWALArchivingStalled` is also firing, fix the store. The WAL clears itself once it is archived.
2. If the store will not be back within a day, raise `storage.size` in the Cluster. Longhorn expands the volume online with no restart, and it cannot be shrunk afterwards.
3. If the volume cannot be grown, [switch the archiver off](#emergency-switch-the-archiver-off).
4. If archiving is healthy, the data has grown. Raise `storage.size` as in step 2.

### No completed backup

`CNPGBackupStale`: the plugin has recorded no completed base backup in 26 hours. `CNPGBackupMetricsMissing`: the instance's exporter is not answering or exports no backup metrics.

```sh
kubectl -n <ns> get backups.postgresql.cnpg.io --sort-by=.metadata.creationTimestamp
kubectl -n <ns> get objectstores.barmancloud.cnpg.io <name> -o jsonpath='{.status}{"\n"}'
kubectl -n <ns> logs <pod> -c plugin-barman-cloud --tail=200 | grep -iE 'backup|retention|error'
```

Use the full resource name: `kubectl get backup` returns Longhorn's backups.

- A backup in `started` for more than a few minutes, or `failed`, usually has the same cause as stalled archiving. Against a stopped store a backup stayed `started` for 7 minutes and then failed.
- During first activation, an immediate backup can instead fail because the old pod has no plugin. Wait for the new sidecar-enabled pod to become Ready, then take a fresh manual backup; the ScheduledBackup does not retry that immediate request.
- Every backup `completed` but the `ObjectStore` status has no `lastSuccessfulBackupTime`: the plugin skipped the status update. This happened once in the test, after the first backup, with `the object has been modified` in the sidecar log. The next backup corrects it; [take one by hand](#take-a-backup-by-hand).
- The exporter not answering at all matches [plugin-barman-cloud#1044](https://github.com/cloudnative-pg/plugin-barman-cloud/issues/1044), where a retention run hung against versitygw. It did not reproduce in the test. If it happens, remove `retentionPolicy` from the `ObjectStore`, restart the pod, and prune by hand as in [Prune by hand](#prune-by-hand).

## Take a backup by hand

Before a risky change, or to clear `CNPGBackupStale`:

```sh
kubectl -n <ns> create -f - <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  generateName: <name>-manual-
spec:
  cluster:
    name: <name>
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
EOF
kubectl -n <ns> get backups.postgresql.cnpg.io -w   # until completed
```

Note the time of the change in UTC. A point-in-time restore to just before it needs only that timestamp.

## Emergency: switch the archiver off

Use this only when `CNPGVolumeFillingUp` is firing, the store cannot be brought back, and the volume cannot be grown. Start while there is room: the restart in step 1 can take as long as CloudNativePG's `stopDelay`, 30 minutes, and WAL keeps collecting until it finishes. At the worst-case rate of 4.6 GB a day that is about 100 MB.

1. Set `enabled: false` on the plugin entry in the Cluster and push to `main`:

   ```yaml
   plugins:
     - name: barman-cloud.cloudnative-pg.io
       enabled: false
       isWALArchiver: true
   ```

   CloudNativePG replaces the affected pods without the sidecar. A singleton is down during its restart; an HA cluster using `switchover` can switch primary first, but the failing archiver can delay that transition. The singleton restart took 80 seconds in the test; a plain restart in the same conditions took 8.5 minutes, for the reason given under [WAL archiving is stalled](#wal-archiving-is-stalled).
2. Confirm the queue is gone: `cnpg_collector_pg_wal_archive_status{value="ready"}` reads 0, and PostgreSQL recycles the WAL at its next checkpoint.

Setting only `isWALArchiver: false` does not work. In the test the sidecar kept attempting uploads, including after a pod restart.

What it costs: every WAL segment written while the plugin is off is never archived. The archive has a gap, so no restore can pass the last segment archived before the switch, and the alerts for this Cluster go quiet except `CNPGBackupMetricsMissing`. Longhorn's volume backups are the only protection until step 4 is done.

To switch back on:

3. Remove `enabled: false` and push. The pod restarts with the sidecar and archiving resumes.
4. [Take a backup by hand](#take-a-backup-by-hand) straight away. Restores are possible again from that backup onward.

## Restore

A restore never overwrites a running database. It creates a Cluster that bootstraps from the store. Three paths, nearest first.

### 1. Alongside the running database

For recovering rows, checking a backup, or rolling back to a moment in time. Create a second Cluster in the same namespace:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: visionect-db-restore
spec:
  instances: 1
  imageName: ghcr.io/cloudnative-pg/postgresql:17.11-system-trixie   # the source's image
  storage:
    size: 30Gi
  inheritedMetadata:
    annotations:
      k8s.v1.cni.cncf.io/networks: cnpg-system/storage-network
  postgresql:
    pg_hba:
      - host all all 10.32.25.0/24 reject
      - host all all ::/0 reject
  bootstrap:
    recovery:
      source: origin
      database: koala
      owner: visionect
      # Omit recoveryTarget to replay everything that was archived.
      recoveryTarget:
        targetTime: "2026-10-12T14:30:00Z"
  externalClusters:
    - name: origin
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters:
          barmanObjectName: visionect-db
          serverName: visionect-db-pg17
```

- Keep the `inheritedMetadata` annotation: the recovery job reaches the store through the storage interface it adds.
- **Give it no `plugins` entry.** A second Cluster archiving into the same `serverName` would write its own timeline into the live database's archive.
- It reuses the source's `ObjectStore` and credentials. For `bambuddy-db` use `database: bambuddy`, `owner: bambuddy`, the same `imageName` as `bambuddy-db` and `serverName: bambuddy-db-pg18`.
- CloudNativePG generates a new `<name>-app` secret and sets the owner's password to match.
- The newest data it can return is the last archived segment: up to 5 minutes old, or older if archiving was stalled.

Copy only the data needed back: run `pg_dump` through `kubectl -n <ns> exec <restore-pod> -c postgres --` using the local PostgreSQL socket, review the resulting SQL, then feed the selected SQL to `kubectl -n <ns> exec -i <live-primary> -c postgres -- psql --username=postgres --dbname=<database> --set=ON_ERROR_STOP=on --single-transaction`. The live databases' Cilium policies block a restore pod's TCP connection through the `-rw` service, so use exec on both sides instead of opening that network path. Do not load an unreviewed full dump into an existing application database. Afterwards delete the restore Cluster and its claim. To replace the database outright with its state at that moment, use [Replace a database](#replace-a-database) instead.

### 2. After the Cluster or the whole Kubernetes cluster is rebuilt

Nothing to do by hand. Both Cluster manifests carry a permanent `bootstrap.recovery` stanza, which CloudNativePG reads only when it creates a data directory. When Flux applies a Cluster that has no volume, it restores the newest base backup, replays the archived WAL, and carries on archiving into the same archive on a new timeline.

- It needs the plugin, the `ObjectStore`, the backup secret and the store on Athena. Flux orders the first three; the Cluster's Kustomization depends on `plugin-barman-cloud`.
- It only fires for a new volume. To force it for an existing Cluster, see [Replace a database](#replace-a-database).
- With no completed base backup in the archive the recovery job fails and is retried indefinitely; WAL alone is insufficient, and no empty database is created. This includes a failure before the first activation backup succeeds. Use the Longhorn copy via the scratch-volume/PostgreSQL/`pg_dump` fallback in [restore order](longhorn-backup-restore.md#restore-order); for the new empty destination replace `bootstrap.recovery` with `bootstrap.initdb` before loading the recovered data. After loading and validating the data, [take a manual base backup](#take-a-backup-by-hand), require it to complete with its WAL available, then restore the permanent `bootstrap.recovery` stanza in Git pointing to that verified archive. Do not leave `initdb` as the future rebuild default, which would create an empty database. To start a database from nothing, use `bootstrap.initdb` for its first start.
- In the [full-cluster procedure](longhorn-backup-restore.md#restore-order) this replaces restoring the database claims from Longhorn. The application starts with the database, so stop it as that procedure says until its own volumes are restored.
- It restores the newest state in the archive, which is later than the application's nightly volume backup. Bambuddy's database refers to files in its archive volume, so restore that pair to one moment: add `recoveryTarget.targetTime`, set to the time of the Longhorn backup being restored for the `bambuddy` claim, before the Cluster is first created. **Undrilled.**

### Replace a database

**Undrilled.** This deletes the live database's volume; everything written after the restore point is lost. The Cluster and its application are in one Flux Kustomization, which would recreate the Cluster halfway through, so suspend it first.

1. [Take a backup by hand](#take-a-backup-by-hand) if the database still runs, and prove the restore point with [path 1](#1-alongside-the-running-database) first.
2. Suspend Flux and stop the application:

   ```sh
   flux suspend kustomization <app> -n <ns>
   flux suspend helmrelease <app> -n <ns>
   kubectl -n <ns> get <deployment|statefulset>/<app> -o jsonpath='{.spec.replicas}{"\n"}'   # note the count
   kubectl -n <ns> scale <deployment|statefulset>/<app> --replicas=0
   kubectl -n <ns> get pods   # until the application's pods are gone
   ```

3. For a point in time, add `recoveryTarget.targetTime` under `bootstrap.recovery` in the Cluster manifest and push to `main`. Omit this to restore the newest state.
4. Delete the Cluster and its claim, and wait until both are gone:

   ```sh
   kubectl -n <ns> delete clusters.postgresql.cnpg.io <name>
   kubectl -n <ns> delete pvc -l cnpg.io/cluster=<name>
   ```

5. Resume, which recreates the Cluster from the manifest:

   ```sh
   flux resume kustomization <app> -n <ns>
   flux resume helmrelease <app> -n <ns>
   kubectl -n <ns> get clusters.postgresql.cnpg.io <name> -w   # until healthy
   ```

6. Check the data, then start the application. Resuming does not undo the manual scale, because the Helm release itself has not changed:

   ```sh
   kubectl -n <ns> scale <deployment|statefulset>/<app> --replicas=<previous-count>
   ```

7. Remove `recoveryTarget` from the manifest and push. It is read only at creation, so removing it changes nothing on the running Cluster.

### 3. Athena is gone

1. Restore the `/sources/object-storage` source from B2 with Kopia onto any machine, following the [off-site README](../../synology/offsite-backup/README.md#restore).
2. Serve it with versitygw, read-only, as in [Serve a copy of the share](../../synology/s3-storage/README.md#serve-a-copy-of-the-share).
3. Create a Secret with that gateway's root key and an `ObjectStore` whose `endpointURL` is the temporary gateway and whose `destinationPath` is the same bucket.
4. Follow path 1 with `barmanObjectName` set to that `ObjectStore`. Check the rows, then decide how it becomes the database: usually by pointing the real `ObjectStore` at a rebuilt store that holds the restored files and following path 2.

The copy is as old as the last off-site run: up to a day, plus that run's duration.

## Prune by hand

Only needed if `retentionPolicy` has been removed. Run Barman's own command from the sidecar image with the database's key; the sidecar container has no shell, so start a pod:

```sh
kubectl -n <ns> run barman-prune --rm -it --restart=Never \
  --image=ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.15.1 \
  --overrides='{"metadata":{"annotations":{"k8s.v1.cni.cncf.io/networks":"cnpg-system/storage-network"}},"spec":{"containers":[{"name":"barman-prune","image":"ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.15.1","envFrom":[{"secretRef":{"name":"<name>-backup"}}],"command":["python3","-c","import os;os.environ.update(AWS_ACCESS_KEY_ID=os.environ[\"ACCESS_KEY_ID\"],AWS_SECRET_ACCESS_KEY=os.environ[\"ACCESS_SECRET_KEY\"]);os.execvp(\"barman-cloud-backup-delete\",[\"barman-cloud-backup-delete\",\"--endpoint-url\",\"https://s3-storage.home.kelch.io:9000\",\"--retention-policy\",\"RECOVERY WINDOW OF 30 DAYS\",\"--dry-run\",\"s3://k8s-prod-<name>/\",\"<serverName>\"])"]}]}}'
```

Remove `--dry-run` once the list is right.

## PostgreSQL major upgrades

The archive name ends in the PostgreSQL major, and a major upgrade must change it in both places it appears in the Cluster: the `plugins` entry and the `externalClusters` entry. `pg_upgrade` restarts at timeline 1, and the plugin does not separate archives itself, so an upgraded database archiving under the old name would overwrite the old major's WAL. The old archive stays restorable, with the old image, until it is deleted by hand. [Take a backup by hand](#take-a-backup-by-hand) as soon as the upgrade finishes: the new archive has no base backup until then, so a rebuild in that window would find nothing to restore.

## What has been exercised

Scratch database drills used PostgreSQL 17.11 clusters holding 270 to 400 MB in a throwaway namespace on `k8s-prod` on 2026-10-12 and 13. NAS foundation, live database backup acceptance and the real-data alongside restores followed on 2026-10-29 UTC. **Drilled** means only the scope stated in each row; production rebuilds and a CNPG restore from Backblaze remain untested.

| Procedure | State |
|---|---|
| WAL archiving and base backups to versitygw, with no checksum workaround | **Drilled** |
| The same through Traefik over TLS, with a per-database account | **Drilled** with a private CA in scratch tests and with trusted Let’s Encrypt TLS on Athena during the 2026-10-29 NAS and live-backup acceptance |
| Reaching Athena’s storage address from a database pod | **Drilled**: scratch instance/bootstrap pods connected over `10.32.25.160/28`; both real instance pods subsequently uploaded base backups and advancing WAL through `net1` to Athena on 2026-10-29 |
| Path 1 to the latest point | **Drilled**: 200,002 rows returned, matching every commit in an archived segment |
| Path 1 to a point in time | **Drilled**: the marker row written before the target was present and the one after it absent |
| Path 2, rebuild into the same archive | **Drilled** twice in a row: identical row count and checksum, then archiving and a backup on the new timeline. Without the annotation it failed with `Expected empty archive` |
| [Replace a database](#replace-a-database), and a `recoveryTarget` paired with a Longhorn backup for Bambuddy | **Undrilled** |
| Path 2 against an empty archive | **Drilled**: the recovery job failed and was retried; no empty database appeared |
| Path 3, from a plain copy of the store's files | **Drilled** with a file copy, with and without the metadata directory. The Kopia restore from B2 and a gateway outside the cluster are **undrilled** |
| Built-in retention | **Drilled**: two consecutive runs deleted backdated backups and their WAL in about a second each, and metrics kept answering |
| Prune by hand | **Drilled** from a pod running the sidecar image, with a per-database account: one backup and its WAL deleted in under a second. The command above ran as written with `--dry-run` |
| Store stopped, then restarted | **Drilled**: signals as described under Alerts; archiving resumed alone |
| Wrong credentials | **Drilled**: `ContinuousArchiving` turned false within a minute |
| Archiver switched off and on again | **Drilled**, including a backup and a rebuild across the gap |
| The alert rules firing | The four backup-related rules are loaded and healthy in vmalert with real backup metrics. Deliberately inducing every fault remains **undrilled**; `scripts/ci/validate-cnpg-alerts.sh` runs a firing and a quiet promtool case for each |
| NAS foundation: kernel 4.4, Btrfs, doco-cd, certificate and account isolation | **Drilled** on 2026-10-29 UTC: capability-free write probe, healthy services, verified TLS (`403 0`), five-part 40 MB transfers with both accounts, matching checksums, deletion and cross-bucket list/write `AccessDenied`. Both real database backup paths and the alongside restores were accepted afterwards |
| Existing Cluster bootstrap admission | Both server-side dry runs passed, then Flux reconciled `bootstrap.recovery` on both live Clusters during activation. Their existing data directories were retained; a rebuild using that stanza remains **undrilled** |
| The real databases: sidecar injection, storage interface, WAL and base backups | **Drilled** on 2026-10-29 for Visionect 17.11 and Bambuddy 18.6, with the exact backups recorded above; this does not verify restored data |
| A real-database restore, including Visionect’s 2.3 GB database | **Drilled** for Visionect 17.11 and Bambuddy 18.6 from live Athena on 2026-10-29: inclusive 02:30 physical recovery, matching public schema/two fixed cohorts each, single-worker amcheck exit 0 and complete cleanup. See the [dated result and limits](#real-data-alongside-restores-2026-10-29); HA rollout approval and live acceptance remain separate under #782 |
| Kopia snapshot and restore of the object-storage source | First normal snapshot and selected base/WAL catalog entries **verified** on 2026-10-29; see the [dated record](#first-normal-object-storage-snapshot-2026-10-29). Payload readability, complete required WAL continuity and restore remain **unverified** |
