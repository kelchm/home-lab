# S3 storage on Athena

The lab's S3 object store. Its first buckets are for the CloudNativePG backups from `k8s-prod`. [versitygw](https://github.com/versity/versitygw) 1.8.0 stores each object as a plain file on the `object-storage` share, and a Traefik container in front of it terminates HTTPS with a Let's Encrypt certificate. The decisions are recorded in [home-lab#224](https://github.com/kelchm/home-lab/issues/224), and the database side is in the [CloudNativePG backup runbook](../../docs/runbooks/cnpg-backup-restore.md). The database layer adds this share to the daily [off-site job](../offsite-backup/README.md); only a verified completed Kopia snapshot establishes the Backblaze B2 copy.

doco-cd deploys this project from `main`; the [Synology README](../README.md) describes the deployment model, rollback, and break-glass apply.

State as of 2026-10-29 UTC: the NAS foundation is deployed and accepted (#1077, merged as `95a0680`). The exact gateway image running as UID/GID 0 with all capabilities dropped passed a disposable create/rename/read/delete probe under both bucket directories and `meta` without changing ACLs or modes. doco-cd then deployed the store; gateway and proxy are healthy, the route publisher exited 0, and an unsigned request from the storage VLAN returns `403 0` with a trusted Let’s Encrypt certificate. `acme.json` is root-owned `0600` under its `0700` directory.

Both restricted database accounts exist and own their respective buckets. Each passed a five-part, 40,000,000-byte upload/download with matching SHA-256, deletion confirmed by a subsequent 404, and `AccessDenied` for both listing and writing the other bucket. The disposable objects were deleted. All 12 pre-existing containers retained their IDs, start times and restart counts.

The database layer (#816, merged as `653afc37`) is now active. Visionect passed first, then Bambuddy resumed and passed: each has a completed manual base backup, a nonzero last-backup metric and advancing WAL objects after at least 15 minutes of observation. The accepted backup IDs are `20261029T013554` for Visionect (PostgreSQL 17.11) and `20261029T020738` for Bambuddy (18.6). Both applications are healthy; their application pods were not restarted. #1095 added `::/0` HBA rejection, and live no-credential self-probes confirmed both storage IPv4 and IPv6 rejection. Details and exact backup names are in the [runbook’s acceptance record](../../docs/runbooks/cnpg-backup-restore.md).

No completed Kopia snapshot of `/sources/object-storage` was observed at database sign-off on 2026-10-29. The subsequent normal run completed at 10:32:48 UTC, with both accepted base archives and selected WAL objects verified in its immutable catalog; the [dated runbook record](../../docs/runbooks/cnpg-backup-restore.md#first-normal-object-storage-snapshot-2026-10-29) gives the exact snapshot and limits. Payload readability, complete required WAL continuity and restore remain unverified; real-data restore remains a separate gate under #224/#782. [First deployment](#first-deployment) remains the NAS rebuild procedure and acceptance checklist.

## What is where

| Thing | Value |
|---|---|
| Endpoint | `https://s3-storage.home.kelch.io:9000`, published on `10.32.25.5` only |
| Certificate | Let's Encrypt, default profile, issued and renewed by Traefik through Cloudflare DNS-01 |
| Objects | `/volume1/object-storage/data/<bucket>/<key>`, one plain file per object |
| Object metadata | `/volume1/object-storage/meta/<bucket>/<key>/meta/`, small files holding each object's ETag and checksum |
| Accounts | `/volume1/docker/s3-storage/iam/users.json`, written by the gateway; holds secret keys in clear text and is outside the share that goes off-site |
| Certificate state | `/volume1/docker/s3-storage/acme/acme.json` |
| Published route | `/volume1/docker/s3-storage/dynamic/routes.yaml`, copied from [`traefik/routes.yaml`](traefik/routes.yaml) on every deployment |

The share is not tied to one system. A bucket is named `<system>-<workload>` and owned by an account of the same name, so the system that writes it shows in the bucket, not the share. Buckets:

| Bucket | Owner account | Written by |
|---|---|---|
| `k8s-prod-visionect-db` | `k8s-prod-visionect-db` | `iot/visionect-db` |
| `k8s-prod-bambuddy-db` | `k8s-prod-bambuddy-db` | `printing/bambuddy-db` |

versitygw is not published. Only Traefik listens, and it forwards to the gateway over the project's private network. The gateway's admin API listens on the container's loopback, so it is reachable only with `docker exec`.

The endpoint is on the storage VLAN only. Pods on `k8s-prod` cannot reach that VLAN through Cilium, which masquerades pod traffic only on the node's primary interface: measured on 2026-10-12, an ordinary pod timed out on `10.32.25.5:2049` and `:8090`. The database layer gives the pods a second interface on the storage bridge, from the [`cnpg-system/storage-network`](../../kubernetes/apps/cnpg-system/plugin-barman-cloud/app/networkattachmentdefinition.yaml) attachment, with addresses in `10.32.25.160/28`. A scratch database pod attached that way connected to both ports. During 2026-10-29 live acceptance, Visionect used `10.32.25.160` and Bambuddy `.161` to upload their base backups and WAL to this endpoint.

## Credentials

| Secret | On Athena | In the cluster |
|---|---|---|
| Gateway root key | `versitygw.sops.env` | No |
| Cloudflare DNS token | `traefik.sops.env` | No |
| Per-database key | `users.json` | `backup-secret.sops.yaml` beside each `Cluster` |

Each database account has the `user` role and owns one bucket. Verified in the scratch test: such an account reads, writes and deletes in its own bucket and is refused on another account's bucket. The root key can do everything and is used only for the admin commands below and for a [restore from a copy](#serve-a-copy-of-the-share).

Both `*.sops.env` files are encrypted to the NAS-scoped age recipient in [`.sops.yaml`](../../.sops.yaml). Edit them with the NAS key:

```sh
SOPS_AGE_KEY="$(op read 'op://Private/athena-doco-cd-sops-age-key/password')" sops synology/s3-storage/versitygw.sops.env
```

The Cloudflare token needs `Zone:Zone:Read` and `Zone:DNS:Edit` on `kelch.io`; Traefik's ACME client reads the zone to find its ID. Cloudflare scopes tokens to a zone, not a record. The token in use, `athena-acme`, has no expiry or client-IP filter. Its Cloudflare label and existing 1Password item's title were renamed from `athena-s3-storage-acme` on 2026-10-29, preserving the token value and effective permissions. [Athena's native ACME client](../../docs/runbooks/athena-acme.md) now shares this credential for DSM issuance; DSM commissioning remains incomplete. The item-ID reference below survives the title change. S3 reads the encrypted dotenv value; no S3 redeployment occurred. Revoking this shared token interrupts both clients' renewal.

The values in both files are kept in 1Password:

| Value | 1Password reference |
|---|---|
| Cloudflare token | `op://Project - home-lab/yg4lgp23nzgmknxb7bgn3rfsvm/credential` |
| Gateway root access key | `op://Project - home-lab/athena-s3-storage-root/username` |
| Gateway root secret key | `op://Project - home-lab/athena-s3-storage-root/credential` |
| Visionect account secret (access key `k8s-prod-visionect-db`) | `op://Project - home-lab/athena-s3-storage-k8s-prod-visionect-db/credential` |
| Bambuddy account secret (access key `k8s-prod-bambuddy-db`) | `op://Project - home-lab/athena-s3-storage-k8s-prod-bambuddy-db/credential` |

## First deployment

Before merging, all on Athena and in UniFi:

1. Create the shared folder `object-storage` in DSM, then the directories. Each bucket is a directory under `data/`:

   ```sh
   ssh kelchm@10.32.20.5 '
     sudo mkdir -p /volume1/object-storage/data/k8s-prod-visionect-db /volume1/object-storage/data/k8s-prod-bambuddy-db /volume1/object-storage/meta
     sudo mkdir -p /volume1/docker/s3-storage/iam /volume1/docker/s3-storage/acme /volume1/docker/s3-storage/dynamic
     sudo chmod 0700 /volume1/docker/s3-storage/iam /volume1/docker/s3-storage/acme
   '
   ```

   Check it: `ssh kelchm@10.32.20.5 'ls -ld /volume1/object-storage/data/* /volume1/object-storage/meta'` lists all three directories.

2. Confirm nothing on Athena already listens on `10.32.25.5:9000`.
3. Add a static record `s3-storage.home.kelch.io` → `10.32.25.5` in UniFi. Without it the name resolves to the `*.home.kelch.io` wildcard.
4. Create the Cloudflare token described under [Credentials](#credentials) and a random root key pair, store them in 1Password, and encrypt them into `traefik.sops.env` and `versitygw.sops.env`.

Before the first merge, run an explicitly authorized disposable container probe with the gateway image, UID/GID 0, all capabilities dropped, `no-new-privileges` and the same share bind mount. Prove that it can create, rename and delete its own temporary files under both bucket directories and `meta`, leaving existing files untouched. Root ownership alone does not prove access through DSM's share ACL. If this fails, stop before merging and review the permissions; do not change ACLs, modes or capabilities as an unreviewed workaround.

Then merge. doco-cd deploys the project, and Traefik requests the certificate on its first start.

After merging:

5. Create one account per database and give it its bucket. Use a new random secret for each, and put the same pair in that database's `backup-secret.sops.yaml` on the database side:

   ```sh
   ssh kelchm@10.32.20.5
   admin() { sudo /usr/local/bin/docker exec s3-storage-versitygw-1 sh -c 'versitygw admin --access "$ROOT_ACCESS_KEY" --secret "$ROOT_SECRET_KEY" --endpoint-url http://127.0.0.1:7080 "$@"' admin "$@"; }
   admin create-user --access k8s-prod-visionect-db --secret '<secret>' --role user
   admin change-bucket-owner --bucket k8s-prod-visionect-db --owner k8s-prod-visionect-db
   admin list-buckets
   ```

6. Accept it from a host on the storage VLAN, such as a PVE node. The database side does not merge until every check passes:

   - `curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' https://s3-storage.home.kelch.io:9000/` prints `403 0`: an unsigned request refused over a verified Let's Encrypt certificate.
   - With one database's account, a 40 MB file goes up and comes back intact, which the AWS CLI sends as a multipart upload, and is deleted again: `aws --endpoint-url https://s3-storage.home.kelch.io:9000 s3 cp test.bin s3://k8s-prod-visionect-db/acceptance/test.bin`, then `s3 cp` it back, compare checksums, and `s3 rm` it.
   - The same account is refused (`AccessDenied`) on the other database's bucket. Repeat both checks with the other account.

   The scratch test ran on a 6.18 kernel and ext4. The disposable probe above checks DSM's ACL with the configured identity, but the multipart test must still prove object operations on the NAS's 4.4 kernel and Btrfs. If a write fails, retain the exact error and review permissions or filesystem compatibility before changing the configuration; copy-related flags are not a remedy for `permission denied`. Until the first certificate issue succeeds, Traefik serves its built-in self-signed certificate and every S3 client refuses the endpoint.

### Roll back the first deployment

Reverting the merge alone does not stop the store: doco-cd only manages declared projects and never destroys one, so dropping the target leaves the containers running. Revert first, wait for doco-cd's next poll, then remove this project only:

```sh
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker-compose -p s3-storage down'
```

Stopping the containers while the target is still on `main` does not hold: doco-cd's next poll recreates a declared project whose services are missing. If the store has to stop before the revert lands, stop doco-cd first as in [break-glass](../README.md#break-glass-apply-over-ssh), and start it again once the revert is on `main`.

`down` removes the three containers and the project network. Leave out `--rmi` and `-v`, and prune nothing: the share, `users.json` and `acme.json` are bind mounts and stay as they are, and the other projects on Athena are untouched.

## Check on it

```sh
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker ps --filter label=com.docker.compose.project=s3-storage'
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker logs --since 1h s3-storage-traefik-1'
ssh kelchm@10.32.20.5 'sudo du -sh /volume1/object-storage/data/*'
```

Nothing here alerts on its own. A stopped or unreachable endpoint shows up in the cluster as `CNPGWALArchivingStalled` once a WAL segment has waited 15 minutes for the archiver; the databases close a segment every 5 minutes, so that is 15 to 20 minutes after the endpoint stops. A certificate that fails to renew has about 30 days before it expires, and then shows up the same way.

## Serve a copy of the share

Any machine can serve a copy of the share, which is how a restore works when Athena is gone. Restore the share with Kopia as described in the [off-site README](../offsite-backup/README.md#restore), then:

```sh
docker run --rm -p 7070:7070 -v /path/to/restored/object-storage:/share:ro \
  ghcr.io/versity/versitygw:v1.8.0 --readonly --access restore --secret '<any secret>' posix --nometa /share/data
```

`--nometa` ignores the metadata directory, so it works on a read-only copy. A restore only reads. Verified on 2026-10-12: a `Cluster` bootstrapped from a plain copy of the scratch store's files served this way, and from one served with `--sidecar /share/meta`, each returned the source's 320,006 rows with a matching checksum.

Do not edit files under `data/` by hand. The gateway returns the checksum it recorded at upload, and the backup tools reject an object whose bytes no longer match it.

## Limits

- Accounts and bucket ownership are created by hand and live outside Git. On a rebuilt NAS, repeat step 5 with the secrets already in the cluster's `backup-secret.sops.yaml` files.
- The off-site job now snapshots the whole share to B2 under the same 30-day lock; the [first normal snapshot has accepted catalog evidence](../../docs/runbooks/cnpg-backup-restore.md#first-normal-object-storage-snapshot-2026-10-29).
- The endpoint is published on the storage VLAN only. A client needs an address there.
- `users.json` is not copied off-site. Losing it loses no backup data.
- The gateway's own Object Lock and versioning are not used. Protection against deletion comes from the verified off-site copy's 30-day lock; the NAS foundation alone provides none.
