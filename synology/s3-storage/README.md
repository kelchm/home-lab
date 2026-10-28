# S3 storage on Athena

The lab's S3 object store. Its first buckets hold the CloudNativePG backups from `k8s-prod`. [versitygw](https://github.com/versity/versitygw) 1.8.0 stores each object as a plain file on the `object-storage` share, and a Traefik container in front of it terminates HTTPS with a Let's Encrypt certificate. The decisions are recorded in [home-lab#224](https://github.com/kelchm/home-lab/issues/224), and the database side is [home-lab#816](https://github.com/kelchm/home-lab/pull/816).

The [off-site job](../offsite-backup/README.md) does not read this share yet. Mounting a share that does not exist stops that job's container from starting, so the source is added with the database side, after the share has been created and checked on Athena.

doco-cd deploys this project from `main`; the [Synology README](../README.md) describes the deployment model, rollback, and break-glass apply.

State as of 2026-10-27: not deployed, and the share does not exist yet. Everything below is the intended configuration. The gateway, the Traefik route and the plain-file restore were exercised in a scratch namespace on `k8s-prod`, not on Athena; [First deployment](#first-deployment) lists what has to be confirmed on the NAS itself.

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

The endpoint is on the storage VLAN only. Pods on `k8s-prod` cannot reach that VLAN through Cilium, which masquerades pod traffic only on the node's primary interface: measured on 2026-10-12, an ordinary pod timed out on `10.32.25.5:2049` and `:8090`. The database side gives the database pods a second interface on the storage bridge, from a `cnpg-system/storage-network` attachment, with addresses in `10.32.25.160/28`. A scratch database pod attached that way connected to both ports.

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

The Cloudflare token needs `Zone:Zone:Read` and `Zone:DNS:Edit` on `kelch.io`; Traefik's ACME client reads the zone to find its ID. Cloudflare scopes tokens to a zone, not a record.

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
4. Create the Cloudflare token described under [Credentials](#credentials), and replace the `REPLACE_ME` values in `versitygw.sops.env` and `traefik.sops.env`. With the placeholders in place the gateway starts with a known root key and Traefik cannot get a certificate.

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

   The scratch test ran on a 6.18 kernel and ext4. If object writes fail on DSM's 4.4 kernel and Btrfs, add `--disableotmp` and `--disable-copy-file-range` to the `posix` arguments; both replace a newer kernel facility with a plain copy. Until the first certificate issue succeeds, Traefik serves its built-in self-signed certificate and every S3 client refuses the endpoint.

## Check on it

```sh
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker ps --filter label=com.docker.compose.project=s3-storage'
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker logs --since 1h s3-storage-traefik-1'
ssh kelchm@10.32.20.5 'sudo du -sh /volume1/object-storage/data/*'
```

Nothing here alerts on its own. A stopped or unreachable endpoint shows up in the cluster as `CNPGWALArchivingStalled` within 15 minutes. A certificate that fails to renew has about 30 days before it expires, and then shows up the same way.

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
- The off-site job copies the whole share, so every bucket goes to B2 under the same 30-day lock.
- The endpoint is published on the storage VLAN only. A client needs an address there.
- `users.json` is not copied off-site. Losing it loses no backup data.
- The gateway's own Object Lock and versioning are not used. Protection against deletion comes from the off-site copy's 30-day lock.
