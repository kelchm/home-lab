# Kubernetes log collector evaluation

Evaluated 2026-09-22–23 for [#581](https://github.com/kelchm/home-lab/issues/581). Scope: Kubernetes container logs sent to VictoriaLogs. The [logging runbook](runbooks/logging.md) describes the deployed Alloy pipeline.

## Conclusion

Proceed with implementing vlagent as Alloy's successor. It recovered every numbered record in the tested backend-outage/graceful-restart case and exposes counters for persistent-buffer overflow. Built-in Kubernetes collection and direct VictoriaLogs delivery make it a useful fit for this single-backend pipeline. The remaining work is to implement its schema compatibility and deployable configuration.

Keep Alloy operating until the replacement configuration is reviewed and rollout is approved. Vector remains an alternative if preserving the current schema at ingestion is the overriding requirement: its transforms can reproduce that schema, replacing custom logic already maintained in Alloy. The tests do not establish that Vector is unsuitable.

[The migration work item](https://github.com/kelchm/home-lab/issues/583) owns the image pin, GitOps configuration, query compatibility, buffer and alert settings, rollout/rollback, and scratch cleanup. It uses this evaluation as its basis. Validation should exercise the final implementation and address the stated limits, including untested abrupt termination, without reopening the collector comparison.

## Delivery evidence

The comparison used Alloy v1.19.2, Alloy v1.19.2 with its experimental writer WAL, Vector v0.58.0, and vlagent v1.52.0, each writing to a separate VictoriaLogs v1.52.0 backend in `log-drill`. Fault injection targeted the scratch components. Production Alloy also collected the synthetic workload, providing an unaffected delivery reference. Verification on September 23 queried retained data and metrics read-only; it did not repeat fault injection.

### Backend outage with collector restart

The backend outage lasted approximately eleven minutes; collectors were deleted about five minutes into it. The deletion used the normal thirty-second termination grace period. Within the half-open UTC window `2026-09-22T21:57:06.386306586Z` to `2026-09-22T22:08:08.285010824Z`, production retained all 12,839 expected numbered records from `drill-load`.

| Collector | Distinct numbered records | Missing against reference | Duplicates |
| --- | ---: | ---: | ---: |
| Production reference | 12,839 | 0 | 0 |
| Alloy | 6,589 | 6,250 | 0 |
| Alloy with WAL | 6,590 | 6,249 | 0 |
| Vector | 12,839 | 0 | 0 |
| vlagent | 12,839 | 0 | 0 |

The expected sequence is 7777 through 20877 inclusive, excluding values whose remainder modulo 100 is 3 or 7; those fixture classes were non-JSON output. The retained sequence sets were checked against that pattern, including unexpected values. These results apply to that numbered subset and the tested graceful restart. They do not establish recovery after SIGKILL, OOM, node power loss, or disk loss.

vlagent has [in-memory batches](https://github.com/VictoriaMetrics/VictoriaLogs/blob/v1.52.0/app/vlagent/remotewrite/pendinglogrows.go) and [in-flight writes](https://github.com/VictoriaMetrics/VictoriaLogs/blob/v1.52.0/app/vlagent/remotewrite/client.go), as well as its persistent queue. Graceful flushing does not prove atomic persistence of every read record under abrupt termination.

The Alloy WAL result establishes a failure of the tested configuration to recover the missing records. [Marker-based replay exists in the implementation](https://github.com/grafana/alloy/blob/v1.19.2/internal/component/common/loki/wal/watcher.go); the result does not establish that replay is universally broken. The tested `max_segment_age` was one hour, and [age-based segment removal](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.write/#wal) is another limit that would need to be accounted for before adopting it.

### Buffer overflow and detection

vlagent's persistent queue evicts old blocks at its configured limit. The surviving node-1 process exposed 3,063 dropped blocks and 1,554,700,751 dropped compressed bytes when inspected on September 23. The [pinned queue implementation](https://github.com/VictoriaMetrics/VictoriaLogs/blob/v1.52.0/vendor/github.com/VictoriaMetrics/VictoriaMetrics/lib/persistentqueue/persistentqueue.go) increments `vm_persistentqueue_blocks_dropped_total` and `vm_persistentqueue_bytes_dropped_total` on eviction. These are useful loss signals, but neither is a lost-row count.

Use a Prometheus/OpenMetrics parser for exporter samples. A retained scrape at `2026-09-22T23:06:19Z` contained:

```text
vlagent_remotewrite_pending_data_bytes{path="<queue>", url="1:secret-url"} 318843496
```

The whitespace-separated second token is a label fragment, so `awk '{s+=$2}'` reports zero for this valid nonzero sample. Labels above are sanitized. `vlagent_remotewrite_queue_blocked = 0` is expected with persistent buffering enabled: [the fast queue](https://github.com/VictoriaMetrics/VictoriaLogs/blob/v1.52.0/vendor/github.com/VictoriaMetrics/VictoriaMetrics/lib/persistentqueue/fastqueue.go) keeps accepting writes while the disk queue evicts older blocks.

The Vector saturation configuration used a 512 MiB disk buffer with `when_full: drop_newest`; dropping at capacity is [documented behavior](https://vector.dev/docs/reference/configuration/sinks/http/#buffer.when_full). Missing loss accounting in that drill remains unresolved. Configuration changes, restarts, and buffer resets during the experiment prevent a clean conclusion about its overflow observability.

Outage capacity must be sized per node from representative compressed input and incident bursts. The short rate samples do not establish a days-long outage guarantee. Queue payload bytes and allocated directory size are different quantities.

Production throughput and alert history do not provide a reference sequence against which to count missing records. The [current alerts](../kubernetes/apps/observability/victoria-metrics-k8s-stack/app/platform-alerts.yaml) can detect several failures, but partial loss may occur while records continue arriving. The investigation therefore does not establish a historical production loss count.

## Application compatibility

### stdout/stderr and severity

vlagent v1.52.0 does not retain a stdout/stderr field. [Upstream change #1791](https://github.com/VictoriaMetrics/VictoriaLogs/pull/1791) adds `output_stream` and was merged September 17. As of this evaluation, [v1.52.0](https://github.com/VictoriaMetrics/VictoriaLogs/releases/tag/v1.52.0), published July 16, was still the latest published release. Use a pinned build containing the field, or explicitly accept its omission; renaming a field cannot recover values absent from stored rows.

Production was queried over the half-open window `2026-09-16T20:11:00Z` to `2026-09-23T20:11:00Z`, excluding `log-drill`. The initial grouped result contained 3,751,265 rows: 2,841,805 stdout and 909,460 stderr. Of 123 namespace/service/container groups, 22 used both streams. These are row counts, not incident counts; small late-arrival differences occurred between queries.

| Source | stdout rows | stderr rows | Verified meaning and extraction |
| --- | ---: | ---: | --- |
| Broadsheet / app | 102,360 | 23 | All 23 stderr rows have an explicit `warning:` prefix from rendering diagnostics. |
| Bambuddy / app | 40,576 | 3,304 | Every stderr row has an explicit logger level: 3,289 INFO and 15 WARNING. stdout includes 40,314 health requests. |
| MCPHub / app | 91,908 | 2,878 | 2,846 stderr rows have explicit headers: 2,840 WARN and 6 ERROR. The other 32 are object/JSON continuation rows following the six ERROR headers. |
| Grafana MCP functional probe / probe | 1,917 | 876 | stdout reports success. stderr includes 199 traceback headers and 199 terminal exception summaries, plus stack/context lines without their own level token. |
| iperf3 / app | 725,574 | 241,858 | In the 24-hour subset, all 34,560 stderr rows contain the same unable-to-receive-cookie error; stdout is listener output. |
| Kanidm / kanidm | About 1.26 million | 5,833 | stderr contains 5,818 INFO-formatted rows and 15 initialization lines. stdout includes 3,552 ERROR and 2,935 WARN rows. |

Broadsheet, Bambuddy, MCPHub, and the probe lack a stored top-level `level` on those stderr rows in the deployed pipeline. Source-specific parsing nevertheless extracts the explicit levels shown above. The patterns were executed using VictoriaLogs `extract_regexp`, scoped to each application and container; stream was used only to report coverage. Bambuddy's [formatter includes the logger level](https://github.com/maziggy/bambuddy/blob/v1.2.5.5/backend/app/main.py). The [owned probe](../kubernetes/apps/ai/grafana-mcp/app/probe.py) can emit one structured failure event with the traceback as a string, avoiding per-line inference.

MCPHub's continuation grouping was checked within each pod's stderr output. That does not establish safe reconstruction after stream metadata is removed or across interleaved events. Preserve unknown levels for unfamiliar formats. Severity also cannot replace every use of stream: Bambuddy's access logs and application diagnostics both include INFO, so separating them needs a category or format selector.

The field omission itself does not discard log messages. A direct production/vlagent comparison for September 23, `01:30–20:11 UTC`, retained 11,385 Broadsheet rows in each backend, including three renderer warnings, and 4,783 Bambuddy rows in each, including 4,484 health requests and 299 Python-formatted application messages. This verifies those counts and categories in that overlap window, not row identity across the entire week. Preserving `output_stream` avoids replacing useful, format-independent selectors with application-specific filters.

### Schema and query behavior

| Concern | Supported finding | Migration implication |
| --- | --- | --- |
| Service grouping | LogsQL `coalesce` can apply the current fallback: recommended app label, legacy app label, container name. | Verify actual operator queries and saved Grafana queries; repository text matches are not a count of deployed queries. |
| Structured severity | In `2026-09-23T18:30–19:30Z`, both backends contained 20,868 non-drill rows. vlagent lacked a stored level on 1,674 ordinary logfmt rows, including 36 warnings. `unpack_logfmt` recovered their raw levels and matching per-container counts. | Handle guarded logfmt parsing, JSON severity fields, and alias normalization; a regex on an absent `level` is insufficient. Verify Grafana coloring and filtering. |
| Metadata and payload | vlagent exposes Kubernetes metadata under `kubernetes.*`, while parsed application fields occupy the top level. | Use the authoritative metadata names and test collisions with application payload keys. |
| Timestamps | In v1.52.0, `kubernetesCollector.timeField` affects JSON extraction; klog parsing independently returns its embedded timestamp. | Verify the chosen timestamp policy with skewed JSON and klog fixtures. |
| Stream identity | Query-time aliases do not change ingestion-time stream identity. | Choose stream fields deliberately and verify the cardinality alert against the resulting schema. |
| Labels and filename | In-place label changes are cached; the tested change was absent after 100 seconds. No operational filename query dependency was found in the repository. | Accept or address label staleness and check operator needs outside Git. |

The parsing distinctions are visible in the [v1.52.0 collector](https://github.com/VictoriaMetrics/VictoriaLogs/blob/v1.52.0/app/vlagent/kubernetescollector/processor.go); see also [metadata configuration](https://docs.victoriametrics.com/victorialogs/vlagent/#kubernetes-metadata-configuration) and [`unpack_logfmt`](https://docs.victoriametrics.com/victorialogs/logsql/#unpack_logfmt-pipe). Equal aggregate totals alone do not prove record identity or full schema parity. Carry the existing static-pod, CRI partial-record, metadata-isolation, and hardening requirements into the deployed configuration and its validation. Source-format work is coordinated with [#485](https://github.com/kelchm/home-lab/issues/485) and [#556](https://github.com/kelchm/home-lab/pull/556).

## Reproducing the measurements

These read-only queries use retained September data. Counts are only reproducible while the relevant data and scratch backends exist; retention or teardown will remove that ability. The tables above preserve the measured aggregates. Raw application log exports are excluded because they may contain credentials or private data.

For the restart table, run this against production and the `vl-alloy`, `vl-alloy-wal`, and `vl-vector` scratch backends:

```logsql
_time:[2026-09-22T21:57:06.386306586Z,2026-09-22T22:08:08.285010824Z) pod:~"^drill-load-" msg.n:*
| stats count() as rows, count_uniq(msg.n) as unique
```

For the `vl-vlagent` scratch backend:

```logsql
_time:[2026-09-22T21:57:06.386306586Z,2026-09-22T22:08:08.285010824Z) kubernetes.pod_name:~"^drill-load-" n:*
| stats count() as rows, count_uniq(n) as unique
```

The group inventory uses production:

```logsql
_time:[2026-09-16T20:11:00Z,2026-09-23T20:11:00Z) NOT namespace:log-drill
| stats by (namespace, service_name, container, stream) count() as rows
```

Source-specific level coverage uses production. Extraction is confined to explicit logger prefixes; it does not infer severity from arbitrary message words:

```logsql
_time:[2026-09-16T20:11:00Z,2026-09-23T20:11:00Z) namespace:iot service_name:broadsheet container:app
| extract_regexp "^(?P<extracted_level>warning|error): "
| stats by (stream, extracted_level) count() as rows
```

```logsql
_time:[2026-09-16T20:11:00Z,2026-09-23T20:11:00Z) namespace:printing service_name:bambuddy container:app
| extract_regexp "^(?:[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3} )?(?P<extracted_level>DEBUG|INFO|WARNING|ERROR|CRITICAL)(?: \\[|:\\s+)"
| stats by (stream, extracted_level) count() as rows
```

```logsql
_time:[2026-09-16T20:11:00Z,2026-09-23T20:11:00Z) namespace:ai service_name:mcphub container:app
| extract_regexp "^\\[[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{3}(?:Z|[+-][0-9]{2}:[0-9]{2})\\] \\[(?P<extracted_level>TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\\] \\[[0-9]+\\] \\[[^\\]]+\\] "
| stats by (stream, extracted_level) count() as rows
```
