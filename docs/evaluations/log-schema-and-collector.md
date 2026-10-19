# Log schema and collector evaluation

Evaluated 2026-10-18. Scope: how Kubernetes container logs are shaped and collected for VictoriaLogs, so Grafana can browse them by service, severity and message without per-source query knowledge. The [logging runbook](../runbooks/logging.md) describes current operation; this record keeps the decisions and the evidence behind them.

## Conclusion

Adopt the OpenTelemetry log data model as the stored schema and collect with Fluent Bit, replacing vlagent. VictoriaLogs stays the backend: Grafana's Loki-only Logs Drilldown was considered and Loki rejected on efficiency, as in the original [bake-off](../observability-bakeoff.md).

The browsing problems traced back to inconsistent metadata rather than to the UI. A 7-day production inventory (117 sources, 6.39 million rows) found no stored service identity, no stored level on 47% of rows and seven level spellings among the rest, 42% of rows without a message (Traefik access logs), and 948 field names in one flat namespace where application keys such as `namespace` and `name` read like Kubernetes identity. vlagent records Kubernetes metadata and parses JSON and klog, but it cannot transform records, so none of this can be fixed in the current collector.

## Schema decisions

Each decision follows a published convention or an upstream implementation:

1. **Service identity.** `service.name` follows the OpenTelemetry Kubernetes rule ([semantic conventions v1.44.0](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/non-normative/k8s-attributes.md)): the `resource.opentelemetry.io/service.name` pod annotation, then the `app.kubernetes.io/instance` label, then `app.kubernetes.io/name`, then the owning workload, pod and container. The OpenTelemetry Operator and Grafana's Kubernetes chart (`alignServiceNameWithOTelSemConv`) use the same order. Pods whose labels give the wrong answer get the annotation.
2. **Missing levels.** Sources without a level store severity 0 (unspecified). The [data model](https://github.com/open-telemetry/opentelemetry-specification/blob/v1.61.0/specification/logs/data-model.md) lets UIs read that as INFO; storing INFO would invent information. A level the source states but the mapping cannot read is flagged with `pipeline.severity_unmapped`.
3. **Traefik message.** The body is Traefik's own `common` access-log line, which is what Traefik puts in the body when it exports OTLP itself ([v3.7.14 source](https://github.com/traefik/traefik/blob/v3.7.14/pkg/middlewares/accesslog/logger.go)). All fields stay as attributes.
4. **Request outcome.** Severity stays what the source logged. `http.response.status_code` is stored for every request and `error.type` only for 5xx responses, per the HTTP conventions' server rules; 4xx and client cancellations (499) are not errors.
5. **UniFi CEF** (not yet collected by Fluent Bit). Body from the event's `msg`, falling back to its name, as Elastic's CEF decoder does; CEF severity 0–3/4–6/7–8/9–10 to INFO/WARN/ERROR/FATAL, as Bindplane's OpenTelemetry collector does; parser-native `cef.*` names; `UNIFIutcTime` as the timestamp. No published CEF-to-OpenTelemetry mapping exists ([semantic-conventions #1460](https://github.com/open-telemetry/semantic-conventions/issues/1460) is open).
6. **Application fields.** Fields with a semantic-convention name use it unprefixed; all other application fields are prefixed by the application name, never by an existing convention namespace such as `app` ([naming guidance](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/general/naming.md)).

Plain-text levels are read only at a fixed position in each format, never from words elsewhere in the line. That is established parser practice (Fluent Bit and Elastic parsers, the OpenTelemetry Collector's regex parser) and keeps the repository's rule against inferring severity from arbitrary text.

## Collector comparison

Measured locally on 2026-10-18 by replaying a one-hour production export (53,376 rows from 98 container files) at the production rate of about 12 lines/s, then a 2,000 lines/s burst. Memory is cgroup working set, the basis of the production vlagent figure. One collector processed all three nodes' files and no Kubernetes API was available, so per-node CPU in the cluster is lower.

| Collector | Memory steady / burst | CPU at 12 lines/s | Transformations |
| --- | --- | --- | --- |
| vlagent (production, per node) | 33–41 MiB | 3–4 mCPU | none |
| Fluent Bit 5.1.4 | 9 / 21 MiB | ~12 mCPU | Lua |
| OpenTelemetry Collector `otelcol-k8s` 0.163.1 | 66 / 72 MiB | ~85 mCPU | stock processors, OTTL |
| OpenTelemetry Collector contrib 0.163.1 | 92 / 101 MiB | ~85 mCPU | stock processors, OTTL |
| Grafana Alloy 1.20.1 (otelcol components) | 112 / 121 MiB | ~89 mCPU | stock processors, OTTL |

The OpenTelemetry Collector implements more of the mapping with stock components, but costs two to three times vlagent's memory and considerably more CPU, with beta log components and fortnightly releases. Fluent Bit was the lightest measured, ships a Kubernetes metadata filter with owner references, and buffers to disk. The mapping is about 300 lines of Lua and configuration under `kubernetes/apps/observability/fluent-bit/app/config/`.

vlagent's proposed transformation language ([VictoriaLogs #1508](https://github.com/VictoriaMetrics/VictoriaLogs/pull/1508)) expressed the same Kubernetes rules in a local trial with the maintainer's test image. It is unmerged, with changes requested and no release date. Revisit if it ships and keeping one VictoriaMetrics component matters more than Fluent Bit's lower footprint.

## Mapping verification

Run through the production configuration and Lua with a replay of the export: 53,376 of 53,376 records arrived, all with `service.name` and a non-empty body, with no unmapped levels. Severity was unspecified for 6,647 rows: mcphub multi-line JSON fragments (5,789), echo probe access lines (842) and 16 plain-text lines from the functional probe and Whereabouts that state no level. All 14,267 Traefik bodies matched Traefik's compiled formatter, and the 22 responses with 5xx status carried `error.type`. `scripts/logging/test-fluent-bit.py` runs 33 fixtures through the pinned image in CI, covering identity precedence including owner references and annotations, payload collisions, each level format, Traefik formatting, split CRI records, a 200 KB line and the fallback for records the mapping cannot handle.

## Delivery

The September drills ([graceful outage](kubernetes-log-collectors.md); [SIGKILL during an outage](https://github.com/kelchm/home-lab/blob/26cb6da0d60e49733614246990ac1ae3567ca06d/docs/runbooks/logging.md)) were repeated against Fluent Bit 5.1.4 with a numbered fixture, kubelet-style rotation and filesystem buffering:

| Scenario | Fluent Bit | vlagent (September) |
| --- | --- | --- |
| 11-minute backend outage, graceful restart | 13,200/13,200, 0 duplicates | 12,839/12,839, 0 duplicates |
| SIGKILL, backend up | 2,400/2,400, 0 duplicates | not tested |
| SIGKILL during an outage | 33 missing, 0 duplicates; file rotated while stopped | 2,400/2,400, 529 duplicates |
| Buffer exhausted (deliberately tiny limit) | 2,336 missing; drop counter reported 1,072 | not tested |

The missing records in the third case were the unread end of a file that rotated while Fluent Bit was stopped. Including rotated files in the tail path recovers them but replays rotated files after every restart (2,700 duplicates after a graceful restart), and Fluent Bit's documentation says the path must not match rotated files. The configuration follows the documentation and accepts this compound case. Because the drop counter undercounts buffer-exhaustion loss, alerting fires on buffer capacity before exhaustion.

## Rejected

- **Loki with Logs Drilldown:** the most complete Grafana browsing experience, rejected on efficiency. Its keyword severity heuristic also contradicts the no-inference rule.
- **loki-vl-proxy** (Loki API over VictoriaLogs): worked locally with Logs Drilldown 3.0.0 once service and level were stored, but is six months old with one maintainer, and its level filters read only stored fields.
- **Logchef:** no surrounding-context view or export for VictoriaLogs.
- **Unpoller's Loki output for UniFi events:** the event endpoints return 404 on Network 10.x, and v5.6.3 duplicates entries ([collector loop](https://github.com/unpoller/unpoller/blob/v5.6.3/pkg/inputunifi/collectevents.go)).
- **Vector:** its `kubernetes_logs` source is documented as best effort without acknowledgements. A central Vector behind vlagent duplicates batches whenever vlagent's request times out during a backend outage, and bounding Vector's retries turns those failures into batches vlagent drops unless an extra shim translates status codes.
