# Off-site backup from Athena

One [Kopia](https://kopia.io) container snapshots Athena's backup shares to a Backblaze B2 bucket every day. The bucket uses Object Lock, and the key on Athena cannot delete, so neither a compromised NAS nor a compromised cluster can destroy the off-site copy. Background and the decisions behind this are in [home-lab#297](https://github.com/kelchm/home-lab/issues/297).

State as of 2026-10-05: running on Athena. The first snapshot (90.6 GB, 81,844 files) took 7h25m at about 3.5 MB/s, and one Kanidm volume has been restored from the off-site copy without using the NAS; see [Restore drill](#restore-drill-2026-10-05).

## What is where

| Thing | Value |
|---|---|
| Bucket | `athena-home-kelch-io`, Backblaze B2 `us-west-000`, private, Object Lock enabled |
| Repository | Kopia, under `kopia/`, encrypted client-side, owner `offsite@athena` |
| Lock | Compliance mode, 30 days, renewed by daily full maintenance while data is still referenced |
| Bucket lifecycle | Hidden and superseded versions are removed 30 days later, once unlocked |
| Snapshots kept | 14 daily, 4 weekly, 3 latest (repository global policy) |
| Schedule | 10:00 UTC daily: start ping, snapshot every directory under `/sources`, full maintenance, then a ping with the exit status |
| Dead-man check | healthchecks.io `athena-offsite-backup`, cron `0 10 * * *` UTC, 3 hours grace, Pushover and email |
| Runtime state | `/volume1/docker/offsite-backup/{cache,logs}`; disposable |

Sources:

| Mounted at | Share | Contents |
|---|---|---|
| `/sources/longhorn` | `/volume1/backups-k8s-prod/longhorn` | Longhorn backup store for `k8s-prod` |

To add a source, mount another share read-only under `/sources/`. It is picked up on the next run.

Every source shares this one repository, and with it one lock period, one password and one key. Data that needs a different lock period or its own password gets a second repository under its own prefix; repositories do not deduplicate against each other. The lock period for new uploads can be changed with `kopia repository set-parameters --retention-period`, but a lock already applied to an object can only be extended.

## Credentials

| Secret | On Athena | In 1Password (Private) |
|---|---|---|
| B2 key `athena-kopia` | Yes, in `secrets.sops.env` | No; create a replacement with the master key |
| Repository password | Yes, in `secrets.sops.env` | `athena-kopia-repository` |
| healthchecks.io ping URL | Yes, in `secrets.sops.env` | No; read it back with the project API key |
| NAS age private key | `/volume1/docker/doco-cd/sops_age_key` | `athena-doco-cd-sops-age-key` |
| B2 master key | No | `blackblaze-master-key` |

The `athena-kopia` key is limited to this bucket and can list, read, write and extend locks. It has no `deleteFiles`, so Kopia's deletions only hide files and Backblaze's lifecycle rule does the removal. `secrets.sops.env` is encrypted to the NAS-scoped age recipient in [`.sops.yaml`](../../.sops.yaml); the cluster's age key cannot read it.

Edit the secrets with the NAS key:

```sh
SOPS_AGE_KEY="$(op read 'op://Private/athena-doco-cd-sops-age-key/password')" sops synology/offsite-backup/secrets.sops.env
```

## First deployment

1. Give doco-cd the NAS age key, as described in the [doco-cd README](../platform/doco-cd/README.md#nas-age-key).
2. Create the runtime directories:

   ```sh
   ssh kelchm@10.32.20.5 'sudo mkdir -p /volume1/docker/offsite-backup/cache /volume1/docker/offsite-backup/logs'
   ```

3. Merge. doco-cd deploys the project, and the container waits for 10:00 UTC. To start the first upload immediately:

   ```sh
   ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker exec offsite-backup-kopia-1 kopia snapshot create /sources/longhorn'
   ```

## Check on it

```sh
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker logs --since 26h offsite-backup-kopia-1'
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker exec offsite-backup-kopia-1 kopia snapshot list --all'
```

A failed or missed run pages through healthchecks.io about three hours after 10:00 UTC. Backblaze's daily storage cap also stops uploads when it is reached, which shows up the same way.

## Restore

This works from any machine with Kopia installed; Athena is not needed. Take the repository password from 1Password and create a short-lived read key for the bucket with the master key. The master key itself does not work with the S3 endpoint ("Malformed Access Key Id").

```sh
# Read key: capabilities listBuckets, readBuckets, listFiles, readFiles, readBucketRetentions, readFileRetentions
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
export KOPIA_PASSWORD="$(op read 'op://Private/athena-kopia-repository/password')"
kopia repository connect s3 --bucket athena-home-kelch-io --endpoint s3.us-west-000.backblazeb2.com \
  --prefix kopia/ --readonly --override-hostname restore --override-username restore
kopia snapshot list --all
kopia restore <snapshot-id> /path/to/restored/longhorn
```

To restore one Longhorn volume's backups instead of the whole store, restore its directory: `kopia restore <snapshot-id>/backupstore/volumes/<aa>/<bb>/<volume> <dest>/backupstore/volumes/<aa>/<bb>/<volume>`, where `<aa>` and `<bb>` are the first two pairs of hex digits of the SHA-512 of the volume name.

Add `--point-in-time=2026-10-01T00:00:00Z` to `connect` to see the repository as it was at that moment, which is how to recover after files were hidden or overwritten by a stolen key. Raise the Backblaze download cap first if the restore is larger than the free allowance.

Longhorn cannot read the bucket directly: it writes lock files into a backup store even when restoring. Serve the directory that contains `backupstore/`, not `backupstore/` itself, from somewhere writable, over NFS or through an S3 gateway. Longhorn appends `backupstore/volumes/...` to the target it is given: the live target, `nfs://10.32.25.5:/volume1/backups-k8s-prod/longhorn`, is the directory holding `backupstore/`, and `/path/to/restored/longhorn` above is its restored equivalent. The target URL is the URL of that directory, for example `nfs://<host>:/path/to/restored/longhorn`, or `s3://<bucket>@<region>/` when `backupstore/` sits at the bucket root. Then either:

- **Without a Longhorn system:** run the engine binary against it. `longhorn backup restore-to-file '<target-url>?backup=<backup>&volume=<volume>' --output-file vol.raw --output-format raw` from the `longhornio/longhorn-engine` image produces the volume as a disk image. This is the path the drill below exercised, through an S3 gateway.
- **With Longhorn:** make it the cluster's backup target and continue with the [Longhorn restore runbook](../../docs/runbooks/longhorn-backup-restore.md). Not drilled. Do this only on a cluster that does not also have the original target. Backup names are the same in both stores, and Longhorn resolves a restore by backup name, so it silently restores from the original target.

When copying a restored tree from macOS, remove the `._*` files that `tar` adds.

### Restore drill, 2026-10-05

One volume, `identity/kanidm-data-kanidm-default-0`, with the NAS used only as a checksum reference afterwards.

| Step | Result |
|---|---|
| Restore the volume's directory from B2 to a workstation with a temporary read key | 539 files, 70 MB, 17 seconds |
| Compare with the NAS copy | All 539 files identical by SHA-256 |
| Serve the copy over S3 from a throwaway versitygw pod, as a bucket `drill` with `backupstore/` at its root, and run `longhorn backup restore-to-file 's3://drill@us-east-1/?backup=backup-1ca3d4fa57d34be5&volume=pvc-d0a45a01-b999-4109-9edd-34f2a635e343'` (engine v1.12.1) | 2 GiB raw image; the gateway logged 75 block reads and the lock file being written and removed |
| Check the image | ext4, `e2fsck -fn` clean; `kanidm.db` passes `PRAGMA integrity_check`, 122 tables, 323 entries |

A first attempt through a second Longhorn backup target on `k8s-prod` restored from the NAS instead, for the reason given above; it is not counted. Not exercised: serving the restored store over NFS, a restore through Longhorn itself, a full 90 GB restore, and starting Kanidm on the restored volume.

## Recovery objectives

| | |
|---|---|
| Data age (RPO) | At worst, 27 hours plus the duration of the run in progress, when runs succeed. Longhorn backs up at 07:00 UTC and this job starts at 10:00 UTC, so when a run starts the newest off-site backup is already 27 hours old, and it stays the newest until that run completes. The first run took 7h25m; an ordinary daily run uploads only what changed and has not been timed yet. Each failed or unfinished run adds a day; the dead-man check reports those |
| Time to restore (RTO) | Not measured for the full store. One 70 MB volume directory took 17 seconds; 90 GB depends on the downlink |
| History | 14 daily and 4 weekly snapshots of the store, each holding Longhorn's own 7 daily and 4 weekly backups |
| Drill cadence | None fixed. Repeat the drill above after changing the Kopia version, the repository layout or the provider |

## Limits

- Closing the Backblaze account deletes everything, locked or not. The account login and its two-factor device are the last line of defence.
- A snapshot taken while Longhorn is writing can capture a backup half-written. The next day's snapshot has it complete.
- Verified on a scratch bucket on 2026-10-05: locked versions could not be deleted or have their lock shortened with the master key, and a point-in-time restore after a simulated attack matched the source. Not yet verified: removal of hidden data after 30 days.
- The first upload took longer than the dead-man check's 3-hour grace. A large new source needs the grace raised for its first run.
