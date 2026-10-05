# Off-site backup from Athena

One [Kopia](https://kopia.io) container snapshots Athena's backup shares to a Backblaze B2 bucket every day. The bucket uses Object Lock, and the key on Athena cannot delete, so neither a compromised NAS nor a compromised cluster can destroy the off-site copy. Background and the decisions behind this are in [home-lab#297](https://github.com/kelchm/home-lab/issues/297).

State as of 2026-10-05: the bucket, key and repository exist and are empty. The job has not run on Athena, and no restore from real data has been tested.

## What is where

| Thing | Value |
|---|---|
| Bucket | `athena-home-kelch-io`, Backblaze B2 `us-west-000`, private, Object Lock enabled |
| Repository | Kopia, under `kopia/`, encrypted client-side, owner `offsite@athena` |
| Lock | Compliance mode, 30 days, renewed by daily full maintenance while data is still referenced |
| Bucket lifecycle | Hidden and superseded versions are removed 30 days later, once unlocked |
| Snapshots kept | 14 daily, 4 weekly, 3 latest (repository global policy) |
| Schedule | 10:00 UTC daily: snapshot every directory under `/sources`, full maintenance, then ping |
| Dead-man check | healthchecks.io `athena-offsite-backup`, cron `0 10 * * *` UTC, 3 hours grace, Pushover and email |
| Runtime state | `/volume1/docker/offsite-backup/{cache,logs}`; disposable |

Sources:

| Mounted at | Share | Contents |
|---|---|---|
| `/sources/longhorn` | `/volume1/backups-k8s-prod/longhorn` | Longhorn backup store for `k8s-prod` |

To add a source, mount another share read-only under `/sources/`. It is picked up on the next run.

## Credentials

| Secret | On Athena | In 1Password (Private) |
|---|---|---|
| B2 key `athena-kopia` | Yes, in `secrets.sops.env` | No; create a replacement with the master key |
| Repository password | Yes, in `secrets.sops.env` | `athena-kopia-repository` |
| healthchecks.io ping URL | Yes, in `secrets.sops.env` | No; read it back with the project API key |
| NAS age private key | `/volume1/docker/doco-cd/sops_age_key` | `athena-age-key` |
| B2 master key | No | `blackblaze-master-key` |

The `athena-kopia` key is limited to this bucket and can list, read, write and extend locks. It has no `deleteFiles`, so Kopia's deletions only hide files and Backblaze's lifecycle rule does the removal. `secrets.sops.env` is encrypted to the NAS-scoped age recipient in [`.sops.yaml`](../../.sops.yaml); the cluster's age key cannot read it.

Edit the secrets with the NAS key:

```sh
SOPS_AGE_KEY="$(op read 'op://Private/athena-age-key/password')" sops synology/offsite-backup/secrets.sops.env
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

This works from any machine with Kopia installed; Athena is not needed. Take the repository password from 1Password, and create a temporary read key for the bucket with the master key (or use the master key itself).

```sh
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
export KOPIA_PASSWORD="$(op read 'op://Private/athena-kopia-repository/password')"
kopia repository connect s3 --bucket athena-home-kelch-io --endpoint s3.us-west-000.backblazeb2.com \
  --prefix kopia/ --readonly --override-hostname athena --override-username offsite
kopia snapshot list --all
kopia snapshot restore <snapshot-id> /path/to/restored/longhorn
```

Add `--point-in-time=2026-10-01T00:00:00Z` to `connect` to see the repository as it was at that moment, which is how to recover after files were hidden or overwritten by a stolen key. Raise the Backblaze download cap first if the restore is larger than the free allowance.

Longhorn cannot read the bucket directly: it writes lock files into a backup store even when restoring. Export the restored directory over NFS, add it as a Longhorn backup target, and continue with the [Longhorn restore runbook](../../docs/runbooks/longhorn-backup-restore.md).

## Limits

- Closing the Backblaze account deletes everything, locked or not. The account login and its two-factor device are the last line of defence.
- A snapshot taken while Longhorn is writing can capture a backup half-written. The next day's snapshot has it complete.
- Verified on a scratch bucket on 2026-10-05: locked versions could not be deleted or have their lock shortened with the master key, and a point-in-time restore after a simulated attack matched the source. Not yet verified: removal of hidden data after 30 days.
