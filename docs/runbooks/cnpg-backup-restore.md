# CloudNativePG backup and restore

How the two PostgreSQL clusters, `iot/visionect-db` and `printing/bambuddy-db`, are backed up, what to do when an alert fires, and how to restore. The design decisions are in [home-lab#224](https://github.com/kelchm/home-lab/issues/224).

State as of 2026-10-12: **not deployed.** This describes the intended configuration. Every procedure below was exercised on scratch clusters in a throwaway namespace on `k8s-prod` against a throwaway versitygw, with CloudNativePG 1.30.1, plugin 0.15.1 and versitygw 1.8.0; [What has been exercised](#what-has-been-exercised) says exactly what that covers. Nothing has run against the real databases, Athena or Backblaze.

## How it works

| Layer | What | Where | Recovery point |
|---|---|---|---|
| 1 | WAL archive and daily base backup, written by the [Barman Cloud plugin](https://cloudnative-pg.io/plugin-barman-cloud/) sidecar in each database pod over its storage-VLAN interface | versitygw on Athena, [`synology/s3-storage`](../../synology/s3-storage/README.md) | 5 minutes or better while Athena is reachable |
| 2 | Kopia snapshot of that share | Backblaze B2, under the [off-site job](../../synology/offsite-backup/README.md)'s 30-day lock | Up to about a day behind layer 1 |
| Independent | Longhorn's daily and weekly volume backups | Athena over NFS, then B2 by the same job | A day; crash-consistent; see [longhorn-backup-restore](longhorn-backup-restore.md) |

PostgreSQL closes a WAL segment at least every 5 minutes while the database is being written to, and the sidecar uploads it. A base backup runs daily. Restoring replays WAL over a base backup and can stop at any moment inside the retained window.

B2 is the copy of last resort. The databases never write to it, the cluster holds no B2 credential, and every restore starts with the copy on Athena when Athena exists.

| Thing | `visionect-db` | `bambuddy-db` |
|---|---|---|
| Bucket | `k8s-prod-visionect-db` | `k8s-prod-bambuddy-db` |
| Archive (`serverName`) | `visionect-db-pg17` | `bambuddy-db-pg18` |
| Base backup | 08:30 UTC daily | 08:40 UTC daily |
| Kept | 14 days | 14 days |
| Volume | 30Gi | 20Gi |

Retention is the plugin's own `retentionPolicy` on each `ObjectStore`. The sidecar applies it every 5 minutes: it deletes base backups older than the newest one that still covers the window, the WAL before that backup, and the matching `Backup` objects.

Base backups run after Longhorn's 07:00 and 08:00 UTC jobs and before the off-site run at 10:00 UTC, so each off-site snapshot holds that day's base backup. A scheduled base backup waits for PostgreSQL's next checkpoint, up to 5 minutes, before it copies anything.

## Alerts

Rules are in [`alerts.yaml`](../../kubernetes/apps/cnpg-system/plugin-barman-cloud/app/alerts.yaml). The Cluster's own status is not enough: it kept reporting `Cluster in healthy state` and `Continuous archiving is working` for 5 minutes after the store was stopped.

### WAL archiving is stalled

`CNPGWALArchivingStalled`: segments have waited for the archiver for 15 minutes. The recovery point has stopped advancing and unarchived WAL is collecting on the database volume.

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

How long there is: `visionect-db` writes about 100 MB of WAL a day and `bambuddy-db` about 42 MB, but each closed segment occupies 16 MB on the volume, so a database written to every 5 minutes fills at up to 4.6 GB a day. That is about 6 days for `visionect-db` (27 GB free) and about 4 days for `bambuddy-db` at 20Gi.

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

   CloudNativePG restarts the pod without the sidecar, with the database down meanwhile. The restart took 80 seconds in the test; a plain restart in the same conditions took 8.5 minutes, for the reason given under [WAL archiving is stalled](#wal-archiving-is-stalled).
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
- It reuses the source's `ObjectStore` and credentials. For `bambuddy-db` use `database: bambuddy`, `owner: bambuddy`, the 18.4 image and `serverName: bambuddy-db-pg18`.
- CloudNativePG generates a new `<name>-app` secret and sets the owner's password to match.
- The newest data it can return is the last archived segment: up to 5 minutes old, or older if archiving was stalled.

Then copy what is needed back with `pg_dump` and `psql` through the two `-rw` services, and delete the restore Cluster and its claim. To replace the database outright with its state at that moment, use [Replace a database](#replace-a-database) instead.

### 2. After the Cluster or the whole Kubernetes cluster is rebuilt

Nothing to do by hand. Both Cluster manifests carry a permanent `bootstrap.recovery` stanza, which CloudNativePG reads only when it creates a data directory. When Flux applies a Cluster that has no volume, it restores the newest base backup, replays the archived WAL, and carries on archiving into the same archive on a new timeline.

- It needs the plugin, the `ObjectStore`, the backup secret and the store on Athena. Flux orders the first three; the Cluster's Kustomization depends on `plugin-barman-cloud`.
- It only fires for a new volume. To force it for an existing Cluster, see [Replace a database](#replace-a-database).
- With an empty archive the recovery job fails and is retried indefinitely; no empty database is created. To start a database from nothing, replace the stanza with `bootstrap.initdb` for the first start.
- In the [full-cluster procedure](longhorn-backup-restore.md#restore-order) this replaces restoring the database claims from Longhorn. The application starts with the database, so stop it as that procedure says until its own volumes are restored.
- It restores the newest state in the archive, which is later than the application's nightly volume backup. Bambuddy's database refers to files in its archive volume, so restore that pair to one moment: add `recoveryTarget.targetTime`, set to the time of the Longhorn backup being restored for the `bambuddy` claim, before the Cluster is first created. **Undrilled.**

### Replace a database

**Undrilled.** This deletes the live database's volume; everything written after the restore point is lost. The Cluster and its application are in one Flux Kustomization, which would recreate the Cluster halfway through, so suspend it first.

1. [Take a backup by hand](#take-a-backup-by-hand) if the database still runs, and prove the restore point with [path 1](#1-alongside-the-running-database) first.
2. Suspend Flux and stop the application:

   ```sh
   flux suspend kustomization <app> -n <ns>
   flux suspend helmrelease <app> -n <ns>
   kubectl -n <ns> scale <deployment|statefulset>/<app> --replicas=0
   ```

3. For a point in time, add `recoveryTarget.targetTime` under `bootstrap.recovery` in the Cluster manifest and push to `main`. Omit this to restore the newest state.
4. Delete the Cluster and its claim, and wait until both are gone:

   ```sh
   kubectl -n <ns> delete clusters.postgresql.cnpg.io <name>
   kubectl -n <ns> delete pvc -l cnpg.io/cluster=<name>
   ```

5. Resume, which recreates the Cluster from the manifest and starts the application once the HelmRelease reconciles:

   ```sh
   flux resume kustomization <app> -n <ns>
   flux resume helmrelease <app> -n <ns>
   kubectl -n <ns> get clusters.postgresql.cnpg.io <name> -w   # until healthy
   ```

6. Check the data, then remove `recoveryTarget` from the manifest and push. It is read only at creation, so removing it changes nothing on the running Cluster.

### 3. Athena is gone

1. Restore the `s3-storage` source from B2 with Kopia onto any machine, following the [off-site README](../../synology/offsite-backup/README.md#restore).
2. Serve it with versitygw, read-only, as in [Serve a copy of the share](../../synology/s3-storage/README.md#serve-a-copy-of-the-share).
3. Create a Secret with that gateway's root key and an `ObjectStore` whose `endpointURL` is the temporary gateway and whose `destinationPath` is the same bucket.
4. Follow path 1 with `barmanObjectName` set to that `ObjectStore`. Check the rows, then decide how it becomes the database: usually by pointing the real `ObjectStore` at a rebuilt store that holds the restored files and following path 2.

The copy is as old as the last off-site run: up to a day, plus that run's duration.

## Prune by hand

Only needed if `retentionPolicy` has been removed. Run Barman's own command from the sidecar image with the database's key; the sidecar container has no shell, so start a pod:

```sh
kubectl -n <ns> run barman-prune --rm -it --restart=Never \
  --image=ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.15.1 \
  --overrides='{"metadata":{"annotations":{"k8s.v1.cni.cncf.io/networks":"cnpg-system/storage-network"}},"spec":{"containers":[{"name":"barman-prune","image":"ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.15.1","envFrom":[{"secretRef":{"name":"<name>-backup"}}],"command":["python3","-c","import os;os.environ.update(AWS_ACCESS_KEY_ID=os.environ[\"ACCESS_KEY_ID\"],AWS_SECRET_ACCESS_KEY=os.environ[\"ACCESS_SECRET_KEY\"]);os.execvp(\"barman-cloud-backup-delete\",[\"barman-cloud-backup-delete\",\"--endpoint-url\",\"https://s3-storage.home.kelch.io:9000\",\"--retention-policy\",\"RECOVERY WINDOW OF 14 DAYS\",\"--dry-run\",\"s3://k8s-prod-<name>/\",\"<serverName>\"])"]}]}}'
```

Remove `--dry-run` once the list is right.

## PostgreSQL major upgrades

The archive name ends in the PostgreSQL major, and a major upgrade must change it in both places it appears in the Cluster: the `plugins` entry and the `externalClusters` entry. `pg_upgrade` restarts at timeline 1, and the plugin does not separate archives itself, so an upgraded database archiving under the old name would overwrite the old major's WAL. The old archive stays restorable, with the old image, until it is deleted by hand. [Take a backup by hand](#take-a-backup-by-hand) as soon as the upgrade finishes: the new archive has no base backup until then, so a rebuild in that window would find nothing to restore.

## What has been exercised

All on 2026-10-12, on scratch clusters of PostgreSQL 17.11 holding 270 to 400 MB, in a throwaway namespace on `k8s-prod`. **Drilled** here means against that scratch store, never the real databases or Athena.

| Procedure | State |
|---|---|
| WAL archiving and base backups to versitygw, with no checksum workaround | **Drilled** |
| The same through Traefik over TLS, with a per-database account | **Drilled**, with a private CA; the Let's Encrypt certificate is **undrilled** |
| Reaching Athena's storage address from a database pod | **Drilled**: an instance pod and a bootstrap job pod each got a `10.32.25.160/28` address and connected to `10.32.25.5`. A backup over that interface is **undrilled**: the backup tests used an in-cluster gateway |
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
| The alert rules firing | **Undrilled**: the scratch clusters were not scraped. Each expression was checked against the live metric names and labels |
| Anything on Athena: kernel 4.4, Btrfs, doco-cd, the certificate | **Undrilled** |
| The real databases: sidecar injection, the Bambuddy volume growth, a restore of 2.3 GB | **Undrilled** |
