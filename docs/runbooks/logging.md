# Kubernetes logging

All Kubernetes container stdout/stderr is collected node-locally by Alloy from `/var/log/pods` and stored in VictoriaLogs. Loki remains a temporary rollback sink only until the observability cleanup.

## Query logs

- **Grafana Explore:** `https://grafana.home.kelch.io/explore` — select the `VictoriaLogs` datasource. This is the normal single-pane entry point. The query editor's `Run in VMUI` action carries the current query and time range to the external VMUI, and `View as JSON` is useful when the complete structured row is more useful than the selected `_msg`.
- **VictoriaLogs VMUI:** `https://vlogs.home.kelch.io/select/vmui/` — use the native LogsQL explorer and live-stream view. The bare hostname redirects here.

Grafana authenticates through its native `auth.generic_oauth` integration and dedicated Kanidm client; VictoriaLogs VMUI uses the OIDC middleware on its HTTPRoute. Useful LogsQL starting points are `namespace:observability`, `service_name:grafana`, `level:in("error", "critical")`, `stream:stderr`, and `namespace:observability | stats by (service_name) count()`. The time picker supplies the query window.

## Field model

Current rows use `cluster`, `namespace`, `service_name`, `pod`, `container`, and `node` as their stream identity. Alloy derives `service_name` from `app.kubernetes.io/name`, then legacy `app`, then the container name. `app_instance` preserves `app.kubernetes.io/instance` when present, but remains an ordinary field because it normally identifies an application or Helm release rather than a unique OpenTelemetry service instance.

Alloy normalizes explicit JSON or credible logfmt severity aliases to the Grafana-native top-level `level` values `trace`, `debug`, `info`, `warning`, `error`, and `critical`; fatal and panic aliases fold into `critical` while the original payload remains intact. `level` is an ordinary VictoriaLogs field rather than part of stream identity. Grafana also has bounded compatibility rules for retained rows whose pre-normalization severity exists only in `msg.level`; no general message-text inference is used.

Structured application fields are stored below `msg.*`, preventing payload keys such as `namespace` from replacing Kubernetes metadata. VictoriaLogs derives `_msg` from `message`, `msg`, `log`, `event`, or `record.message` after applying that prefix; non-JSON lines remain unchanged in `_msg`. Alloy strips ANSI terminal color escapes after reconstructing CRI partial records so stored messages remain readable and searchable.

Use ordinary field filters for `app_instance`, `level`, `filename`, and `stream`. For example, `stream:stderr` works across both pre-hardening and current data, while a stream selector such as `{stream="stderr"}` matches only older rows where `stream` was part of `_stream`.

Grafana and all three Traefik instances emit their supported JSON console/access-log formats. Other applications keep their native output until a measured source-specific change is justified. Do not add a global multiline rule: CRI partial records are already reconstructed, and legitimate separator output can resemble continuation lines. Likewise, do not infer stored severity from arbitrary words such as `error` or `info`; an honest unknown level is safer than a false classification.

Retention is nominally 30 days, but VictoriaLogs begins deleting the oldest partitions at 80% disk use. The effective retention is whichever limit is reached first.

## Pipeline failures

Metrics-native rules page through VMAlertmanager when an Alloy target disappears, a node stops sending entries, retries persist, either layer drops data, VictoriaLogs becomes read-only or nearly fills its PVC, ingestion goes silent, or stream creation exceeds the measured rollout envelope. These alerts deliberately depend on the metrics path rather than querying the log backend they diagnose.

If an alert fires, inspect the affected Alloy component graph and writer metrics, the `victoria-logs-single-server-0` workload and PVC, and the corresponding vmagent scrape targets before restarting anything. Alloy persists file positions in `/var/lib/alloy`, but its sender queue is memory-only; prolonged sink outages can exhaust the bounded retry window.
