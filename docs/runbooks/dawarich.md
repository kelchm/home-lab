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
3. **Permanent bootstrap.** The first start used `bootstrap.initdb`, which created the empty database and its PostGIS and pgcrypto extensions. After the first base backup completed on 2026-11-11, `cluster.yaml` switched to the `bootstrap.recovery` stanza and `cnpg.io/skipEmptyWalArchiveCheck` annotation the other databases use, so a rebuild restores the archive instead of starting empty. CloudNativePG reads the bootstrap only when it creates a data directory; the switch did not touch the running database.

## Import Google Timeline

Use the phone's own export (Google Maps → Settings → Personal content → Export Timeline data). Dawarich detects the format, including the on-device `Timeline.json`, Takeout `Records.json` and Semantic Location History. Upload it under Imports → New from a browser on the LAN; the import runs as a background job. Spot-check a few days you remember against the export.

## Re-queue track generation

Imports queue track building per day of history; a large import leaves thousands of jobs. If Valkey restarts or the pod is replaced before they finish, re-queue the remainder. It only touches points not yet in a track:

```sh
kubectl -n personal exec deploy/dawarich -c app -- bin/rails runner '
  User.find_each do |u|
    first, last = u.points.pick(Arel.sql("MIN(timestamp), MAX(timestamp)"))
    next unless first
    Tracks::ParallelGeneratorJob.perform_later(u.id, start_at: Time.zone.at(first), end_at: Time.zone.at(last), mode: :bulk, untracked_only: true)
  end'
```

It runs for every user with points; `untracked_only` makes it a no-op where tracks are complete.

## Reverse geocoding

[Photon](https://github.com/komoot/photon), from `rtuszik/photon-docker`, runs beside Dawarich in `personal` with the prebuilt US index (`REGION=usa`, about 10.6 GB compressed, rebuilt weekly upstream). Dawarich reaches it at `photon.personal.svc.cluster.local:2322`, capped at 25 lookups per second. Every location in the archive is in the US; a trip abroad needs a larger region, such as `north-america` or `planet`.

- **First start** downloads and unpacks the index before Photon listens. On 2026-11-11 it took about 14 minutes: a 9.85 GB download at about 195 Mbit/s, 7 minutes unpacking and a 15-second start. Lookups made meanwhile fail and are retried by Dawarich's nightly geocoding job.
- **Measured use** (2026-11-11): the unpacked US index is 14 GB of the 49 GB volume, so an update peaks around 38 GB. Photon uses 2.1 GiB of memory idle and peaked at 2.7 GiB during the backfill.
- **Updates**: photon-docker checks every 30 days and, with `UPDATE_STRATEGY=PARALLEL`, downloads and unpacks the new index beside the current one while Photon keeps serving, then restarts Photon briefly to switch to it. Lookups during that restart fail and are retried by the nightly geocoding job. The 50 GiB volume is sized to hold two indexes and the compressed download; the first install left 36 GB free, enough for an update by about 10 GB. It is excluded from Longhorn backups because the index can be downloaded again.
- **Backfill**: Dawarich looks up each point once and stores the result. To geocode existing points, use Settings → Background jobs → **Continue reverse geocoding**, or let the nightly job at 01:15 queue them. Either queues one job per point, about 290k for the imported history, which is why Valkey's cap is 768 MB. The 2026-11-11 backfill of 289,961 points ran at the 25-per-second cap for 3 hours 15 minutes with no failed jobs: 97.4% of points got a city and 99.9% a country (the rest are off the coast or outside any city), across 1,375 distinct cities.
- **Re-geocoding** already geocoded points after an index update is optional and manual (**Start reverse geocoding**); it redoes every point.

## Phone tracking

The Dawarich iOS app connects with the server URL and the API key from Account → API access. Dawarich accepts the key as an `Authorization: Bearer` header or as `?api_key=`. Prefer a client that sends the header: the services gateway's access log keeps query strings, so a key in the URL is stored in VictoriaLogs until [#1422](https://github.com/kelchm/home-lab/issues/1422) redacts it. Confirm which form the iOS app uses from its first requests before relying on it. OwnTracks (`/api/v1/owntracks/points`) and Overland (`/api/v1/overland/batches`) also work. Uploads need NetBird connected when away; the app keeps unsent points and retries. Check that the queue survives a phone restart before relying on it, as described in #1419.

## Privacy notes

- Telemetry, analytics and the update check are off on a self-hosted instance.
- Reverse geocoding and place search go to the in-cluster Photon only. Dawarich still has no internet egress, so a misconfigured geocoder fails closed rather than falling back to public Nominatim.
- The browser loads map tiles from `tyles.dwri.xyz` and fonts and sprites from `protomaps.github.io`. Those hosts see which map areas are viewed, not the stored points. Each user can point the map at another tile or style URL in the map settings.
