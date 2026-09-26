# Kubernetes logging

All Kubernetes container stdout/stderr is collected node-locally by Alloy from `/var/log/pods` and stored in VictoriaLogs, which is the only log backend.

## Query logs

- **Grafana Explore:** `https://grafana.home.kelch.io/explore` — select the `VictoriaLogs` datasource. This is the normal single-pane entry point. The query editor's `Run in VMUI` action opens the query in the external VMUI. `View as JSON` is useful when the complete structured row is more useful than the selected `_msg`.
- **VictoriaLogs VMUI:** `https://vlogs.home.kelch.io/select/vmui/` — use the native LogsQL explorer and live-stream view. The bare hostname redirects here.

The datasource plugin v0.31.0 serializes the VMUI handoff's absolute end time as UTC without a timezone suffix; VictoriaLogs v1.52.0 VMUI interprets it in its selected timezone. In an America/New_York browser, the September 26 check shifted a historical window by four hours. The plugin also omits seconds. For exact historical windows, set and verify the time range explicitly in VMUI; automatic absolute-time handoff is not yet accepted under [#485](https://github.com/kelchm/home-lab/issues/485). Upstream datasource v0.32.0 still uses the same serialization.

Grafana authenticates through its native `auth.generic_oauth` integration and dedicated Kanidm client; VictoriaLogs VMUI uses the OIDC middleware on its HTTPRoute. Useful LogsQL starting points are `namespace:observability`, `service_name:grafana`, `level:in("error", "critical")`, `stream:stderr`, and `namespace:observability | stats by (service_name) count()`. The time picker supplies the query window.

## Field model

Current rows use `cluster`, `namespace`, `service_name`, `pod`, `container`, and `node` as their stream identity. Alloy derives `service_name` from `app.kubernetes.io/name`, then legacy `app`, then the container name. `app_instance` preserves `app.kubernetes.io/instance` when present, but remains an ordinary field because it normally identifies an application or Helm release rather than a unique OpenTelemetry service instance.

Alloy normalizes explicit JSON or credible logfmt severity aliases to the Grafana-native top-level `level` values `trace`, `debug`, `info`, `warning`, `error`, and `critical`; fatal and panic aliases fold into `critical` while the original payload remains intact. `level` is an ordinary VictoriaLogs field rather than part of stream identity. Grafana also has bounded compatibility rules for retained rows whose pre-normalization severity exists only in `msg.level`; no general message-text inference is used.

Structured application fields are stored below `msg.*`, preventing payload keys such as `namespace` from replacing Kubernetes metadata. VictoriaLogs derives `_msg` from `message`, `msg`, `log`, `event`, or `record.message` after applying that prefix; non-JSON lines remain unchanged in `_msg`. Alloy strips ANSI terminal color escapes after reconstructing CRI partial records so stored messages remain readable and searchable.

Use ordinary field filters for `app_instance`, `level`, `filename`, and `stream`. For example, `stream:stderr` works across both pre-hardening and current data, while a stream selector such as `{stream="stderr"}` matches only older rows where `stream` was part of `_stream`.

Grafana and all three Traefik instances emit their supported JSON console/access-log formats. Other applications keep their native output until a measured source-specific change is justified. Do not add a global multiline rule: CRI partial records are already reconstructed, and legitimate separator output can resemble continuation lines. Likewise, do not infer stored severity from arbitrary words such as `error` or `info`; an honest unknown level is safer than a false classification.

Retention is nominally 30 days, but VictoriaLogs begins deleting the oldest partitions at 80% disk use. The effective retention is whichever limit is reached first.

## Source inventory

