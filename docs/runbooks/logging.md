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

## Source inventory

Measured 2026-09-18 over a 24-hour window against the live backend with `stats by (service_name) count()` and its `NOT level:~".+"` variant, so the "plain text" column is exactly the share of each source's entries that the normalizer could not classify. Sources absent from the table were either below ~7k entries/day or already emit a recognizable top-level `level` (logfmt emitters such as cilium-agent, longhorn, and broadsheet normalize cleanly). Per #485 Phase 2, native structured output is enabled only where the application documents it, and noisy-log reduction plus secret/PII redaction are tracked separately rather than via generic collector regexes.

| Source | 24h entries | Plain text | Documented structured output | Decision |
| --- | --- | --- | --- | --- |
| kanidm | 163k | all | None. Only `log_level` (info/debug/trace); the OTLP integration exports trace spans to a tracing backend, not log events. | Keep plain text. Volume is dominated by `repl_run_consumer` heartbeat ticks at INFO (~every 2.5s per replica) plus per-request logs; pursue upstream before any collector-side transform. |
| iperf3 | 138k | all | N/A (hostNetwork benchmark server, network-perf). | Idle: no benchmark traffic was observed — the entire volume is probe self-noise. The tcpSocket readiness/liveness probes (10s/30s) connect-and-close, which the iperf3 server logs as failed clients (~4 lines per 10s burst per pod). Replace with exec probes or retire the DaemonSet if the benchmark target is no longer wanted. |
| echo | 17k | all | N/A (http-echo canary in `default`, HTTPRoute on gateway-public). | Idle: the volume is kubelet healthz probe spam (two probes every 10s). Also publicly unreachable — the `echo.kelch.io` DNS record no longer resolves even though the HTTPRoute is Accepted, so the canary serves no purpose today. Decide: restore DNS, move it under the internal zone, or decommission. |
| kaniop | 12k | all | None documented. | Keep plain text. |
| kube-apiserver | 11k | all | Yes: `--logging-format=json`. | Not flipped: apiserver args are Talos-managed control-plane config outside Flux, so this is a manual rollout decision via the talos-rollout process, not a HelmRelease change. |
| csi-driver-nfs | 9.1k | all | None (klog emitters). | Keep plain text. |
| csi-snapshotter | 9.0k | all | None (klog emitters). | Keep plain text. |
| mcphub | 8.3k | all | None documented. | Keep plain text. |
| multus | 7.4k | all | None (klog emitters). | Keep plain text. |

A known presentation wart: Traefik access-log JSON rows carry no `msg` key (fields such as `msg.RequestAddr` and `msg.DownstreamStatus` hold the request detail), so VictoriaLogs substitutes its `missing _msg field` placeholder for those rows. The underlying fields remain fully queryable and console-log rows are unaffected. Synthesizing a readable `_msg` for Traefik would require a collector-side transform targeted at one source, which the Phase 2 guardrails rule out for now; revisit only if VMUI access-log readability becomes a real workflow gap.

## Pipeline failures

Metrics-native rules page through VMAlertmanager when an Alloy target disappears, a node stops sending entries, retries persist, either layer drops data, VictoriaLogs becomes read-only or nearly fills its PVC, ingestion goes silent, or stream creation exceeds the measured rollout envelope. These alerts deliberately depend on the metrics path rather than querying the log backend they diagnose.

If an alert fires, inspect the affected Alloy component graph and writer metrics, the `victoria-logs-single-server-0` workload and PVC, and the corresponding vmagent scrape targets before restarting anything. Alloy persists file positions in `/var/lib/alloy`, but its sender queue is memory-only; prolonged sink outages can exhaust the bounded retry window.
