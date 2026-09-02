# Kubernetes logging

All Kubernetes container stdout/stderr is collected node-locally by Alloy from `/var/log/pods` and stored in VictoriaLogs. Loki remains a temporary rollback sink only until the observability cleanup.

## Query logs

- **Grafana Explore:** `https://grafana.home.kelch.io/explore` — select the `VictoriaLogs` datasource. This is the normal single-pane entry point.
- **VictoriaLogs VMUI:** `https://vlogs.home.kelch.io/select/vmui/` — use the native LogsQL explorer and live-stream view. The bare hostname redirects here.

Both routes use the cluster's Kanidm OIDC protection. Useful LogsQL starting points are `namespace:observability`, `namespace:observability _msg:error`, `stream:stderr`, and `namespace:observability | stats by (pod) count()`. The time picker supplies the query window.

## Field model

Current rows use `cluster`, `namespace`, `pod`, `container`, and `node` as their stream identity. Use ordinary field filters for `filename` and `stream`; for example, `stream:stderr` works across both pre-hardening and current data, while a stream selector such as `{stream="stderr"}` matches only older rows where `stream` was part of `_stream`.

Structured application fields are stored below `msg.*`, preventing payload keys such as `namespace` from replacing Kubernetes metadata. VictoriaLogs derives `_msg` from `message`, `msg`, `log`, `event`, or `record.message` after applying that prefix; non-JSON lines remain unchanged in `_msg`.

Retention is nominally 30 days, but VictoriaLogs begins deleting the oldest partitions at 80% disk use. The effective retention is whichever limit is reached first.

## Pipeline failures

Metrics-native rules page through VMAlertmanager when an Alloy target disappears, a node stops sending entries, retries persist, either layer drops data, VictoriaLogs becomes read-only or nearly fills its PVC, ingestion goes silent, or stream creation exceeds the measured rollout envelope. These alerts deliberately depend on the metrics path rather than querying the log backend they diagnose.

If an alert fires, inspect the affected Alloy component graph and writer metrics, the `victoria-logs-single-server-0` workload and PVC, and the corresponding vmagent scrape targets before restarting anything. Alloy persists file positions in `/var/lib/alloy`, but its sender queue is memory-only; prolonged sink outages can exhaust the bounded retry window.