Measured 2026-09-18 over a 24-hour window with `stats by (service_name) count()` and its `NOT level:~".+"` variant. "No recognized severity" means the stored top-level `level` is absent or empty; it does not establish that a row is unstructured. This is a dated inventory of the deployed Alloy pipeline, not a complete list of sources or a current traffic baseline. The later [collector evaluation](../evaluations/kubernetes-log-collectors.md#stdoutstderr-and-severity) records additional source-level findings, including Broadsheet renderer warnings whose explicit prefix is not normalized by Alloy.

| Source | 24h entries | No recognized severity | Documented structured output | Decision |
| --- | --- | --- | --- | --- |
| kanidm | 163k | all | No JSON/log-event export found in the tested v1.11.2. `log_level` controls verbosity; OTLP exports trace spans. | Keep native output. The sample is dominated by INFO replication heartbeats and requests; any volume reduction must preserve useful operational and audit events. |
| iperf3 | 138k | all | Not evaluated for the idle benchmark server. | Observed bursts match tcpSocket readiness/liveness probes (10s/30s). Probe noise and whether to retain an always-on benchmark target belong to [#226](https://github.com/kelchm/home-lab/issues/226). |
| echo | 17k | all | Not needed to identify the observed health-check requests. | The sample is dominated by kubelet `/healthz` requests. It does not establish that the canary is unused; the earlier DNS observation is not a current reachability claim. Retention and exposure belong to [#226](https://github.com/kelchm/home-lab/issues/226). |
| kaniop | 12k | all | None documented in the evaluation. | Keep native output. |
| kube-apiserver | 11k | all | Yes: `--logging-format=json`. | Deferred: Talos-managed control-plane arguments require a separate manual rollout. Revisit if native JSON materially improves the accepted query workflow. |
| csi-driver-nfs | 9.1k | all | None established for the deployed klog emitter. | Keep native output. |
| csi-snapshotter | 9.0k | all | None established for the deployed klog emitter. | Keep native output. |
| mcphub | 8.3k | all | None documented in the evaluation. | Keep native output; explicit logger prefixes are covered in the later collector evaluation. |
| multus | 7.4k | all | None established for the deployed klog emitter. | Keep native output. |

Traefik access-log JSON has queryable request/status fields but lacks a message field selected by the ingestion configuration, so `_msg` displays a missing-message placeholder. Use Grafana's `View as JSON` to inspect these rows. Revisit message presentation during [#583](https://github.com/kelchm/home-lab/issues/583)'s operator-workflow acceptance; do not infer severity from arbitrary access-log text. Native source-format changes, noisy-log reduction and secret/PII redaction remain separate from the collector migration.

## Pipeline failures

Metrics-native rules page through VMAlertmanager when an Alloy target disappears, a node stops sending entries, retries persist, either layer drops data, VictoriaLogs becomes read-only or nearly fills its PVC, ingestion goes silent, or stream creation exceeds the measured rollout envelope. These alerts deliberately depend on the metrics path rather than querying the log backend they diagnose.

If an alert fires, inspect the affected Alloy component graph and writer metrics, the `victoria-logs-single-server-0` workload and PVC, and the corresponding vmagent scrape targets before restarting anything. Alloy persists file positions in `/var/lib/alloy`, but its sender queue is memory-only; prolonged sink outages can exhaust the bounded retry window.

Restarting Alloy while its sender queue contains unsent entries can lose those entries even though file positions survive. If backpressure prevents the tailer from reading files before rotation removes them, the writer's dropped-entry counter does not account for those unread records. Preserve collector state and inspect backend health before restarting a collector during an outage.

The [logging alert rules](../../kubernetes/apps/observability/victoria-metrics-k8s-stack/app/platform-alerts.yaml) provide partial detection. `AlloyNodeLogIngestionSilent` requires zero sent entries over a ten-minute window followed by fifteen minutes in that condition; it cannot detect partial loss while other entries continue arriving. The sustained-retry alert also waits fifteen minutes. Neither provides a guarantee that an alert will arrive before the retry budget is exhausted, and quiet alert history does not establish complete delivery.

The [collector evaluation](../evaluations/kubernetes-log-collectors.md) records delivery measurements and candidate tradeoffs. Alloy remains the deployed collector; its experimental writer WAL is disabled.
