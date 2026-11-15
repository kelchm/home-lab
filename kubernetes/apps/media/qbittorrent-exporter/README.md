# qBittorrent exporter

[prometheus-qbittorrent-exporter](https://github.com/esanchezm/prometheus-qbittorrent-exporter) v1.8.0 runs as one Deployment (`qbittorrent-exporter`). It reads qBittorrent through its Service with the Web UI API key from `arr-api-keys` and serves Prometheus metrics on port 8000. qBittorrent's pod, config volume and settings are unchanged.

## Why this exporter

It is the exporter [issue 746](https://github.com/kelchm/home-lab/issues/746) named, it is maintained (1.7.1 and 1.8.0 shipped in October 2026), and its 1.7.1 release added `QBITTORRENT_API_KEY`, which sends qBittorrent 5.2+'s API key as a bearer header. That is the only way an exporter can authenticate here: the L7 rule described below takes the connection out of qBittorrent's pod-CIDR auth bypass, so a username/password exporter or one relying on the bypass would get 403. The image is multi-arch (amd64, arm64, 386); `org.opencontainers.image.revision` is the 1.8.0 tag commit.

## Behavior and limits

- **Metrics.** Server state (`qbittorrent_up` with a `version` label, `connected`, `firewalled`, `dht_nodes`, `total_peer_connections`), session and all-time transfer counters, and `qbittorrent_torrents_count` per `category` and `status`. Every series carries `server="qbittorrent.media.svc.cluster.local:8080"`. Torrent counts are roughly (categories + 1) × 22 states. `EXPORT_METRICS_BY_TORRENT` stays off: it would add `qbittorrent_torrent_size` and `qbittorrent_torrent_downloaded` with each torrent's name as a label.
- **Read cost.** A successful scrape makes five calls: `GET app/version`, `GET app/webapiVersion`, `GET torrents/categories`, `POST sync/maindata` (full sync, `rid=0`) and `POST torrents/info` (every torrent). The exporter only uses the server state from the sync data. The ServiceMonitor scrapes every minute with a 30-second timeout.
- **Failures.** The exporter catches API errors and still answers the scrape with HTTP 200, so the target's `up` stays 1. Use `qbittorrent_up`. A failed scrape reports `qbittorrent_up{version=""} 0` and every counter and gauge as 0, which looks like a counter reset. The dashboard's rate panels therefore skip zero samples (see below). qbittorrent-api times out each request after 15.1 seconds and retries, so a qBittorrent that accepts connections but never answers makes one scrape take about two minutes. vmagent gives up after 30 seconds, and the exporter's per-scrape threads finish on their own.
- **Logging.** The default `INFO` level logs two startup lines, then only failures: one line per failed call, naming the endpoint and the error, and urllib3 retry warnings for connection errors. The key travels only in the `Authorization` header. Pinned-image tests with dummy keys against qBittorrent 5.3.0 found it in none of these lines, for a wrong key, an HTTP 403 from a proxy, DNS failure, connection refused and a hung upstream.
- **Authority.** qBittorrent API keys have no scopes: the key has the same full control as the Web UI. What the exporter can do with it is bounded by qBittorrent's Cilium policy, which admits only the five calls above for this identity. Anything that can read `arr-api-keys` in the `media` namespace can read the key.
- **Probes.** Every HTTP path on the exporter runs a full collection, so the probes are TCP checks. The first collection runs before the listener opens; the startup probe allows three minutes for it.

## Network

`networkpolicy.yaml` admits only vmagent (label and ServiceAccount) to port 8000, and limits the exporter's egress to DNS and qBittorrent's port 8080. qBittorrent's policy admits the exporter by `app.kubernetes.io/name` and the `qbittorrent-exporter` ServiceAccount identity, with an HTTP rule for exactly those five method/path pairs. Like Homepage's rule, it sends the connection through Cilium's L7 proxy, whose upstream source is outside the pod-CIDR auth bypass, so the request needs the API key. The exporter's login attempt after a 403 (`POST auth/login`) and its scheme probe of `/` after a connection error are denied by the proxy. The victoria-metrics-k8s-stack policy gives vmagent egress to port 8000. There is no HTTPRoute.

These rules rely on Cilium enforcing qBittorrent's policy. [Issue 1348](https://github.com/kelchm/home-lab/issues/1348) found that endpoint unenforced after a Cilium agent restart; adding a rule recomputes the endpoint's policy. After deployment, confirm that qBittorrent's endpoint enforces and rerun `scripts/verify-qbittorrent-boundary.sh`.

## Dashboard

`dashboards/qbittorrent.json` is upstream `grafana/dashboard.json` at tag 1.8.0 ("Qbittorrent", uid `eKyTETFMk`), filed in `Services/Media`. Local patches:

- Remove `__inputs` and point every panel at the dashboard's `data_source` variable, with no fixed current value, so it uses the default VictoriaMetrics datasource.
- Status takes `max by (server)` of `qbittorrent_up`. A failure creates a second series with `version=""`, and without this the panel shows both "Online" and "Offline" while that series is in range.
- Download speed, Upload speed and Transfer Rates use `rate((counter > 0)[$__rate_interval:]) or counter * 0` with a 1-minute Min step. Upstream's `rate(counter[2m])` turns a failed scrape's zero into a spike equal to the whole session total divided by the window. Against VictoriaMetrics v1.156.0 with synthetic data, the upstream query showed 421 MB/s for a steady 1 MB/s after one failed scrape; the patched query stayed at 1 MB/s at 15-second to 5-minute steps and handled a real qBittorrent restart as a counter reset.

Session and all-time totals, the ratio, connection status, DHT nodes and torrent counts also read 0 during a failed scrape; the Status panel shows why. Recheck panel queries after changing the exporter version or its labels.

## Rotation

The key is a copy of qBittorrent's Web UI API key, which `homepage-secret` also holds. See the [credential rotation section](../../../../docs/runbooks/seerr-bootstrap.md#credential-rotation), then restart `deploy/qbittorrent-exporter`. The exporter reads its key only at startup.
