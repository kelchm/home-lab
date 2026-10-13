# S3 storage on Athena

An S3 endpoint for the cluster's CloudNativePG backups. [versitygw](https://github.com/versity/versitygw) 1.8.0 stores each object as a plain file on a dedicated share, and a Traefik container in front of it terminates HTTPS with a Let's Encrypt certificate. The [off-site job](../offsite-backup/README.md) snapshots the share to Backblaze B2 once a day. The decisions are recorded in [home-lab#224](https://github.com/kelchm/home-lab/issues/224), and the database side is in the [CloudNativePG backup runbook](../../docs/runbooks/cnpg-backup-restore.md).

doco-cd deploys this project from `main`; the [Synology README](../README.md) describes the deployment model, rollback, and break-glass apply.

State as of 2026-10-12: not deployed. Everything below is the intended configuration. The gateway, the Traefik route and the plain-file restore were exercised in a scratch namespace on `k8s-prod`, not on Athena; [First deployment](#first-deployment) lists what has to be confirmed on the NAS itself.

## What is where

| Thing | Value |
|---|---|
| Endpoint | `https://s3-storage.home.kelch.io:9000`, published on `10.32.20.5` only |
| Certificate | Let's Encrypt, default profile, issued and renewed by Traefik through Cloudflare DNS-01 |
| Objects | `/volume1/s3-storage/data/<bucket>/<key>`, one plain file per object |
| Object metadata | `/volume1/s3-storage/meta/<bucket>/<key>/meta/`, small files holding each object's ETag and checksum |
| Accounts | `/volume1/docker/s3-storage/iam/users.json`, written by the gateway; holds secret keys in clear text and is outside the share that goes off-site |
| Certificate state | `/volume1/docker/s3-storage/acme/acme.json` |
| Published route | `/volume1/docker/s3-storage/dynamic/routes.yaml`, copied from [`traefik/routes.yaml`](traefik/routes.yaml) on every deployment |

Buckets:

| Bucket | Owner account | Written by |
|---|---|---|
| `k8s-prod-visionect-db` | `k8s-prod-visionect-db` | `iot/visionect-db` |
| `k8s-prod-bambuddy-db` | `k8s-prod-bambuddy-db` | `printing/bambuddy-db` |

versitygw is not published. Only Traefik listens, and it forwards to the gateway over the project's private network. The gateway's admin API listens on the container's loopback, so it is reachable only with `docker exec`.

The endpoint is on Athena's `10.32.20.5` address, not the storage address `10.32.25.5`: pods on `k8s-prod` cannot reach the storage VLAN. Cilium masquerades pod traffic only on the node's primary interface, so a pod's packets leave the storage bridge with a pod source address that Athena cannot answer. Measured on 2026-10-12 from a pod: `10.32.20.5:5001` and `10.32.10.5:8090` connect; `10.32.25.5:2049` and `10.32.25.5:8090` time out. Backup traffic is therefore routed through the gateway router at 1 GbE.

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

The Cloudflare token needs `Zone:DNS:Edit` on `kelch.io`. Cloudflare scopes tokens to a zone, not a record.

## First deployment

1. Create the shared folder `s3-storage` in DSM, then the directories. Each bucket is a directory under `data/`:

   ```sh
   ssh kelchm@10.32.20.5 '
     sudo mkdir -p /volume1/s3-storage/data/k8s-prod-visionect-db /volume1/s3-storage/data/k8s-prod-bambuddy-db /volume1/s3-storage/meta
     sudo mkdir -p /volume1/docker/s3-storage/iam /volume1/docker/s3-storage/acme /volume1/docker/s3-storage/dynamic
     sudo chmod 0700 /volume1/docker/s3-storage/iam /volume1/docker/s3-storage/acme
   '
   ```

2. Confirm nothing on Athena already listens on `10.32.20.5:9000`.
3. Add a static record `s3-storage.home.kelch.io` → `10.32.20.5` in UniFi. Without it the name resolves to the `*.home.kelch.io` wildcard.
4. Replace the placeholder values in `versitygw.sops.env` and `traefik.sops.env`, and merge. doco-cd deploys the project; Traefik requests the certificate on its first start.
5. Create one account per database and give it its bucket. Use a new random secret for each, and put the same pair in that database's `backup-secret.sops.yaml`:

   ```sh
   ssh kelchm@10.32.20.5
   admin() { sudo /usr/local/bin/docker exec s3-storage-versitygw-1 sh -c 'versitygw admin --access "$ROOT_ACCESS_KEY" --secret "$ROOT_SECRET_KEY" --endpoint-url http://127.0.0.1:7080 "$@"' admin "$@"; }
   admin create-user --access k8s-prod-visionect-db --secret '<secret>' --role user
   admin change-bucket-owner --bucket k8s-prod-visionect-db --owner k8s-prod-visionect-db
   admin list-buckets
   ```

6. Check the endpoint from a workstation: `curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' https://s3-storage.home.kelch.io:9000/` prints `403 0`, an unsigned request refused over a verified certificate.

To confirm on Athena, because the scratch test ran on a 6.18 kernel and ext4:

- versitygw starts and completes a multipart upload on DSM's 4.4 kernel and Btrfs. If object writes fail, add `--disableotmp` and `--disable-copy-file-range` to the `posix` arguments; both replace a newer kernel facility with a plain copy.
- Traefik's first certificate issue succeeds. Until it does, Traefik serves its built-in self-signed certificate and every S3 client refuses the endpoint.

## Check on it

```sh
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker ps --filter label=com.docker.compose.project=s3-storage'
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker logs --since 1h s3-storage-traefik-1'
ssh kelchm@10.32.20.5 'sudo du -sh /volume1/s3-storage/data/*'
```

Nothing here alerts on its own. A stopped or unreachable endpoint shows up in the cluster as `CNPGWALArchivingStalled` within 15 minutes. A certificate that fails to renew has about 30 days before it expires, and then shows up the same way.

## Serve a copy of the share

Any machine can serve a copy of the share, which is how a restore works when Athena is gone. Restore the share with Kopia as described in the [off-site README](../offsite-backup/README.md#restore), then:

```sh
docker run --rm -p 7070:7070 -v /path/to/restored/s3-storage:/share:ro \
  ghcr.io/versity/versitygw:v1.8.0 --readonly --access restore --secret '<any secret>' posix --nometa /share/data
```

`--nometa` ignores the metadata directory, so it works on a read-only copy. A restore only reads. Verified on 2026-10-12: a `Cluster` bootstrapped from a plain copy of the scratch store's files served this way, and from one served with `--sidecar /share/meta`, each returned the source's 320,006 rows with a matching checksum.

Do not edit files under `data/` by hand. The gateway returns the checksum it recorded at upload, and the backup tools reject an object whose bytes no longer match it.

## Limits

- Accounts and bucket ownership are created by hand and live outside Git. On a rebuilt NAS, repeat step 5 with the secrets already in the cluster's `backup-secret.sops.yaml` files.
- `users.json` is not copied off-site. Losing it loses no backup data.
- The gateway's own Object Lock and versioning are not used. Protection against deletion comes from the off-site copy's 30-day lock.
