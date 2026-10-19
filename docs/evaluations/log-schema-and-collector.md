# Log schema and collector evaluation

Evaluated 2026-10-18; UniFi syslog added 2026-10-19. Scope: how Kubernetes container logs and UniFi syslog are shaped and collected for VictoriaLogs, so Grafana can browse them by service, severity and message without per-source query knowledge. The [logging runbook](../runbooks/logging.md) describes current operation; this record keeps the decisions and the evidence behind them.

## Conclusion

Adopt the OpenTelemetry log data model as the stored schema and collect with Fluent Bit, replacing vlagent. VictoriaLogs stays the backend: Grafana's Loki-only Logs Drilldown was considered and Loki rejected on efficiency, as in the original [bake-off](../observability-bakeoff.md).

The browsing problems traced back to inconsistent metadata rather than to the UI. A 7-day production inventory (117 sources, 6.39 million rows) found no stored service identity, no stored level on 47% of rows and seven level spellings among the rest, 42% of rows without a message (Traefik access logs), and 948 field names in one flat namespace where application keys such as `namespace` and `name` read like Kubernetes identity. vlagent records Kubernetes metadata and parses JSON and klog, but it cannot transform records, so none of this can be fixed in the current collector.

## Schema decisions

Each decision follows a published convention or an upstream implementation:

