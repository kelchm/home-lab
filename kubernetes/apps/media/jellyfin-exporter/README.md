# Jellyfin exporter

[jellyfin_exporter](https://github.com/rebelcore/jellyfin_exporter) v1.6.0 runs as one Deployment (`jellyfin-exporter`). It reads Jellyfin through its Service with a copy of Homepage's Jellyfin API key and serves Prometheus metrics on port 9594. Jellyfin's pod, settings, route and API keys are unchanged.

## Why this exporter

It is the exporter [issue 746](https://github.com/kelchm/home-lab/issues/746) named, and it is maintained: 1.5.3 and 1.6.0 shipped in October 2026, and the last push was November 9. The image is built from scratch, runs as user 65534 and is multi-arch (amd64 included). The pinned index digest carries a Sigstore signature from the upstream `publish.yml` workflow for a version tag, and the binary reports revision `a886a700`, the `v1.6.0` tag commit.

Jellyfin's native `EnableMetrics` setting is not needed and stays off: it serves .NET runtime and HTTP metrics, while the stock dashboard reads only this exporter's library, session, storage and task metrics. Jellyfin's route answers `/metrics` with 404, and this change keeps it that way.

## Behavior and limits

- **Collectors.** `system`, `media`, `playing`, `transcoding`, `tasks` and `storage`. `users` (accounts, last access, client IPs) and `activity` (Playback Reporting plugin totals per user) are off because every series they produce is per person.
- **Privacy.** `playing` and `transcoding` label each session with the user, device, client, IP address and title. The ServiceMonitor drops those labels at scrape time. Stored playback labels retain `session_id`, media type, play method, container, codecs, hardware acceleration and transcode reasons; system, storage and task metadata also remains. Jellyfin derives `session_id` deterministically from the client application, device ID and account, so it is a persistent pseudonymous identifier that can correlate playback over time. Retaining it preserves concurrent-session uniqueness; this is session-level monitoring, not anonymized aggregate-only storage. The exporter's own `/metrics` output still contains the dropped labels, so only vmagent may reach it.
- **Read cost.** Each scrape makes seven GET calls: `/System/Ping`, `/System/Info`, `/System/Info/Storage`, `/Items/Counts`, `/ScheduledTasks` and two `/Sessions` queries. Against the live server they took 20–150 ms each; `/Items/Counts` (one library count query) is the slowest, and `/System/Info/Storage` runs `statfs` on Jellyfin's folders, including the NFS library. The ServiceMonitor scrapes every minute with a 30-second timeout. Jellyfin 12.4 ignores the `IsPlaying` filter on `/Sessions`; the exporter skips sessions with nothing playing itself.
- **Failures.** Collectors run in parallel, each request times out after 5 seconds, and the scrape still answers HTTP 200. `jellyfin_scrape_collector_success` reports each collector. `jellyfin_up` comes from the unauthenticated ping, so a rejected key shows `jellyfin_up 1` with every other collector failing. A failed collector omits its series rather than reporting zeros.
- **Logging.** The default `info` level logs startup lines, then one line per failed call with the URL and error. A non-2xx response adds at most 1 KiB of its body. The key travels only in the `Authorization` header. Pinned-image tests with dummy keys found it in no log line or metric for a wrong key (401), a proxy 403, connection refused, DNS failure and a hung upstream.
- **Authority.** Jellyfin 12.4 gives every API key the Administrator role and skips user policy checks ([`CustomAuthenticationHandler`](https://github.com/jellyfin/jellyfin/blob/v12.4/Jellyfin.Api/Auth/CustomAuthenticationHandler.cs), [`DefaultAuthorizationHandler`](https://github.com/jellyfin/jellyfin/blob/v12.4/Jellyfin.Api/Auth/DefaultAuthorizationPolicy/DefaultAuthorizationHandler.cs)), and the live key reads the elevation-only `/System/Info/Storage`. Keys have no scopes. What this pod can do with the key is bounded by its egress policy. Anything that can read the `jellyfin-exporter` or `homepage-secret` Secret holds full Jellyfin admin rights.
- **Probes.** HTTP checks on `/`, the landing page, which does not query Jellyfin.

## Network

`networkpolicy.yaml` admits only vmagent (label and ServiceAccount) to port 9594 and limits egress to DNS for `jellyfin.media.svc.cluster.local` and Jellyfin's port 8096. The pod uses `ndots:1` to resolve that service name as absolute first; arbitrary external DNS names are not admitted. The Jellyfin rule carries an HTTP allowlist of exactly the seven method/path pairs above, so Cilium's L7 proxy answers any other request from this pod, including a write or another admin read, with 403. Cilium matches each path as an RE2 regex against the whole request target, query string included. Enabling another collector or upgrading the exporter can add calls, which then fail with 403 until the policy lists them. The victoria-metrics-k8s-stack policy gives vmagent egress to 9594. There is no HTTPRoute.

Jellyfin has no ingress policy, and this change adds none: a policy selecting Jellyfin would put it into default deny for every client. The allowlist therefore lives on the exporter's egress, and Jellyfin's other clients are unaffected.

The allowlist relies on Cilium enforcing the exporter's endpoint. [Issue 1348](https://github.com/kelchm/home-lab/issues/1348) found endpoints unenforced after a Cilium agent restart when they reused a stale identity; the exporter's labels and ServiceAccount give it a new identity, which that issue found enforces. After deployment, and after any agent restart before Cilium 1.20.5, run `.agents/skills/talos-rollout/scripts/policy-enforcement.sh` and confirm the exporter's and vmagent's endpoints enforce.

## Dashboard

`dashboards/jellyfin.json` is upstream [`rebelcore/jellyfin_grafana`](https://github.com/rebelcore/jellyfin_grafana) `dashboards/jellyfin.json` at `a14c4a046160f861a93e4a847bb1916f715ccaa2` (Release 1.6.0; the repository has no tags), titled "Jellyfin" with uid `603679cbda70a9fe`, filed in `Services/Media`. Local patches:

- Add a `data_source` variable with no fixed current value, so the dashboard uses the default VictoriaMetrics datasource, and point every panel at it.
- Status, Pending Restart, Active Streams, Transcoding, the media counts, Storage Usage % and Collector Status use instant queries. A range reducer would otherwise show the last value of a series that has stopped. Status and Pending Restart take `max()`. Transcoding takes `sum()`.
- Active Streams falls back to 0 only while the `playing` collector succeeds. Upstream's `or vector(0)` showed 0 when the exporter or Jellyfin was down.
- Remove Connected Users, the Users row (User Accounts, Active Sessions) and the Activity row (Playback Activity). They need the disabled `users` and `activity` collectors.
- Replace the per-user, per-title Now Playing state timeline with a stacked time series of `count by (type, method)`.
- Active Streams and Now Playing count sessions with an item loaded, including paused playback.
- The transcoding tables and bitrate chart group by `session_id` and the kept codec labels instead of user and title.
- Storage queries filter Jellyfin's negative unknown-space values before calculating capacity or percentages. Storage inspection can fail while the collector's HTTP request still succeeds. All-unknown locations show NoData. Library rows use `max without(name,path)` because the current folders share the `media-library` filesystem; this does not detect distinct filesystems automatically. Keep separate rows if libraries move to independent filesystems. The Storage Details footer is hidden because location rows can repeat the same underlying capacity.
- Range panels (Now Playing, Transcoding Bitrate, Library Storage Over Time, Collector Scrape Duration) get a 1m Min interval to match the scrape interval. No panel uses `rate()`.
- Re-lay rows top to bottom after the removals.

Every expression was checked against VictoriaMetrics v1.156.0 scraping the pinned exporter through the same relabeling, with a fake Jellyfin serving 12.4-shaped responses: all returned data and no dropped label was stored. When the exporter stopped, current-state panels went to NoData and history panels kept their data. With Jellyfin unreachable, Status read 0 and the other current panels went to NoData. With no sessions, Active Streams and Transcoding read 0. The media count panels for types this library lacks (albums, songs, books and the like) read a real 0. Recheck panel queries after changing the exporter version, collectors or relabeling.

## Rotation

The key is the Jellyfin API key named `homepage` (Jellyfin dashboard → API Keys). `HOMEPAGE_VAR_JELLYFIN_KEY` in `homepage-secret` and `JELLYFIN_TOKEN` in this app's `secret.sops.yaml` both hold it. After replacing it in Jellyfin, update both Secrets (`secret.sops.yaml`'s header shows a `sops set` form that keeps the key out of process arguments), then `kubectl -n media rollout restart deploy/jellyfin-exporter`. The exporter reads its key only at startup; Reloader restarts Homepage.
