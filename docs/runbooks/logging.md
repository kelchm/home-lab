# Kubernetes logging

All Kubernetes container stdout/stderr is collected node-locally by Alloy from `/var/log/pods` and stored in VictoriaLogs, which is the only log backend.

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

## Durability posture (#581 evaluation, 2026-09-22)

The retention of Alloy is a measured decision, not momentum. The 2026-09-22 evaluation (issue #581, superseding the replatform proposal in #575) deployed vlagent v1.52.0, Vector v0.58.0, and prod-identical Alloy arms — one with the experimental `loki.write` WAL — against four scratch VictoriaLogs backends in a drill namespace and ran outage and saturation drills with numbered, countable fixtures.

Measured results: vlagent and Vector replayed 100% of a 12,839-row fixture stream across an 11-minute backend outage that included deleting every collector pod mid-outage; Alloy replayed 51% and lost exactly the entries buffered in memory before the restart. Under a 64-minute saturation outage at ~1.4MB/s, vlagent engaged its documented drop-oldest at `-remoteWrite.maxDiskUsagePerURL` (retaining the most recent ~512MB compressed window), Vector's disk buffer silently stopped accepting at cap with no drop counter or log line and permanently lost the excess, and the Alloy WAL arm delivered 97% of a steady fixture stream versus 30% for the baseline — but the WAL did not replay across the mid-outage collector restart in the first drill, deleted undelivered data when segments crossed `max_segment_age=1h`, and holds roughly one hour of ingest on disk with no size cap. At this cluster's real volume (~0.7KB/s average) vlagent's buffer equals roughly 8–9 days of outage versus Alloy's ~10-minute retry window, but vlagent cannot express the `service_name` fallback, the #487 severity normalization, or the incumbent stream identity and field model (all verified, not assumed), so the migration cost is a full field-model and query-compatibility rework.

Accepted limitations of the retained pipeline, now with evidence: beyond retry exhaustion (~10 minutes of continuous backend unavailability), the pipeline has a second, uncounted loss mode — `loki.write` `block_on_overflow` backpressures the file tailer, kubelet/containerd log rotation (10MB × 5 files) deletes unread rotated files, and `loki_write_dropped_entries_total` does not increment for this loss. Node-silence alerting is the coverage for that mode; it fired by design during the drill. Revisit the collector choice only if outage tolerance beyond ~10 minutes becomes a requirement or vlagent grows transform capability: vlagent is the credible successor (restart-safe buffer, lowest resources, first-party), and the migration budget is a field-model migration (severity normalization via VL-side `logLevelRules`/`detected_level`, stream-identity rework, runbook query rewrites, Grafana provisioning, and successor CI contract) rather than a config change. Do not enable Alloy's experimental `loki.write` WAL: it is restart-lossy, age-deletes undelivered data silently, and is uncapped.
