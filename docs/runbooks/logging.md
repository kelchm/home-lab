# Logging

Kubernetes application and control-plane container stdout/stderr is collected into VictoriaLogs, which is the only log backend. Fluent Bit runs on every node, enriches each record from the Kubernetes API, maps it onto the OpenTelemetry log data model and sends it to VictoriaLogs over OTLP; see [Log schema](#log-schema). The UniFi console and its devices send syslog straight to VictoriaLogs; see [UniFi console logs](#unifi-console-logs). The [schema and collector evaluation](../evaluations/log-schema-and-collector.md) records why the schema and collector look the way they do.

Collection covers kubelet-managed container logs, including init containers, native sidecars and static control-plane pods. It does not cover Talos service/kernel journals or application files outside stdout/stderr. Quiet or empty containers do not establish a collection failure: compare Kubernetes inventory, node log files and stored records before declaring a source missing.

## Query logs

- **Kubernetes logs dashboard:** [Kubernetes / Logs](https://grafana.home.kelch.io/d/kubernetes-logs) — the production starting point for application logs. Its namespace/service selectors and stream/severity filters cover current records and retained history in older schemas.
- **Grafana Explore:** `https://grafana.home.kelch.io/explore` — select the `VictoriaLogs` datasource for the query editor, visual builder and live logs. The query editor's `Run in VMUI` action opens the query in the external VMUI. Open a row's log menu and select `Show log details` to inspect its structured fields alongside `_msg`.
- **VictoriaLogs VMUI:** `https://vlogs.home.kelch.io/select/vmui/` — use the native LogsQL explorer and live-stream view. The bare hostname redirects here.

For routine application browsing:

1. Open **Kubernetes / Logs** and set the time picker around the event. The initial view is the last 15 minutes in `observability`.
2. Choose **Namespace**, then type the application name in **Service**. Suggestions come from logs in that time window; service choices follow the namespace selection. Multiple selections and **All** are supported. If an older application is absent, move the time window to when it was running.
3. Start with **Stream** and **Severity** at **All**, then narrow them as needed. `stderr` is not synonymous with errors. **unknown** includes messages without a recognized severity. The panel shows up to 1,000 matching lines; narrow the application/time selection when that limit is reached.
4. Open a row's log menu and choose **Show log details** to inspect its pod, container and application fields. Use **Show context** there for surrounding records from that pod/container; these can include both stdout/stderr, other severities and times outside the dashboard window. Use Explore for additional LogsQL filtering, following [Retained history](#retained-history) when the window reaches older schemas.

The VictoriaLogs datasource v0.33.1, deployed October 18, 2026, adds **Drilldown** beside **Syntax** in Explore's **Code** editor. It provides field/stream breakdowns, message patterns, filter chips and log samples. Treat it as broad discovery: it starts from `*`, discarding the editor expression and stream filters, over the editor's time range. **Go to editor** replaces the original expression and filters; drawer time changes do not transfer. Start from **Raw Logs**, explicitly select the source/namespace again, and verify both the resulting scope and Explore time before continuing. Its first breakdown is `service.name`. The raw Fields list still includes infrastructure labels and application fields; application fields are grouped by their application prefix. Keep **Kubernetes / Logs** as the routine application starting point. See the [qualification record](../evaluations/grafana-victorialogs-drilldown.md) for evidence, acceptance and production verification; [#871](https://github.com/kelchm/home-lab/issues/871) tracks the field-discovery and scope-handoff investigation.

Pattern counts marked `~` are estimates from a 1-in-10 sample and the list is capped at 100 patterns. The details Logs tab shows at most 100 rows, and expanded breakdown rows at most 50; these samples are separate from volume counts. Explore retains the configured 1,000-line limit. Histogram bucket alignment and visible edge buckets can differ from an exact-window count; use `| stats count()` with explicit time bounds for incident totals. Severity buttons and field filters operate on stored fields and do not provide the dashboard's compatibility normalization.

The VMUI handoff still serializes the absolute end time as UTC `YYYY-MM-DD HH:mm` without a timezone suffix, including in v0.33.1. In an America/New_York browser, the September 26 check shifted a historical window by four hours; seconds are also omitted. The v0.33.1 generated link additionally omitted query-level filter chips in the scratch trial, retaining `query=*` after a namespace-chip selection. For exact or scoped investigations, copy an explicit LogsQL expression and set and verify the time range in VMUI. The timezone limitation remains accepted ([#485](https://github.com/kelchm/home-lab/issues/485)); revisit when a release preserves timezone, precision and all filters.

Grafana authenticates through its native `auth.generic_oauth` integration and dedicated Kanidm client; VictoriaLogs VMUI uses the OIDC middleware on its HTTPRoute. Useful LogsQL starting points are `k8s.namespace.name:=observability`, `service.name:=grafana`, `severity_number:>=17` (errors and worse), `log.iostream:=stderr`, `error.type:*` (HTTP 5xx responses) and `k8s.namespace.name:=observability | stats by (service.name) count()`. Bare words search the message only; use `*:word` to search every field. The time picker supplies the query window. These filters select current records only; see [Retained history](#retained-history) for older rows.

## Log schema

Records follow the [OpenTelemetry log data model](https://opentelemetry.io/docs/specs/otel/logs/data-model/) and [semantic conventions](https://opentelemetry.io/docs/specs/semconv/). VictoriaLogs flattens resource and log attributes into fields named as below.

| Field | Content |
| --- | --- |
| `service.name` | The `resource.opentelemetry.io/service.name` pod annotation, else the `app.kubernetes.io/instance` label, else `app.kubernetes.io/name`, else the owning workload (Deployment, StatefulSet, DaemonSet, Job, …), pod, then container. Stream field. |
| `service.namespace`, `service.instance.id` | The `resource.opentelemetry.io/service.namespace` annotation, else the Kubernetes namespace; `<namespace>.<pod>.<container>`. |
| `k8s.namespace.name`, `k8s.pod.name`, `k8s.container.name`, `k8s.node.name` | Kubernetes identity; all four are stream fields. Also `k8s.pod.uid`, `k8s.cluster.name` (`k8s-prod`) and `k8s.<kind>.name` for the owning workload. |
| `severity_number`, `severity_text` | OpenTelemetry severity (1–4 trace, 5–8 debug, 9–12 info, 13–16 warn, 17–20 error, 21–24 fatal) and the source's level text unchanged. `0` means the source gave no level. |
| `_msg` | The message: the application's `msg`/`message`/`record.message`, the klog message, Traefik's `common` access-log line, or the whole line for plain text. |
| `log.iostream` | `stdout` or `stderr`. Never implies severity. |
| `http.*`, `url.*`, `client.*`, `server.*`, `network.protocol.*`, `tls.*`, `user_agent.original` | HTTP semantic-convention names for Traefik access logs. `error.type` holds the status code for 5xx responses only. |
| `code.file.path`, `code.line.number`, `thread.id` | Source location from klog and Go slog. |
| `<application>.<field>` | Every other application field, prefixed by the `app.kubernetes.io/name` label (else the legacy `app` label, owning workload or container), e.g. `traefik.RouterName`, `grafana.logger`, `kustomize-controller.name`. |
| `pipeline.severity_unmapped` | The source stated a level the mapping does not recognize; extend `normalize.lua`. |
| `pipeline.normalize_error` | The mapping raised an error on this record, which keeps its identity and raw line as the message; fix `normalize.lua`. |

To give a workload a better service name than its labels produce, set `resource.opentelemetry.io/service.name` on its pod template.

Levels come from structured fields (JSON, logfmt, PostgreSQL's `record.error_severity`), klog's leading letter, or a level token at a fixed position in known plain-text formats. Words elsewhere in a line never set severity; an unknown level is stored as unspecified rather than guessed. mcphub's multi-line JSON fragments and echo's probe access lines are the main unspecified sources.

### Retained history

Records written before the Fluent Bit cutover keep vlagent's schema (`kubernetes.pod_namespace`, `kubernetes.pod_name`, `kubernetes.container_name`, `kubernetes.pod_node_name`, `kubernetes.pod_labels.*`, `output_stream`, application fields at top level, `level` only when the application emitted one) until retention removes them, roughly 30 days later. A few Alloy-era rows (`namespace`, `service_name`, `pod`, `container`, `node`, `stream`, `msg.*`) remain until about October 27, 2026.

The **Kubernetes / Logs** dashboard reads all three schemas through the [shared compatibility pipe](../../scripts/logging/compat.logsql): it aliases each schema's identity to `namespace`, `pod`, `container`, `node`, `service_name` and `stream`, and normalizes severity to `trace`, `debug`, `info`, `warning`, `error`, `critical` or `unknown`, taking OpenTelemetry `severity_number` first. In Explore or VMUI, select all schemas with `(k8s.namespace.name:=printing OR kubernetes.pod_namespace:=printing OR namespace:=printing)`, append the pipe, then filter its aliases, e.g. `| filter service_name:=bambuddy level:in(error,critical)`. Explore's quick severity buttons filter stored fields only and can omit older rows; clear them when using the pipe.

Retention is nominally 30 days, but VictoriaLogs begins deleting the oldest partitions at 80% disk use. The effective retention is whichever limit is reached first. Replayed records are stored as additional rows until retention removes them; queries and counts include them.

## UniFi console logs

The UniFi console (`Gateway DMP`, Network 10.6.106) exports its system log as CEF over syslog to the `victoria-logs-syslog` LoadBalancer at `10.32.130.3` UDP/514, which forwards to VictoriaLogs' UDP listener on container port 5514. The same setting makes the gateway send its own daemon syslog and makes adopted switches and access points send plain syslog from their management addresses on the Default network (`10.32.1.0/24`). The Service uses `externalTrafficPolicy: Local`, so the CiliumNetworkPolicy sees the sender's address; its syslog rule admits external traffic only from the gateway's VLAN 30 interface, `10.32.30.1`, and the Default network, alongside the namespace baseline's in-cluster allowances. The HTTP port 9428 stays cluster-internal. The export is UDP with no retry or buffer: records sent while VictoriaLogs restarts, or dropped on the path, are lost.

Every record lands in the default tenant beside the Kubernetes logs. `_time` is the arrival time, because RFC3164 headers carry no timezone; the console's own header time, read as America/New_York, is kept in `timestamp`, and `remote_ip` records the sending address. Until the console export is enabled and the first records are checked, this section describes intended behaviour; [#856](https://github.com/kelchm/home-lab/issues/856) tracks that verification.

### Console setup

The console account the agents use is View Only, so the operator makes this change. The console currently keeps its activity log locally (`rsyslogd` site setting: this controller only, no remote target). The page names below come from third-party guides for Network 9.x and 10.x; correct them here if the console differs.

1. Open **Settings → CyberSecure → Traffic Logging** and find **Activity Logging (Syslog)**.
2. Select **SIEM Server**. Set **Server Address** to `10.32.130.3` and **Port** to `514`.
3. Enable every content category except **Firewall Default Policy**, which users report multiplies volume about 200 times. Leave **Debug Logs** and **Netconsole** off; they are gateway diagnostics, not events.
4. Apply, then leave the separate **Control Plane → Integrations** syslog export off. Exporting the same categories through both pages duplicates every event.

Within a minute, `_time:5m remote_ip:=10.32.30.1 | stats count()` should be nonzero. If it stays at zero, check `hubble_drop_total{destination_namespace="observability", reason="POLICY_DENIED", protocol="UDP"}` for a rise; it also counts unrelated traffic, so confirm with `hubble observe --verdict DROPPED --since 10m` in the `cilium-agent` container on the node running `victoria-logs-single-server-0`. A dropped UDP packet to that pod's port 5514 shows the sender's actual source address, which then belongs in the CIDRs in [networkpolicy.yaml](../../kubernetes/apps/observability/victoria-logs-single/app/networkpolicy.yaml).

### Fields

VictoriaLogs parses the syslog header and the CEF payload itself; no processor runs in between.

| Field | Content |
| --- | --- |
| `hostname`, `app_name`, `proc_id` | Syslog header; stream fields. CEF lines have `app_name=CEF`; firewall rule logs have `app_name=kernel`. |
| `cef.device_vendor`, `cef.device_product`, `cef.device_event_class_id` | CEF header; also stream fields. Product is `UniFi Network` for network events and `UniFi OS` for console events. |
| `cef.name`, `cef.severity`, `cef.device_version` | Event name, 0–10 severity, and the application version. |
| `cef.extension.<key>` | Every CEF extension, such as `UNIFIcategory`, `msg`, `src`, `dst`, `UNIFIwanName`, `UNIFIipsSignature`, `UNIFIclientAlias` and `UNIFIdeviceName`. Ubiquiti lists the keys in [UniFi System Logs & SIEM Integration](https://help.ui.com/hc/en-us/articles/33349041044119). |
| `timestamp`, `remote_ip`, `format`, `facility_keyword`, `level` | Header time, sender address, `rfc3164` or `rfc5424`, and the syslog facility and severity. |

CEF records have no `_msg`, so Grafana and VMUI show VictoriaLogs' missing-message placeholder; the readable text is in `cef.extension.msg`. Append `| copy cef.extension.msg as _msg` to show it in the message column. Firewall rule logs keep the kernel line, including `DESCR="<policy name>"`, in `_msg`.

Plain syslog from the gateway and adopted devices carries no `cef.*` fields or `proc_id`, so the CEF stream filters and alerts never match it. The gateway repeats its hostname in the header, so its daemon lines have `app_name=Gateway-DMP` and the process name opens `_msg`. Devices tag each line with their MAC address, model and firmware: `hostname` is the device name in the console, such as `CoreSwitch`, `app_name` is a value such as `28704e64bf9d,USW-Enterprise-24-PoE-7.5.15+17146`, and `_msg` opens with the process, such as `switch: DHCP_SNP: …` or `hostapd[5197]: …`.

Expected event classes, from Ubiquiti's examples and published captures of Network 9.3–10.6. Confirm the WAN and honeypot entries against the first live records:

| Class | Name | Notes |
| --- | --- | --- |
| 100 | Internet Down | `UNIFIwanName`, `UNIFIwanId`, `UNIFIwanIsp` |
| 107 | Internet Restored | A short drop is reported as restored "after a temporary disconnection" in `msg`. |
| 112, 113 | High Latency Detected, Packet Loss Detected | `UNIFIwanLatency` on 112 |
| 200, 201 | Threat Detected, Threat Detected and Blocked | `src`, `dst`, `UNIFIipsSignature`, `UNIFIipsSignatureId`, `UNIFIrisk` |
| 400–404 | Wi-Fi and wired client connected, disconnected and roamed | `UNIFIclientAlias`, `UNIFIclientIp`, `UNIFIclientMac` |
| 512, 513 | Device Offline, Device Reconnected | `UNIFIdeviceName` |
| 544–549 | Admin access and configuration changes | `UNIFIadmin`, `UNIFIaccessMethod` |

### Example queries

- Everything the console sent: `remote_ip:=10.32.30.1`
- Adopted devices by volume: `remote_ip:ipv4_range(10.32.1.0/24) | stats by (hostname, level) count()`
- One access point's client events: `hostname:=BasementAP _msg:~"stahtd|hostapd"`
- Events by class: `{cef.device_product="UniFi Network"} | stats by (cef.device_event_class_id, cef.name) count()`
- WAN events: `{cef.device_product="UniFi Network"} cef.extension.UNIFIcategory:="Internet and WAN" | fields _time, cef.name, cef.extension.UNIFIwanName, cef.extension.msg`
- IDS detections by pair: `{cef.device_product="UniFi Network", cef.device_event_class_id=~"200|201"} | stats by (cef.extension.src, cef.extension.dst, cef.extension.UNIFIipsSignature) count()`
- One client's connections: `{cef.device_product="UniFi Network"} cef.extension.UNIFIclientAlias:="Hyperion" | fields _time, cef.name, cef.extension.msg`
- Devices going offline: `{cef.device_product="UniFi Network", cef.device_event_class_id="512"}`
- Admin activity: `cef.extension.UNIFIadmin:* OR cef.extension.UNIFIcategory:=Audit OR {cef.device_product="UniFi OS"}` (Network 10.x files admin events under `Audit`; 9.3 used `System` with `UNIFIsubCategory=Admin`)
- Firewall rule hits: `{app_name="kernel"} DESCR`

### Baseline

The console's own system log for the 30 days to October 18, 2026 held 7,439 events, about 250 a day. Client connect, disconnect and roam events made up 89%. The longest gap between two events was 29 hours.

- **IDS:** IPS runs in detect-only (`ids`) mode. All 525 detections had a LAN source, and 523 came from one operator Mac (`Hyperion`, `10.32.10.244`), mostly to the Sparks. Eleven had an external destination, outside multicast: ten from the Mac (nine to CloudFront addresses, one to `192.0.2.10`) and one from `k8s-prod-2`. `UniFiIntrusionDetectedOutbound` would have fired for each of those eleven.
- **WAN:** one T-Mobile outage lasting 3 days 16 hours, three drops of 24 to 90 seconds (two T-Mobile, one Starlink), 26 high-latency and 11 packet-loss events. `UniFiWANFlapping` would not have fired.
- **Devices:** a 10-minute capture on October 18 held 252 lines from six adopted devices, about 1,500 an hour. Switches sent mostly debug-level DHCP snooping counters; access points sent client association, `hostapd` and fan-temperature lines. The gateway's own daemon syslog ran at about 850 rows an hour.
- **Other:** 177 admin logins, 16 configuration changes, 27 SFP receive faults, and 5 firewall-policy blocks: `spark-240a`'s storage address to `admin-prod` Traefik, and `k8s-prod-1` to `10.32.21.100`.

### Alerts

`vmalert-victoria-logs` evaluates the LogsQL rules in [unifi-alerts.yaml](../../kubernetes/apps/observability/victoria-logs-single/app/unifi-alerts.yaml) every minute and sends them through VMAlertmanager like any other `evaluator=vmalert` alert ([alerting runbook](alerting.md)). Each expression sets its own `_time` window, so one event keeps its alert firing for that long and then it resolves. The metrics vmalert never loads these rules, and a broken expression raises `AlertingRulesError` from the chart's vmalert rules.

| Alert | Fires when | Severity |
| --- | --- | --- |
| `UniFiWANFlapping` | Three or more Internet Down, temporary-disconnection or failover events for one WAN within an hour. `UniFiWANNotActive` already pages a WAN down for 5 minutes; this catches shorter repeated drops. | warning |
| `UniFiIntrusionDetectedOutbound` | An IDS detection, in the last 15 minutes, whose destination is outside private, CGNAT, link-local and multicast space. Lab-internal detections are queryable but do not page, because the baseline is dominated by operator traffic. | warning |
| `UniFiHoneypotTriggered` | Any honeypot event in the last 15 minutes. | warning |
| `UniFiSyslogSilent` | For an hour, VictoriaLogs' `vl_rows_ingested_total{type="syslog_udp"}` shows no syslog rows in the last two days, including when the listener has never received one. It is a MetricsQL rule on the backend's own counter, sized above the 29-hour baseline gap, so it also fires about an hour after rollout until the console export is enabled. Gateway daemon and device syslog count too, so it catches a dead listener path or a disabled export rather than a quiet CEF stream. | warning |

The rules' field names and class IDs come from Ubiquiti's documentation and published captures, not from this console. A local VictoriaLogs v1.53.0 and vmalert v1.154.0 fired each rule on matching fixtures and ignored internal and multicast detections. Recheck the selectors against the first live records, especially the temporary-disconnection text, the honeypot event name and whether detections carry `dst`. To silence a benign signature, exclude its `cef.extension.UNIFIipsSignatureId` in the rule rather than adding an Alertmanager silence.

## Source inventory

Measured 2026-09-18 over a 24-hour window with `stats by (service_name) count()` and its `NOT level:~".+"` variant. "No recognized severity" means the stored top-level `level` is absent or empty; it does not establish that a row is unstructured. This is a dated inventory of the former Alloy pipeline, not a complete list of sources or a current traffic baseline. The later [collector evaluation](../evaluations/kubernetes-log-collectors.md#stdoutstderr-and-severity) records additional source-level findings, including Broadsheet renderer warnings whose explicit prefix is not normalized by Alloy.

| Source | 24h entries | No recognized severity | Documented structured output | Decision |
| --- | --- | --- | --- | --- |
| kanidm | 163k | all | No JSON/log-event export found in the tested v1.11.2. `log_level` controls verbosity; OTLP exports trace spans. | Keep native output. The sample is dominated by INFO replication heartbeats and requests; any volume reduction must preserve useful operational and audit events. |
| iperf3 | 138k | all | Not evaluated for the idle benchmark server. | Observed bursts match tcpSocket readiness/liveness probes (10s/30s). Resolved in [#226](https://github.com/kelchm/home-lab/issues/226): the always-on server was removed on 2026-09-30, and [`tools/iperf3/iperf3.yaml`](../../tools/iperf3/iperf3.yaml) runs it on demand. |
| echo | 17k | all | Not needed to identify the observed health-check requests. | The sample is dominated by kubelet `/healthz` requests. It does not establish that the canary is unused; the earlier DNS observation is not a current reachability claim. Retention and exposure belong to [#226](https://github.com/kelchm/home-lab/issues/226). |
| kaniop | 12k | all | None documented in the evaluation. | Keep native output. |
| kube-apiserver | 11k | all | Yes: `--logging-format=json`. | Deferred: Talos-managed control-plane arguments require a separate manual rollout. Revisit if native JSON materially improves the accepted query workflow. |
| csi-driver-nfs | 9.1k | all | None established for the deployed klog emitter. | Keep native output. |
| csi-snapshotter | 9.0k | all | None established for the deployed klog emitter. | Keep native output. |
| mcphub | 8.3k | all | None documented in the evaluation. | Keep native output; explicit logger prefixes are covered in the later collector evaluation. |
| multus | 7.4k | all | None established for the deployed klog emitter. | Keep native output. |

Traefik access logs carry their request fields under HTTP semantic-convention names and use Traefik's own `common` line as the message. Do not infer severity from access-log text; filter on `http.response.status_code` or `error.type`. Native source-format changes, noisy-log reduction and secret/PII redaction remain separate work.

## Pipeline failures

Metrics-native rules in the `fluent-bit-pipeline` group page through VMAlertmanager when a node's collector target disappears (`FluentBitCollectorTargetMissing`), a node reads no container logs (`FluentBitNodeLogIngestionSilent`), delivery keeps retrying (`FluentBitDeliveryRetrying`), the output buffer passes 75% (`FluentBitBufferNearCapacity`) or records are dropped, including container log lines over 1 MiB that the collector skips (`FluentBitRecordsDropped`). The `logging-pipeline` group covers VictoriaLogs itself, including `VictoriaLogsIngestionSilent` on `vl_rows_ingested_total{type="opentelemetry_protobuf"}`. These alerts depend on the metrics path rather than querying the log backend they diagnose. Follow the [alert-delivery runbook](alerting.md#end-to-end-delivery-test) when retesting delivery.

Fluent Bit's drop counter undercounts loss when its buffer is exhausted (a local drill dropped 2,336 records and counted 1,072), so treat `FluentBitBufferNearCapacity` as the actionable signal and the drop alert as a lower bound.

Before restarting anything, inspect the affected pod's metrics (`/api/v2/metrics/prometheus` on port 2020: `fluentbit_input_records_total`, `fluentbit_output_retries_total`, `fluentbit_output_errors_total`, `fluentbit_output_chunk_available_capacity_percent`, `fluentbit_storage_fs_chunks`), its log, the `victoria-logs-single-server-0` workload and PVC, and vmagent scrape targets. Preserve `/var/lib/fluent-bit` on the node: it holds the tail positions and the buffered chunks. Restore backend health first; Fluent Bit retries without limit and drains its buffer on its own.

## Fluent Bit collector

The DaemonSet runs the official chart with the pinned upstream image. Its pipeline is plain files under [`kubernetes/apps/observability/fluent-bit/app/config/`](../../kubernetes/apps/observability/fluent-bit/app/config/), delivered as the `fluent-bit-config` ConfigMap; Reloader restarts the pods when it changes.

- **Input:** `tail` on `/var/log/containers/*.log` with the `cri` multiline parser, which joins CRI partial records. The path deliberately excludes kubelet-rotated files: matching them replays rotated files as duplicates after every restart. `Read_from_Head` keeps the start of files discovered after startup, so new pods' first lines are collected.
- **Enrichment:** the `kubernetes` filter adds labels, annotations and owner references from the API server (get/list/watch on pods and namespaces).
- **Mapping:** [`normalize.lua`](../../kubernetes/apps/observability/fluent-bit/app/config/normalize.lua) runs in two passes around the JSON parser filter. The first saves identity and the raw line in record metadata, so application payload keys can never overwrite identity; the second builds the OpenTelemetry record described in [Log schema](#log-schema). Text-format level patterns are listed per application at the top of the file.
- **Output:** OTLP/HTTP to `victoria-logs-single-server:9428/insert/opentelemetry/v1/logs` with the stream fields in the `VL-Stream-Fields` header, unlimited retries.

`python3 scripts/logging/test-fluent-bit.py` runs the production configuration and Lua through the pinned image against [fixtures](../../scripts/logging/fluent-bit-fixtures.json), replacing only the Kubernetes filter, the output target and state paths. CI runs it through `scripts/ci/validate-logging.sh`. Add a fixture with every mapping change.

### State, buffering and delivery limits

Tail positions (`tail.db`) and the filesystem buffer persist under the node-local hostPath `/var/lib/fluent-bit`, both with full sync. The output buffer is limited to 4 GiB per node, several days of the measured input rate. A node replacement or disk loss loses this state.

A local delivery drill (numbered fixture, kubelet-style rotation) recovered every record across an 11-minute backend outage with a graceful restart and across a SIGKILL under load, without duplicates. If the collector is killed during a backend outage and a container's file rotates before it restarts, the unread end of the rotated file is lost; the drill lost 33 records that way. The [evaluation](../evaluations/log-schema-and-collector.md#delivery) records the runs.

### Cutover from vlagent

Fluent Bit starts with no tail positions, so it reads every container's current log file from the beginning and VictoriaLogs stores those records a second time, with their original timestamps. Expect up to one file's worth of duplicates per container (kubelet rotates at 10 MiB) in the hours before the cutover; retention removes them. The same replay covers any gap between vlagent stopping and Fluent Bit starting, unless a file rotates in between.

After the rollout, verify on each node: the Fluent Bit pod is Ready, `fluentbit_input_records_total{name="pods"}` increases, `fluentbit_output_retries_total` stays flat, and fresh records for that node's pods arrive with `service.name` set. `/var/lib/vlagent` remains on the nodes until it is removed with exact-path cleanup mounts.

## Collector maintenance and rollback

The DaemonSet rolls one node at a time. Before a planned collector change, restore backend health and check `fluentbit_storage_fs_chunks` is back at its usual low level; a delivery backlog holds hundreds. Never run two Fluent Bit processes against the same `/var/lib/fluent-bit`.

To roll back, revert the Fluent Bit change in a reviewed PR: Git history keeps the vlagent DaemonSet, alerts and dashboards. vlagent resumes from its own checkpoints in `/var/lib/vlagent` if they are still present, replaying what it had not yet read; records already sent by Fluent Bit stay in the new schema until retention removes them.

## Backend crash durability

The deployed VictoriaLogs v1.52.0 binary confirms the upstream [in-memory flush setting](https://docs.victoriametrics.com/victorialogs/#list-of-command-line-flags): `-inmemoryDataFlushInterval` defaults to five seconds, has a one-second minimum, and trades more frequent durable writes for additional disk I/O. Accepted data can remain in memory until that flush; an unclean shutdown can lose it without collector drop counters increasing. The interval concerns backend persistence, not application event timestamps, so old/replayed records can also be affected.

Keep the five-second default for this CPU-constrained homelab and the 4 GiB backend memory limit. Reducing the interval would narrow the persistence window but would not provide synchronous acknowledgement or exactly-once recovery. Revisit if backend crashes recur or the loss tolerance changes. The observed cutover gap is consistent with unflushed data lost at the OOM, but the exact fate of every missing record was not traced through request acknowledgements.
