# Exportarr

[Exportarr](https://github.com/onedr0p/exportarr) v2.3.0 runs one Deployment per app for Sonarr, Radarr, Lidarr, Prowlarr and Bazarr (`exportarr-<app>`). Each exporter reads its app through the app's Service with that app's API key from `arr-api-keys` and serves Prometheus metrics on port 9707. The apps' own pod templates, config volumes and settings are unchanged. SABnzbd is not exported yet; see below.

## Why Exportarr

Exportarr and [Scraparr](https://github.com/thecfu/scraparr) both cover all six apps. Exportarr was chosen because its stock dashboard (`examples/grafana/dashboard2.json`) has rows for all six apps. Scraparr's dashboard has none for Lidarr or SABnzbd. Exportarr also runs one small static binary per app, so each pod holds only its own key. Scraparr runs one process holding every key. Exportarr's default metrics carry no per-movie, per-series or per-user labels. Its bounded labels are quality, genre, tag, indexer, user agent, subtitle language, provider and score, root-folder path and health message.

As of 2026-11-14, v2.3.0, from August 2025, is the latest release. The v3 rewrite on upstream `main` (removes Readarr, adds a request timeout, partial-failure scrapes and request cloning) has no release or published image. Moving to v3 changes metric shapes, so it needs its own dashboard check.

## Behavior and limits

- **Read cost.** Every scrape calls the app's API live; v2.3.0 has no cache. The default collectors read whole-library list endpoints (Sonarr `series`, Radarr `movie`, Lidarr `artist`, Bazarr `movies`), the wanted/cutoff, queue, history, root-folder, status and health endpoints, and Prowlarr's indexer stats. The ServiceMonitor scrapes every 2 minutes with a 1-minute timeout. `ENABLE_ADDITIONAL_METRICS` stays off. It would add two calls per series in Sonarr and per artist in Lidarr, plus every Bazarr episode, on every scrape. Without it, Sonarr episode quality, Lidarr song quality and monitored albums, and Bazarr's episode-subtitle series are absent. Bazarr's combined `bazarr_subtitles_*` totals count movies only.
- **Authority.** The Starr apps have no read-only API key, so each exporter holds a full-control key for its app, although it only issues `GET` requests. These apps also trust any direct connection because authentication is external. Admitting an exporter in an app's Cilium policy therefore grants the same access as its key, as it does for the other integrations listed there.
- **Logging.** Starr keys travel in the `X-Api-Key` header, so request URLs and errors do not contain them. `LOG_LEVEL=warn` drops the per-request info line each scrape would write; errors still log, in JSON.
- **Memory and timeouts.** v2.3.0 does not close response bodies on 4xx/5xx and has no HTTP client timeout ([onedr0p/exportarr#432](https://github.com/onedr0p/exportarr/issues/432)). A wrong key or a failing app can grow memory; the 128 MiB limit bounds each exporter's memory use, and OOM restarts interrupt its scrapes. Investigate repeated restarts rather than treating the limit as a fix. The ServiceMonitor timeout bounds vmagent's wait; it does not guarantee that an already-running upstream API request stops.
- **Labels.** `job` is `exportarr`; the ServiceMonitor relabels `instance` to the app name (`sonarr`, `radarr`, …), which the dashboard's per-app selectors list. A collector failure returns HTTP 500 for that target's whole scrape (`up=0`).

## Network

`networkpolicy.yaml` admits only vmagent (label and ServiceAccount) to port 9707. Each exporter's egress is limited to DNS and its own app's container port. Each app's policy admits only its own exporter, by `app.kubernetes.io/controller` and the `exportarr-<app>` ServiceAccount identity. The victoria-metrics-k8s-stack policy gives vmagent egress to port 9707. There is no HTTPRoute.

## SABnzbd

SABnzbd only accepts its API key as an `apikey` query parameter. v2.3.0 adds it to the shared request URL inside its HTTP transport, so a connection-level failure, such as SABnzbd restarting, returns an error containing the full URL with the key. That error goes to the exporter's error log and into the HTTP 500 scrape body, which vmagent logs. Fluent Bit redacts `apikey=` only in Traefik request paths, Kubernetes Events and lines it cannot normalize, so these lines would store the key in VictoriaLogs. SABnzbd stays unexported until an Exportarr release no longer puts the key in errors. Upstream `main` clones the request before adding it, but that fix is unreleased.

## Dashboard

`dashboards/exportarr.json` is based on `dashboard2.json` from the v2.3.0 tag ("Media Dashboard", uid `WURH98Y4k`), filed in `Services/Media`. Later upstream revisions dropped the Bazarr row. Local patches remove the import placeholder and fixed datasource selection, point uptime panels at the apps' `app` containers, and fit the dashboard to the five deployed exporters. The SABnzbd and Readarr rows are omitted, as are panels and targets requiring additional metrics or nonexistent Bazarr queue/history series. Bazarr panels explicitly cover movie subtitles; episode subtitles remain uncollected.

The Prowlarr failure percentage uses increases over the selected range in both its numerator and denominator, and its health count sums the issue series. The user-agent query chart uses a count unit and a 2-minute target Min step so `$__rate_interval` accounts for the scrape interval. Indexer, user-agent, quality and health breakdowns can still be empty when the app has no matching activity or issues. Labels, variables and panels have not been checked against scraped data yet. [Issue 746](https://github.com/kelchm/home-lab/issues/746) tracks that acceptance.

## Rotation

Rotate a key in the app, update `arr-api-keys` (and `homepage-secret`) as the [credential rotation table](../../../../docs/runbooks/seerr-bootstrap.md#credential-rotation) describes, then restart `deploy/exportarr-<app>`; the exporter reads its key only at startup.