1. **Service identity.** `service.name` follows the OpenTelemetry Kubernetes rule ([semantic conventions v1.44.0](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/non-normative/k8s-attributes.md)): the `resource.opentelemetry.io/service.name` pod annotation, then the `app.kubernetes.io/instance` label, then `app.kubernetes.io/name`, then the owning workload, pod and container. The OpenTelemetry Operator and Grafana's Kubernetes chart (`alignServiceNameWithOTelSemConv`) use the same order. Pods whose labels give the wrong answer get the annotation.
2. **Missing levels.** Sources without a level store severity 0 (unspecified). The [data model](https://github.com/open-telemetry/opentelemetry-specification/blob/v1.61.0/specification/logs/data-model.md) lets UIs read that as INFO; storing INFO would invent information. A level the source states but the mapping cannot read is flagged with `pipeline.severity_unmapped`.
3. **Traefik message.** The body is Traefik's own `common` access-log line, which is what Traefik puts in the body when it exports OTLP itself ([v3.7.14 source](https://github.com/traefik/traefik/blob/v3.7.14/pkg/middlewares/accesslog/logger.go)). All fields stay as attributes.
4. **Request outcome.** Severity stays what the source logged. `http.response.status_code` is stored for every request and `error.type` only for 5xx responses, per the HTTP conventions' server rules; 4xx and client cancellations (499) are not errors.
5. **UniFi CEF.** Body from the event's `msg`, falling back to its name, as Elastic's CEF decoder does; CEF severity 0–3/4–6/7–8/9–10 to INFO/WARN/ERROR/FATAL, as Bindplane's OpenTelemetry collector does; parser-native `cef.*` names; `UNIFIutcTime` as the timestamp. No published CEF-to-OpenTelemetry mapping exists ([semantic-conventions #1460](https://github.com/open-telemetry/semantic-conventions/issues/1460) is open).
6. **Application fields.** Fields with a semantic-convention name use it unprefixed; all other application fields are prefixed by the application name, never by an existing convention namespace such as `app` ([naming guidance](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/general/naming.md)).

7. **Attribute shape.** Field names are schema, never data: arrays stay one value, an object whose keys are data is stored as one JSON string, and a record keyed that way stays in the message, as [VictoriaLogs' FAQ](https://github.com/VictoriaMetrics/VictoriaLogs/blob/v1.53.0/docs/victorialogs/FAQ.md#how-many-fields-a-single-log-entry-may-contain) requires for dynamically keyed objects. A field moves to a semantic-convention name only when its source's documented format establishes the meaning, never by key name alone; source locations are mapped only as `<file>.<source extension>:<line>`, and Traefik's `ClientAddr` is the connection peer because Traefik replaces `ClientHost` with the whole `X-Forwarded-For` value. Record time is the CRI timestamp: payload timestamps measured from whole seconds (Traefik) to nanoseconds, and promoting them would reorder lines. Where a source can stop emitting data as names, the source is changed instead (Flux's `GroupChangeLog`). Deferred until a concrete need: promoting payload timestamps, HTTP fields of other applications, `exception.*` for `err`/`error`, logger names as instrumentation scope (this pipeline cannot set scope per record), and a shared convention for objects a controller reconciles. These rules were checked by a standards review, an edge-case review against a day of production fields and a necessity review.

Plain-text levels are read only at a fixed position in each format, never from words elsewhere in the line. That is established parser practice (Fluent Bit and Elastic parsers, the OpenTelemetry Collector's regex parser) and keeps the repository's rule against inferring severity from arbitrary text.

## UniFi syslog

Added October 19, 2026, when UniFi syslog moved from VictoriaLogs' built-in listener to Fluent Bit. A 10-minute packet capture of the export that day held 3,832 datagrams. The gateway's CEF lines carried no syslog priority. Every other line did: the gateway's daemon log at `daemon.info` with some `user.warning` and `user.err`, switches mostly at `daemon.debug` (DHCP snooping counters), and access points across `kern`, `user`, `daemon` and `authpriv` from `debug` to `crit`, including routine Wi-Fi driver messages at `kern.err`. Fourteen datagrams, all from one switch, carried a driver message and its continuation on two lines; VictoriaLogs had stored each continuation as a separate record without a header, about 190 a day.

The mapping follows from that capture:

- **Severity.** CEF events use decision 5. Other lines use the syslog severity as the [OpenTelemetry Collector's syslog parser](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.163.0/pkg/stanza/operator/parser/syslog/parser.go) maps it (`emerg` 21, `alert` 19, `crit` 18, `err` 17, `warning` 13, `notice` 10, `info` 9, `debug` 5) with the keyword as `severity_text`. A line with neither stays unspecified.
- **Identity.** `service.name` is the device name from the syslog header, because each device is what an operator browses; `service.namespace` is `unifi`. Host attributes follow the OpenTelemetry [host conventions](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/resource/host.md), which name switches as hosts: `host.name`, and for adopted devices `host.id` (MAC), `host.type` (model) and `os.version` (firmware) from the tag they send. Fluent Bit's OTLP output takes a chunk's resource from its first record ([v5.1.4 source](https://github.com/fluent/fluent-bit/blob/v5.1.4/plugins/out_opentelemetry/opentelemetry_logs.c)), so a stock `rewrite_tag` filter gives each device its own tag, by hostname or else sender address; without it, a local run labelled every device's records with the first sender's identity.
- **Message and time.** One datagram is one record, so the switch's two-line messages stay whole. CEF events take `UNIFIutcTime`; other lines keep their arrival time because RFC 3164 headers carry no timezone.

**Placement.** The existing DaemonSet receives syslog rather than a separate Deployment. With `externalTrafficPolicy: Local`, Cilium advertises the LoadBalancer address only from nodes with a Ready collector, so the sender's address survives for the network policy, each datagram reaches exactly one collector and a collector restart moves senders to the other nodes. Each collector buffers to its node's disk under the existing delivery guarantees. A single-replica Deployment would need its own buffer volume and would drop all UniFi syslog whenever it restarted; the withdrawn relay ([#867](https://github.com/kelchm/home-lab/pull/867)) showed the cost of a separate workload with its own queue and alerts.

**Cutover.** Each datagram goes to one Service backend, so the old and new paths never store the same datagram. The cutover takes two changes to avoid a gap. Flux applies `victoria-logs-single` before `fluent-bit`, so one change that deleted the old Service and created a new one would leave the address without a listener until the first Fluent Bit pod with the syslog port was Ready. Instead, Fluent Bit starts listening first, and the existing `victoria-logs-syslog` Service then changes its selector in place, so its backends switch from VictoriaLogs to Ready Fluent Bit pods in one update. The Service keeps its name and its place in the VictoriaLogs app, because renaming or moving it would recreate it.

**Verification.** `scripts/logging/test-fluent-bit.py` sends 14 UniFi datagrams from two sender addresses through the pinned image: CEF severities at each band boundary in use, header and extension escapes, the name fallback, an unmapped CEF severity, gateway, switch and access-point lines with their priorities and device tags, the two-line switch message, datagrams without a header from two senders and an out-of-range priority. Sent into VictoriaLogs v1.53.0, every UniFi record had a readable `_msg`, and the unchanged `UniFiWANFlapping`, `UniFiIntrusionDetectedOutbound` and `UniFiHoneypotTriggered` expressions matched their events and ignored internal and multicast detections.

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

Run through the production configuration and Lua with a replay of the export: 53,376 of 53,376 records arrived, all with `service.name` and a non-empty body, with no unmapped levels. Severity was unspecified for 6,647 rows: mcphub multi-line JSON fragments (5,789), echo probe access lines (842) and 16 plain-text lines from the functional probe and Whereabouts that state no level. All 14,267 Traefik bodies matched Traefik's compiled formatter, and the 22 responses with 5xx status carried `error.type`. `scripts/logging/test-fluent-bit.py` runs 53 container-log fixtures through the pinned image in CI, covering identity precedence including owner references and annotations, payload collisions, each level format, Traefik formatting and peer addresses, split CRI records, a 200 KB line, arrays, data-keyed objects, source locations and the fallback for records the mapping cannot handle.

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
