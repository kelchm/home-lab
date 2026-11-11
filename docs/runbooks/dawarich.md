# Dawarich

Dawarich is Matt's private location-history archive ([#1419](https://github.com/kelchm/home-lab/issues/1419)). It runs in `personal`, a namespace for personal-data apps, at `https://dawarich.home.kelch.io` on `gateway-services`. At home, UniFi resolves the name; away, NetBird's split DNS resolves it and NetBird's existing permit to the household gateway carries the traffic. Nothing is published to the internet, and the network policy gives the pod no internet egress.

The raw Google Timeline export on Athena (`/volume1/homes/kelchm/cloud-backups/google-timeline/`) is the durable archive. Dawarich is the way to view it and to keep collecting new points; it can be replaced without losing the history.

## What runs

| Part | Detail |
|---|---|
| App | `freikin/dawarich`, one pod with a web container and a worker container. The web container runs migrations and seeds on every start, then serves; the worker runs background jobs (`sidekiq-entrypoint.sh`). Jobs used to run embedded in the web process, but after the first big import their CPU load starved the web process until health checks timed out and the site returned 503. |
| Valkey | Sidecar on `127.0.0.1:6379` holding the job queue, cache and websocket state. Not persisted: a restart of the Valkey container or a pod replacement drops queued and running jobs. Re-run an import that was in flight (delete it under Imports and upload the file again), and re-queue track generation for untracked points (below). Stats recalculate hourly on their own. |
| Database | `dawarich-db`, CloudNativePG PostgreSQL 17 with PostGIS, two instances, WAL archiving and daily base backups to Athena bucket `k8s-prod-dawarich-db`. See the [CloudNativePG runbook](cnpg-backup-restore.md). |
| Files | 20 GiB Longhorn volume at `/var/app/storage` for uploaded imports and exports. `/var/app/public`, `/var/app/tmp` and `/tmp` are scratch. |

## After the first deployment

1. **Replace the seeded admin.** On an empty database, the seeds create the admin `demo@dawarich.app` with password `safepassword`. Change both before anything else. Either sign in and change them under the account settings, or set them from the pod, with the new password stored in 1Password first. The values travel on stdin, so they appear in neither the command line nor the shell history:

   ```sh
   printf '%s\n%s\n' 'you@example.com' "$(op read 'op://<vault>/<item>/password')" |
     kubectl -n personal exec -i deploy/dawarich -c app -- bin/rails runner '
       email, password = $stdin.read.split("\n").first(2)
       User.find_by!(email: "demo@dawarich.app").update!(email: email, password: password, password_confirmation: password)'
   ```

   Self-registration is off by default (`ALLOW_EMAIL_PASSWORD_REGISTRATION`).
2. **Accept the database backups** as for the other databases: after the instances are Ready, [take a backup by hand](cnpg-backup-restore.md#take-a-backup-by-hand) and require it to complete, with objects on Athena and a nonzero last-backup metric. A base backup alone does not prove later changes are saved: also require WAL objects under `k8s-prod-dawarich-db` to keep advancing and `cnpg_collector_pg_wal_archive_status{namespace="personal", value="ready"}` to stay at 0 for at least 15 minutes, as in the [activation checks](cnpg-backup-restore.md#turn-backups-on). [WAL archiving is stalled](cnpg-backup-restore.md#wal-archiving-is-stalled) covers a failure.
3. **Switch the database to its permanent bootstrap.** `cluster.yaml` starts with `bootstrap.initdb`, which creates an empty database. Once a base backup has completed, replace it with the `bootstrap.recovery` and `externalClusters` stanzas the other databases use, pointing at `dawarich-db` and `serverName: dawarich-db-pg17`, and add the `cnpg.io/skipEmptyWalArchiveCheck: enabled` annotation with it. The annotation is left off until then because, with `initdb`, CloudNativePG's empty-archive check is what stops a rebuilt empty database from archiving over the existing backups. CloudNativePG reads the bootstrap only when it creates a data directory, so the change does not touch the running database; it makes a rebuild restore the archive instead of starting empty. Do not leave `initdb` in place.

## Import Google Timeline

Use the phone's own export (Google Maps → Settings → Personal content → Export Timeline data). Dawarich detects the format, including the on-device `Timeline.json`, Takeout `Records.json` and Semantic Location History. Upload it under Imports → New from a browser on the LAN; the import runs as a background job. Spot-check a few days you remember against the export.

## Re-queue track generation

Imports queue track building per day of history; a large import leaves thousands of jobs. If Valkey restarts or the pod is replaced before they finish, re-queue the remainder. It only touches points not yet in a track:

```sh
kubectl -n personal exec deploy/dawarich -c app -- bin/rails runner '
  u = User.sole
  Tracks::ParallelGeneratorJob.perform_later(u.id, start_at: Time.zone.at(u.points.minimum(:timestamp)), end_at: Time.zone.at(u.points.maximum(:timestamp)), mode: :bulk, untracked_only: true)'
```

## Phone tracking

The Dawarich iOS app connects with the server URL and the API key from Account → API access. Dawarich accepts the key as an `Authorization: Bearer` header or as `?api_key=`. Prefer a client that sends the header: the services gateway's access log keeps query strings, so a key in the URL is stored in VictoriaLogs until [#1422](https://github.com/kelchm/home-lab/issues/1422) redacts it. Confirm which form the iOS app uses from its first requests before relying on it. OwnTracks (`/api/v1/owntracks/points`) and Overland (`/api/v1/overland/batches`) also work. Uploads need NetBird connected when away; the app keeps unsent points and retries. Check that the queue survives a phone restart before relying on it, as described in #1419.

## Privacy notes

- Telemetry, analytics and the update check are off on a self-hosted instance. Reverse geocoding is off until a provider is configured.
- The place search falls back to public Nominatim when no geocoder is configured. The network policy blocks that, so the search returns nothing rather than leaking the query.
- The browser loads map tiles from `tyles.dwri.xyz` and fonts and sprites from `protomaps.github.io`. Those hosts see which map areas are viewed, not the stored points. Each user can point the map at another tile or style URL in the map settings.
