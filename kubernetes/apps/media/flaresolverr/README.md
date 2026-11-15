# FlareSolverr

Prowlarr uses the internal API on port 8191. The separate Prometheus listener on port 8192 is enabled with `PROMETHEUS_ENABLED=true` and `PROMETHEUS_PORT=8192`. The chart creates a ServiceMonitor (30-second interval), which VictoriaMetrics Operator converts to a VMServiceScrape. Cilium admits only Prowlarr to the API and only vmagent in `observability` to the metrics port. [#1499](https://github.com/kelchm/home-lab/pull/1499) and [#1501](https://github.com/kelchm/home-lab/pull/1501) record the rollout and its verification.

## Metrics

The pinned [v3.5.2 metrics implementation](https://github.com/FlareSolverr/FlareSolverr/blob/v3.5.2/src/metrics.py) exports `flaresolverr_request_total` with `domain` and `result` labels, and a `flaresolverr_request_duration` histogram with a `domain` label and buckets at 0, 10, 25 and 50 seconds. [The request plugin](https://github.com/FlareSolverr/FlareSolverr/blob/v3.5.2/src/bottle_plugins/prometheus_plugin.py) counts every `/v1` response and skips health and index responses.

- `result` is `solved` ("Challenge solved!"), `not_detected` (the page loaded without a challenge), `error` (any "Error: …" response, including timeouts and blocked requests) or `unknown` (session commands and other messages).
- `domain` is the hostname of the solution URL, or of the requested URL when there is no solution, and `unknown` when neither exists.
- Each label combination appears only after its first request, so a result that has not happened since the pod started has no series rather than a zero. The counters reset when the pod restarts.
- `instance` is the pod IP and changes with every pod. `job="flaresolverr"` is the stable identity.

## Dashboard

`app/dashboards/flaresolverr.json` is [grafana.com dashboard 19363](https://grafana.com/grafana/dashboards/19363-flaresolverr/) revision 1, its only revision ("FlareSolverr", uid `c51691e6-d80f-40e1-a2fb-896c8c03690a`), filed in `Services/Media`. Upstream graphs the raw counters, which count since the pod started rather than over the selected time, and its stat panels show 0 when there is no data. Local patches:

- Remove `__inputs` and point every panel at the `data_source` variable, with no fixed current value, so it uses the default VictoriaMetrics datasource. Refresh is 30 seconds instead of 5, matching the scrape interval.
- The Host variable keeps its upstream query but defaults to All (`.*`), and every panel, including the upstream stat panels that ignored it, selects `instance=~"$host"`. Upstream selected a single pod IP, which hides history from earlier pods.
- Challenges, Requests and Average Request Duration take `increase` over `$__interval` with a 30-second target Min step. Each request lands in exactly one interval, so Requests' legend total, which upstream showed as the last value, is the range count. Average duration divides the duration sum's increase by the count's increase, so intervals without requests are gaps.
- The three stat panels and the Domains table are instant queries over `$__range`, totals for the selected time range rather than the counter's current value. They have no NoData-to-zero mapping. A stat reads 0 for an absent result only while `up{job="flaresolverr"}` is 1 at the range's end, and Challenges draws 0 only at steps where `up` is 1. If the target is down or gone, those panels show No data or gaps instead of zero. The table lists domains with requests in the range and fills their absent results with computed zeros.

Not Solved covers only `error`. `unknown` responses count under Requests and are left out of Challenges and the Domains sum. The histogram's buckets are too coarse for useful percentiles, so the dashboard keeps upstream's average.

Grafana's API checks found every panel query and the Host variable matched the stored data. At that point there had been one request, Prowlarr's `not_detected` call to `prowlarr.servarr.com`. That shows the metrics and queries fit. It does not prove that FlareSolverr solves challenges correctly, and the `solved` and `error` series did not exist yet.

Recheck panel queries after a FlareSolverr upgrade that changes the metrics or labels.
