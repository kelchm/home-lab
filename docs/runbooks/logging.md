# Logging

Kubernetes application and control-plane container stdout/stderr is collected into VictoriaLogs, which is the only log backend. Fluent Bit runs on every node, enriches each record from the Kubernetes API, maps it onto the OpenTelemetry log data model and sends it to VictoriaLogs over OTLP; see [Log schema](#log-schema). Fluent Bit also receives each Talos node's service and kernel logs, and syslog from the UniFi console and its devices, and stores them in the same schema; see [Talos node logs](#talos-node-logs) and [UniFi console logs](#unifi-console-logs). A separate Fluent Bit collector stores Kubernetes events; see [Kubernetes events](#kubernetes-events). The [schema and collector evaluation](../evaluations/log-schema-and-collector.md) records why the schema and collector look the way they do.

Collection covers kubelet-managed container logs, including init containers, native sidecars and static control-plane pods. Talos service and kernel logs are a separate source with their own gaps; see [Delivery and gaps](#delivery-and-gaps). Application files outside stdout/stderr are not collected. Quiet or empty containers do not establish a collection failure: compare Kubernetes inventory, node log files and stored records before declaring a source missing.

## Query logs

- **Grafana Explore:** `https://grafana.home.kelch.io/explore` — the starting point for logs and [Kubernetes events](#kubernetes-events). Select the `VictoriaLogs` datasource for the query editor, visual builder and live logs. Home's log links open it with a query and Home's time range, such as **Logs from** on a namespace in the k8s-prod **Namespaces** table. The query editor's `Run in VMUI` action opens the query in the external VMUI. Open a row's log menu and select `Show log details` to inspect its structured fields alongside `_msg`.
- **VictoriaLogs VMUI:** `https://vlogs.home.kelch.io/select/vmui/` — use the native LogsQL explorer and live-stream view. The bare hostname redirects here.

For routine application browsing:

1. Open a namespace from Home's k8s-prod **Namespaces** table: **Logs from** shows its container lines with its Warning events, such as crash loops, failed probes and mount errors, and **Events in** shows all of its Kubernetes events. Or open Explore, select `VictoriaLogs` and query `k8s.namespace.name:=media`, which returns both in full. Set the time picker around the event. Event lines start with the reason and object.
2. Narrow the query: `service.name:=jellyfin` for one application, `severity_number:>=13` for warnings and worse, `log.iostream:=stderr` for one stream, or `log.iostream:*` for container lines without events. Filtering by application leaves out the namespace's events, which have their own `service.name`; open them beside it with Explore's **Split**. `stderr` is not synonymous with errors, and a severity filter leaves out lines without a recognized severity. Explore shows up to 1,000 matching lines; narrow the query or time range when that limit is reached.
3. Open a row's log menu and choose **Show log details** to inspect its pod, container and application fields. For a container line, **Links** has **Events for this pod**, which opens that pod's Kubernetes events in a second Explore pane over the same time range, matched by pod UID. Use **Show context** there for surrounding records from that pod/container; these can include both stdout/stderr, other severities and times outside the Explore window. Follow [Retained history](#retained-history) when the window reaches older schemas.

The VictoriaLogs datasource v0.33.1, deployed October 18, 2026, adds **Drilldown** beside **Syntax** in Explore's **Code** editor. It provides field/stream breakdowns, message patterns, filter chips and log samples. Treat it as broad discovery: it starts from `*`, discarding the editor expression and stream filters, over the editor's time range. **Go to editor** replaces the original expression and filters; drawer time changes do not transfer. Start from **Raw Logs**, explicitly select the source/namespace again, and verify both the resulting scope and Explore time before continuing. Its first breakdown is `service.name`. The raw Fields list still includes infrastructure labels and application fields; application fields are grouped by their application prefix. See the [qualification record](../evaluations/grafana-victorialogs-drilldown.md) for evidence, acceptance and production verification; [#871](https://github.com/kelchm/home-lab/issues/871) tracks the field-discovery and scope-handoff investigation.

Pattern counts marked `~` are estimates from a 1-in-10 sample and the list is capped at 100 patterns. The details Logs tab shows at most 100 rows, and expanded breakdown rows at most 50; these samples are separate from volume counts. Explore retains the configured 1,000-line limit. Histogram bucket alignment and visible edge buckets can differ from an exact-window count; use `| stats count()` with explicit time bounds for incident totals. Severity buttons and field filters operate on stored fields.

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
| `http.*`, `url.*`, `client.*`, `server.*`, `network.protocol.*`, `network.peer.*`, `tls.*`, `user_agent.original` | HTTP semantic-convention names for Traefik access logs. `network.peer.*` is the connection's peer; `client.*` is the original client only when Traefik reports a single address (it copies `X-Forwarded-For` whole). `error.type` holds the status code for 5xx responses only. |
| `code.file.path`, `code.line.number`, `thread.id` | Source location from klog, or from a `source`, `caller` or `file` value shaped `<file>.<source extension>:<line>` (slog, zap, logrus). Other values of those keys stay application fields. |
| `<application>.<field>` | Every other application field, prefixed by the `app.kubernetes.io/name` label (else the legacy `app` label, owning workload or container), e.g. `traefik.RouterName`, `grafana.logger`, `kustomize-controller.name`. Nested objects join keys with dots; arrays stay one JSON array value (`grafana.plugins`) and are not descended into; an object whose keys are data rather than names (none of its keys could be a field name, or the declared `victoria-metrics-operator.spec_diff`) is one JSON string, and a whole record keyed that way stays in the message. Null fields and empty objects are omitted; nulls inside arrays and encoded objects are kept. Read an encoded object with `unpack_json from <field>` and expand an array with `unroll by (<field>)`. |
| `pipeline.severity_unmapped` | The source stated a level the mapping does not recognize; extend `normalize.lua`. |
| `pipeline.normalize_error` | The mapping raised an error on this record, which keeps its identity and raw line as the message; fix `normalize.lua`. |

kustomize-controller runs with `GroupChangeLog`, so its applied-object log lists objects in arrays under their action (`kustomize-controller.output.unchanged`) instead of one field per object.

To give a workload a better service name than its labels produce, set `resource.opentelemetry.io/service.name` on its pod template.

Record time is the container runtime's timestamp for the line. Application timestamp fields stay application fields, except on Traefik access lines: `time` (the record time, to the second) and `StartLocal` (a copy of `StartUTC`) are dropped. Integers above 2^53 lose precision in Fluent Bit's Lua filter, which converts every number to a double.

Levels come from structured fields (JSON, logfmt, PostgreSQL's `record.error_severity`), klog's leading letter, or a level token at a fixed position in known plain-text formats. Words elsewhere in a line never set severity; an unknown level is stored as unspecified rather than guessed. Terminal colour codes are removed before parsing, and blank lines are dropped. Each line is one record, so continuation lines (Python tracebacks, mcphub's multi-line JSON) are the main unspecified source; read a whole event with **Show context**. The [evaluation](../evaluations/log-schema-and-collector.md#multi-line-events) records why lines are not grouped at collection.

### Retained history

Records written before the Fluent Bit cutover keep vlagent's schema (`kubernetes.pod_namespace`, `kubernetes.pod_name`, `kubernetes.container_name`, `kubernetes.pod_node_name`, `kubernetes.pod_labels.*`, `output_stream`, application fields at top level, `level` only when the application emitted one) until retention removes them, roughly 30 days later. A few Alloy-era rows (`namespace`, `service_name`, `pod`, `container`, `node`, `stream`, `msg.*`) remain until about October 27, 2026.

Query them by their own fields, e.g. `kubernetes.pod_namespace:=printing kubernetes.container_name:=app` for vlagent rows or `namespace:=printing` for Alloy rows; `level` holds the application's own level where it emitted one.

Retention is nominally 30 days, but VictoriaLogs begins deleting the oldest partitions at 80% disk use. The effective retention is whichever limit is reached first. Replayed records are stored as additional rows until retention removes them; queries and counts include them.

## UniFi console logs

The UniFi console (`Gateway DMP`, Network 10.6.106) exports its system log as CEF over syslog to the `victoria-logs-syslog` LoadBalancer at `10.32.130.3` UDP/514, which the VictoriaLogs app owns but which selects the Fluent Bit pods. The same setting makes the gateway send its own daemon syslog and makes adopted switches and access points send plain syslog from their management addresses on the Default network (`10.32.1.0/24`). Fluent Bit listens on container port 5140 on every node; Cilium advertises the address from each node with a Ready collector, and the gateway spreads senders across those nodes, so each datagram reaches exactly one collector. The Service uses `externalTrafficPolicy: Local`, so the fluent-bit CiliumNetworkPolicy sees the sender's address; its syslog rule admits external traffic only from the gateway's VLAN 30 interface, `10.32.30.1`, and the Default network, alongside the namespace baseline's in-cluster allowances.

Fluent Bit maps each datagram onto the same OpenTelemetry schema as container logs and buffers it on the receiving node's disk until VictoriaLogs accepts it, so a backend outage or restart does not drop received datagrams; [Backend crash durability](#backend-crash-durability) covers what VictoriaLogs can lose after accepting them. The export itself is UDP with no retry: datagrams sent while no collector is Ready, or dropped on the path, are lost. [#856](https://github.com/kelchm/home-lab/issues/856) tracks acceptance of this source.

### Console setup

The console account the agents use is View Only, so the operator makes this change. The page names below come from third-party guides for Network 9.x and 10.x; correct them here if the console differs.

1. Open **Settings → CyberSecure → Traffic Logging** and find **Activity Logging (Syslog)**.
2. Select **SIEM Server**. Set **Server Address** to `10.32.130.3` and **Port** to `514`.
3. Enable every content category except **Firewall Default Policy**, which users report multiplies volume about 200 times. Leave **Debug Logs** and **Netconsole** off; they are gateway diagnostics, not events.
4. Apply, then leave the separate **Control Plane → Integrations** syslog export off. Exporting the same categories through both pages duplicates every event.

Within a minute, `_time:5m service.namespace:=unifi | stats by (service.name) count()` should list the gateway and the devices. If it stays empty, check `hubble_drop_total{destination_namespace="observability", reason="POLICY_DENIED", protocol="UDP"}` for a rise; it also counts unrelated traffic, so confirm with `hubble observe --verdict DROPPED --since 10m` in each node's `cilium-agent` container. A dropped UDP packet to a fluent-bit pod's port 5140 shows the sender's actual source address, which then belongs in the CIDRs in [networkpolicy.yaml](../../kubernetes/apps/observability/fluent-bit/app/networkpolicy.yaml).

### Fields

The `unifi_syslog` parser in [parsers.conf](../../kubernetes/apps/observability/fluent-bit/app/config/parsers.conf) splits off the priority and hostname, and [`unifi.lua`](../../kubernetes/apps/observability/fluent-bit/app/config/unifi.lua) maps the rest. The `pipeline.*` flags mean the same as for [container logs](#log-schema).

| Field | Content |
| --- | --- |
| `service.name`, `host.name` | The device name from the syslog header, such as `Gateway-DMP`, `CoreSwitch` or `BasementAP`, or the sender's address for a datagram without a header. `service.name` is a stream field. |
| `service.namespace` | `unifi` on every UniFi record. |
| `host.id`, `host.type`, `os.version` | An adopted device's MAC address, model and firmware, from the `<mac>,<model>-<firmware>` tag it puts before each message. The gateway sends none. |
| `severity_number`, `severity_text` | CEF events: the CEF severity (0–10) as text, mapped 0–3 to INFO (9), 4–6 to WARN (13), 7–8 to ERROR (17) and 9–10 to FATAL (21). Other lines: the syslog priority's severity (`emerg` … `debug`) as the OpenTelemetry Collector maps it, such as `err` 17, `notice` 10 and `debug` 5. CEF lines carry no syslog priority. |
| `_msg` | CEF: `cef.extension.msg`, else `cef.name`. Other lines: the text after the header and device tag, which usually opens with the process, such as `switch: DHCP_SNP: …`, `hostapd[5197]: …` or `systemd[1]: …`. A switch message that spans lines stays one record. |
| `_time` | CEF: `UNIFIutcTime`. Other lines: arrival time, because RFC 3164 headers carry no timezone. |
| `cef.device_vendor`, `cef.device_product`, `cef.device_event_class_id` | CEF header. Product and class ID are stream fields. Product is `UniFi Network` for network events and `UniFi OS` for console events. |
| `cef.name`, `cef.severity`, `cef.device_version`, `cef.version` | Event name, 0–10 severity, application version and CEF version. |
| `cef.extension.<key>` | Every CEF extension, such as `UNIFIcategory`, `msg`, `src`, `dst`, `UNIFIwanName`, `UNIFIipsSignature`, `UNIFIclientAlias` and `UNIFIdeviceName`. Ubiquiti lists the keys in [UniFi System Logs & SIEM Integration](https://help.ui.com/hc/en-us/articles/33349041044119). |
| `syslog.facility` | The syslog facility name, such as `kern`, `user`, `daemon` or `authpriv`, on lines with a priority. |
| `network.peer.address`, `network.peer.port` | The sender's address and source port. |

Plain syslog from the gateway and adopted devices carries no `cef.*` fields, so the CEF stream filters and alerts never match it. Its severity is what the firmware states: access points log routine Wi-Fi driver messages such as `kernel: … recv auth frame …` at `err`, and switches log DHCP snooping counters at `debug`.

Event classes. "Seen" means stored from this console between October 18 and 19, 2026; the others come from Ubiquiti's examples and published captures of Network 9.3–10.6.

| Class | Name | Seen | Notes |
| --- | --- | --- | --- |
| 100 | Internet Down | yes | `UNIFIwanName`, `UNIFIwanId`, `UNIFIwanIsp` |
| 101 | Multiple Internet Disconnections | yes | UniFi's own summary when a WAN has dropped more than once in 24 hours; each drop also arrives as 100. |
| 107 | Internet Restored | yes | `UNIFIreportedDuration` gives the outage length. Published captures report short drops as restored "after a temporary disconnection" in `msg`; this console has not. |
| 112, 113 | High Latency Detected, Packet Loss Detected | yes | `UNIFIwanLatency` on 112 |
| 200 | Threat Detected | yes | `src`, `dst`, `UNIFIipsSignature`, `UNIFIipsSignatureId`, `UNIFIrisk`. IPS runs detect-only, so 201 (Threat Detected and Blocked) is not expected. |
| 202 | Honeypot Triggered | yes | The client as `UNIFIclientAlias`, `UNIFIclientHostname` and `UNIFIclientMac`; no `src` address. |
| 400–404 | Wi-Fi and wired client connected, disconnected and roamed | yes | `UNIFIclientAlias`, `UNIFIclientIp`, `UNIFIclientMac` |
| 510 | Device Updated | yes | `UNIFIdeviceName`, `UNIFIdeviceVersion`, `UNIFIdevicePriorVersion` |
| 512, 513 | Device Offline, Device Reconnected | no | `UNIFIdeviceName` |
| 544–549 | Admin access and configuration changes | 544–546, 549 | `UNIFIadmin`, `UNIFIaccessMethod` |

### Example queries

- Everything the console sent: `service.name:=Gateway-DMP`
- Adopted devices by volume: `service.namespace:=unifi host.id:* | stats by (service.name, severity_text) count()`
- Errors and worse by device: `service.namespace:=unifi severity_number:>=17 | stats by (service.name, severity_text) count()`
- One access point's client events: `service.name:=BasementAP _msg:~"stahtd|hostapd"`
- Events by class: `{cef.device_product="UniFi Network"} | stats by (cef.device_event_class_id, cef.name) count()`
- WAN events: `{cef.device_product="UniFi Network"} cef.extension.UNIFIcategory:="Internet and WAN" | fields _time, cef.name, cef.extension.UNIFIwanName, _msg`
- IDS detections by pair: `{cef.device_product="UniFi Network", cef.device_event_class_id=~"200|201"} | stats by (cef.extension.src, cef.extension.dst, cef.extension.UNIFIipsSignature) count()`
- One client's connections: `{cef.device_product="UniFi Network"} cef.extension.UNIFIclientAlias:="Hyperion" | fields _time, cef.name, _msg`
- Devices going offline: `{cef.device_product="UniFi Network", cef.device_event_class_id="512"}`
- Admin activity: `cef.extension.UNIFIadmin:* OR cef.extension.UNIFIcategory:=Audit OR {cef.device_product="UniFi OS"}` (Network 10.x files admin events under `Audit`; 9.3 used `System` with `UNIFIsubCategory=Admin`)
- Firewall rule hits: `service.name:=Gateway-DMP DESCR`

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
| `UniFiWANFlapping` | Three or more Internet Down, temporary-disconnection or failover events for one WAN within an hour. `UniFiWANDown` already pages a WAN down for 5 minutes; this catches shorter repeated drops. | warning |
| `UniFiIntrusionDetectedOutbound` | An IDS detection, in the last 15 minutes, whose destination is outside private, CGNAT, link-local and multicast space. Lab-internal detections are queryable but do not page, because the baseline is dominated by operator traffic. | warning |
| `UniFiHoneypotTriggered` | Any honeypot event (class 202) in the last 15 minutes, by client alias and MAC address. The honeypot listens at `10.32.1.2` on the Default network. | warning |
| `UniFiSyslogIngestionSilent` | For an hour, Fluent Bit's `fluentbit_input_records_total{name="unifi"}`, summed over nodes, shows no datagrams in the last two days, or the input is missing. It is a MetricsQL rule in the `fluent-bit-pipeline` group, so it does not depend on the log store, and is sized above the 29-hour baseline gap. Gateway daemon and device syslog count too, so it catches a dead listener path or a disabled export rather than a quiet CEF stream. | warning |

CI evaluates each rule against fixtures, including internal and multicast detections that must stay quiet; see [Log alerts](#log-alerts). Checked against this console's records on October 19, 2026: Internet Down (100) and Threat Detected (200) arrive with the class IDs the rules select, and every detection carried `dst`. The temporary-disconnection text that `UniFiWANFlapping` also matches has not appeared, in Internet Restored (107) or elsewhere; the rule still counts every drop through class 100. Honeypot contact from the operator Mac produced class 202 with the client but no `src`, and `UniFiHoneypotTriggered` reached Pushover. A synthetic event sent to the syslog input earlier that day (`service.name=delivery-test`, `src=192.0.2.250`) also reached Pushover and resolved, with no failed notifications. To silence a benign signature, exclude its `cef.extension.UNIFIipsSignatureId` in the rule rather than adding an Alertmanager silence.

### Cutover from direct ingestion

Before Fluent Bit received UniFi syslog, VictoriaLogs' own listener stored it behind the same Service. Fluent Bit started listening first, and the Service's selector then moved to the Fluent Bit pods in place on October 19, 2026, so its backends switched in one update and each datagram reached one backend. The last row VictoriaLogs stored itself arrived at 20:42:41 UTC and the first through Fluent Bit at 20:42:48, a shorter gap than the source's ordinary quiet spells of up to 35 seconds; no CEF event was stored twice, and every new row had `service.name`, a severity and a readable `_msg`. Rows with `service.name` `rollout-test` or `delivery-test` are deliberate tests from that rollout, not UniFi events.

Rows from direct ingestion keep that schema until retention removes them, about 30 days after the cutover: `hostname`, `app_name`, `proc_id`, `remote_ip`, `facility_keyword`, `level` and the header time in `timestamp`, no `service.name` or `severity_number`, and VictoriaLogs' missing-message placeholder in `_msg` on CEF records (`| copy cef.extension.msg as _msg` shows the text). The `cef.*` field names are unchanged, and `cef.device_product` and `cef.device_event_class_id` are stream fields in both schemas, so the CEF alerts and the CEF example queries read both; for everything else, select both with `(service.namespace:=unifi OR remote_ip:*)`.

## Talos node logs

Each node's machined sends its service logs and kernel log to that node's Fluent Bit, which stores them in the same schema with `service.namespace=talos`. Talos sends `json_lines` over TCP to `10.43.0.20:5170`, the fixed ClusterIP of the [`fluent-bit-talos`](../../kubernetes/apps/observability/fluent-bit/app/talos-service.yaml) Service. Its `internalTrafficPolicy: Local` makes Cilium's socket load balancer connect machined to the collector on the same node, so the collector's own node name identifies every record; nothing in Talos's kernel-log format would. The destinations are set in [`machine-logging.yaml`](../../talos/patches/global/machine-logging.yaml): `machine.logging.destinations` for services and a `KmsgLogConfig` document for the kernel log, both applied by Talos without a reboot. The `talos.logging.kernel` kernel argument would do the same as `KmsgLogConfig`, but these nodes boot a UKI, so a kernel argument needs a new image schematic and an upgrade. [#891](https://github.com/kelchm/home-lab/issues/891) records the evaluation.

Talos sends every service it keeps a log for: `machined` and its `controller-runtime` and `dns-resolve-cache` logs, `apid`, `trustd`, `containerd`, `cri`, `etcd`, `kubelet`, `udevd`, `syslogd`, `dashboard`, `auditd`, `kernel` and system extensions such as `ext-iscsid`. Talos cannot select services, so the collector drops four sources:

| Source | Dropped | Daily volume per node, October 19, 2026 |
| --- | --- | --- |
| `auditd` | Everything. It repeats the kernel's audit records, almost all SELinux AVC records (SELinux is permissive) for writes by apps on Longhorn volumes, which Longhorn mounts unlabelled; most come from VictoriaLogs and VictoriaMetrics. [#905](https://github.com/kelchm/home-lab/issues/905) traced the flood and kept auditd dropped. Talos v1.14.4 ([#921](https://github.com/kelchm/home-lab/issues/921)) stopped Cilium's denials on its state directories and bpffs, confirmed on every node by a complete, loss-free capture ([results](https://github.com/kelchm/home-lab/issues/911#issuecomment-6307959306)); [#911](https://github.com/kelchm/home-lab/issues/911) re-measures after Longhorn `seLinuxMount` and decides whether to collect auditd. | 0.4–3.8 GB, 2–18 million lines, on Talos 1.13. On v1.14.4 on October 21, 0.16–19.6 audit events a second per node, highest on the nodes running VictoriaLogs and VictoriaMetrics. The kernel also logs `audit: rate limit exceeded` and an `audit_lost` count. |
| `kernel` | Everything. machined's text copy of the kernel ring buffer, the lines `talosctl logs kernel` shows; the `KmsgLogConfig` stream sends the same records with their facility, priority and sequence number. On k8s-prod-1's first apply, the copy still held boot lines the ring buffer had overwritten. | The kernel log's own volume, sent a second time. |
| `machined` | Successful authorizations (`machined/authz/… authorized …`) and successful API calls (`machined OK […]`), nearly all from the console dashboard polling machined. Denials and failed calls are kept; apid logs every remote call. | About 87 MB, 470,000 lines; the 1 MiB buffer `talosctl logs machined` reads holds about 17 minutes. |
| `dns-resolve-cache` | Debug lines, which dump every DNS query the node's DNS cache forwards. | Up to 34 MB. |

What remains is about 46,000 rows and 9 MB a day for the three nodes, about 5% of the rows VictoriaLogs stores. Most of it is `controller-runtime`, `cri`, `etcd` and `kubelet`; the kernel log adds a few hundred lines a day after the few thousand of each boot. The dropped records still cross the node's loopback; `fluentbit_filter_drop_records_total{name="talos"}` counts them.

### Talos fields

The mapping is `talos_normalize` in [`normalize.lua`](../../kubernetes/apps/observability/fluent-bit/app/config/normalize.lua). The `pipeline.*` flags mean the same as for [container logs](#log-schema).

| Field | Content |
| --- | --- |
| `service.name` | The Talos service, or `kernel`. Stream field. Fluent Bit sends it as a record attribute rather than a resource attribute, because its OTLP output gives a whole chunk one resource; VictoriaLogs stores both kinds as the same fields. |
| `service.namespace`, `host.name`, `k8s.node.name`, `k8s.cluster.name` | `talos`, the node name twice and `k8s-prod`. `k8s.node.name` is a stream field. |
| `severity_number`, `severity_text` | Kernel: its priority (`emerg` … `debug`), mapped as for [UniFi syslog](#fields). `controller-runtime`, `dns-resolve-cache`: the level token at the start of the line. Plain text: the level in its format, for the formats these services write (CNI plugins' `<time> [verbose]` in `cri`, klog's leading letter, logrus text's `level=`, grpc-go's `WARNING:` in `etcd`). Other `containerd`, `cri` and `etcd` lines: Talos's `talos-level`, which Talos takes from the line's own JSON level. `kubelet`: 9 for records with a verbosity `v` and 17 for records without one, which is how Kubernetes JSON logs mark errors. Everything else is 0: Talos reports `info` for any line without a level, so its value is not used. Where no level text is sent, VictoriaLogs stores the number's name (`Info`, `Error`, `Unspecified`) as `severity_text`. |
| `_msg` | The message, without a leading timestamp or level. Talos appends a JSON `err` value to the message (`DeleteContainer returned error: …`). |
| `_time` | The write time at the start of the line (machined, `controller-runtime`, `dns-resolve-cache`, apid, trustd, CNI plugins and logrus text in `cri`), else Talos's `talos-time`: the service's own JSON timestamp (etcd, kubelet, containerd, cri), the kernel's timestamp, or for other plain text the time Talos read the line. |
| `syslog.facility` | Kernel records: `kern`, or `user` for Talos's own sequencer lines (`[talos] task … done`), which it writes to the kernel log at `warning` and to machined's log. |
| `kernel.seq`, `kernel.clock` | The kernel's sequence number and microseconds since boot. A gap in `kernel.seq` marks missing kernel records, whether the ring buffer overran or a record was lost on the way. |
| `code.file.path`, `code.line.number` | The `caller` of etcd and kubelet records. |
| `<service>.<field>` | Every other field Talos split from the line, such as `etcd.compact-revision`, `kubelet.podUID` or `controller-runtime.controller`, shaped as for container logs. |

### Delivery and gaps

Talos keeps each service's last 1 MiB of output in memory and reads the kernel ring buffer. Per service, it sends one line at a time; while the collector is unreachable, each attempt waits up to five seconds and the next follows a second later, and when one succeeds Talos continues from its buffer, so a short outage delays records rather than losing them. Once the writer laps a waiting sender, the sender skips to the newest 64 KiB of the buffer and everything before is lost without a trace; machined's buffer laps in about 17 minutes and `controller-runtime`'s and `cri`'s in half a day to a day; kernel gaps show in `kernel.seq`. When a node first receives this configuration, each service's sender has been waiting since boot: a quiet service sends its whole history, a busy one its first few lines from boot and then its newest 64 KiB, and the kernel log what its ring buffer still holds. Expect thousands of records with older timestamps from that node.

- **Boot:** records wait in Talos's buffers until Cilium and the node's collector run, then arrive with their own timestamps. A boot that hangs before that sends nothing; the console and `talosctl logs`/`dmesg` are the evidence.
- **Shutdown and reboot:** once Talos stops the node's pods, including the collector, its remaining shutdown records are lost with the reboot. Watch the console for the end of a shutdown.
- **Collector restart:** Talos treats a write the node's TCP stack accepts as delivered. A sender whose connection outlives the old collector pod keeps writing into it until TCP gives up, then reconnects to the new pod; everything written in between is lost. On k8s-prod-1 on October 21, 2026, the kernel log's connection stayed on the deleted pod for 16 minutes and lost 14 records, a gap in `kernel.seq`, while the service logs kept arriving; [#917](https://github.com/kelchm/home-lab/issues/917) tracks a fix. Received records are buffered on the node's disk like container logs, in a 256 MiB `talos` output buffer.
- **Long lines:** machined reads each service's buffer with a 64 KiB line limit. A longer line stops forwarding of that service until the node reboots. The longest sampled line was under 4,000 characters.
- **Exposure:** port 5170 is reachable from the node hosts and the observability namespace, as the namespace baseline allows; senders are not authenticated.

### Query Talos logs

Talos records carry no Kubernetes namespace, so a namespace query leaves them out. Home's **Talos logs from** link on each k8s-prod node opens that node's records in Explore. Explore and VMUI take LogsQL such as:

- One node's services: `service.namespace:=talos k8s.node.name:=k8s-prod-1 | stats by (service.name) count()`
- A node's boot sequence: `{service.name="kernel", k8s.node.name="k8s-prod-1"} "[talos]"`, or `service.name:=machined "[talos]"`
- Kernel warnings and worse: `{service.name="kernel"} syslog.facility:=kern severity_number:>=13`
- Controller errors: `{service.name="controller-runtime"} severity_number:>=17 | fields _time, k8s.node.name, controller-runtime.controller, _msg, controller-runtime.error`
- kubelet errors: `{service.name="kubelet"} severity_number:>=17`
- etcd warnings: `{service.name="etcd"} severity_number:>=13`
- Remote API calls: `{service.name="apid"}`

### Roll out the Talos configuration

Merge the receiver first and let Flux roll the DaemonSet; Talos must not be pointed at an address without a listener. The first apply replays the kernel log and service buffers back to the node's boot, and VictoriaLogs rejects records older than its 30-day retention as `vl_rows_dropped_total{reason="too_small_timestamp"}`, which pages `VictoriaLogsRowsDropped`. Apply to a node within 30 days of its boot (`talosctl -n <node-ip> get machinestatus -o yaml` shows it as `created`). Then, one node at a time:

1. Confirm the collector is ready: the node's Fluent Bit pod is Ready, `kubectl -n kube-system exec <cilium-agent pod on the node> -c cilium-agent -- cilium-dbg service list` maps `10.43.0.20:5170` to that pod's address only, and `fluentbit_input_records_total{name="talos",node="<node>"}` exists at 0.
2. Run `task talos:apply-node IP=<node-ip>`. The dry-run diff must add only the `machine.logging.destinations` entry and the `KmsgLogConfig` document, and its summary must say the configuration applies without a reboot. Stop if it differs; otherwise confirm.
3. Within a few minutes, check that `fluentbit_input_records_total{name="talos",node="<node>"}` rises, `fluentbit_output_retries_total{name="talos"}` stays flat, `service.namespace:=talos k8s.node.name:=<node> | stats by (service.name) count()` lists the kernel and the running services, and `service.namespace:=talos (pipeline.normalize_error:* OR pipeline.severity_unmapped:*)` is empty. `talosctl -n <node-ip> services` and the Kubernetes node stay healthy.
4. Wait ten minutes, recheck the silence and retry counters, then continue with the next node.

To roll back, revert the patch and apply it the same way; Talos stops sending without a reboot. A Talos node pointed at a missing collector only keeps retrying.

## Kubernetes events

The `fluent-bit-events` collector, one replica of the node collectors' chart and image, watches the API server's events with Fluent Bit's `kubernetes_events` input and stores them in the same schema with `service.name=kubernetes-events`, one stream per namespace. Its pipeline is [`config/events/`](../../kubernetes/apps/observability/fluent-bit/app/config/events/), delivered as the `fluent-bit-events-config` ConfigMap, so changing it never restarts the node collectors. The [evaluation](../evaluations/log-schema-and-collector.md#kubernetes-events) records the decisions.

### Event fields

[`events.lua`](../../kubernetes/apps/observability/fluent-bit/app/config/events/events.lua) maps each event as the OpenTelemetry Collector's Kubernetes events receiver does, with the differences the [evaluation](../evaluations/log-schema-and-collector.md#kubernetes-events) lists; the most visible is that the line names the reason and object.

| Field | Content |
| --- | --- |
| `_msg` | The reason, involved object and message, in the order `kubectl events` shows them, as in `Unhealthy Pod/prowlarr-7d6c5b4a3-z9xwv: Readiness probe failed: …`. Events stored before this line format was deployed on October 22, 2026 hold the message alone until retention removes them. |
| `severity_number`, `severity_text` | `Normal` is 9 (info), `Warning` 13 (warn), `Error` 17 and `Critical` 21, with the type as the text. Any other type is stored as unspecified with `pipeline.severity_unmapped`. |
| `k8s.namespace.name` | The involved object's namespace, absent for cluster-scoped objects such as nodes. Stream field. |
| `k8s.object.kind`, `k8s.object.name`, `k8s.object.uid`, `k8s.object.fieldpath`, `k8s.object.api_version`, `k8s.object.resource_version` | The involved object. `fieldpath` names a container, as in `spec.containers{app}`. |
| `k8s.event.reason`, `k8s.event.action`, `k8s.event.count` | The reason (`BackOff`, `FailedMount`, `Scheduled`), the action newer reporters set, and how many times the event has occurred (`series.count` for an event series). |
| `k8s.event.reporting_controller`, `k8s.event.reporting_instance`, `k8s.node.name` | Who reported it: the controller (`reportingComponent`, else `source.component`), its instance, and the node for kubelet events. |
| `k8s.event.name`, `k8s.event.uid`, `k8s.event.start_time` | The Event object and its creation time. |

Record time is the latest occurrence the record reports: `series.lastObservedTime` for an event series, otherwise the event's `lastTimestamp`, else `firstTimestamp`, else its creation time, as Fluent Bit's input sets it. A repeated event is stored again at each update. Older reporters, such as the kubelet and Flux, update the event on every occurrence, so a crash loop's `BackOff` arrives at each new count. Reporters on the newer events API, such as the scheduler, keep a series: they write it at the second occurrence, every 30 minutes after that, and once more six minutes after the last occurrence, so a pod stuck in `FailedScheduling` arrives at those writes.

### Event delivery

The API server keeps an event for an hour after its last write. The collector lists them, then watches for changes; each watch ends after 10 minutes and the collector lists again, skipping what it has already sent. A new collector process has no such memory, so after a restart it stores every event the API server holds a second time, about the last hour; the copies share `k8s.event.uid` and `k8s.event.count`. An outage longer than an hour loses the events that expired during it. Deletions are not recorded. The input dates an event series by its creation and drops anything older than its retention before mapping, so the retention is ten years, effectively off: a series that keeps being written can outlive the API server's hour by any length.

The output buffer is 256 MiB on the pod's `emptyDir`, under the same retry and buffer alerts as the node collectors; see [Pipeline failures](#pipeline-failures). `KubeEventsIngestionSilent` fires when `fluentbit_input_records_total{job="fluent-bit-events",name="events"}` has not risen for 45 minutes, summed over pods so a replaced collector's last counter does not count, or is absent because the collector is down, unscraped or has no events input; Flux alone records more than 200 events an hour. A watch the API server refuses appears in the pod's log as `http_status=`, such as 403 if the ClusterRole lacks events.

### Query events

Each event's line names the reason and object, so a word such as `BackOff` or a quoted pod name, `"jellyfin-0"`, finds events in Explore and VMUI, not only words from the message. A namespace query in Explore returns the namespace's events among its container lines; on Home, a namespace's **Logs from** link keeps only its Warning events and **Events in** lists them all. A container line's details link to its pod's events: the VictoriaLogs datasource derives a **Pod events** field from `k8s.pod.uid` and queries `k8s.object.uid` with it. That pane opens without a volume histogram, because the datasource plugin builds the linked query without a query type. Control-plane static pods (`kube-apiserver`, `kube-controller-manager`, `kube-scheduler`) log under their mirror pod's UID, while the kubelet can report their events under the static pod's own UID, so their link may come up empty; query those by name, such as `{service.name="kubernetes-events"} k8s.object.name:="kube-apiserver-k8s-prod-1"`. Explore and VMUI take LogsQL such as:

- `{service.name="kubernetes-events", k8s.namespace.name="media"}` lists one namespace's events.
- `{service.name="kubernetes-events"} severity_number:>=13` lists warnings across the cluster.
- `{service.name="kubernetes-events"} k8s.object.name:="jellyfin-0"` follows one object: scheduling, image pulls, mounts and probe failures.
- `{service.name="kubernetes-events"} k8s.event.reason:=BackOff | stats by (k8s.namespace.name, k8s.object.name) max(k8s.event.count) backoff_events` ranks crash loops by how often the kubelet reported back-off. The kubelet records `BackOff` each time it finds a container still waiting, so this counts waits, not restarts; `kube_pod_container_status_restarts_total` counts restarts.

`python3 scripts/logging/test-fluent-bit-events.py` runs the production events configuration through the pinned image against [`events-api.py`](../../scripts/logging/events-api.py), which serves a list and a watch stream as the API server does, with [fixtures](../../scripts/logging/fluent-bit-events-fixtures.json) for each mapping rule, retention, repeated events and series, ignored watch types, and the list that follows a watch, including an event created while the watch was down. CI runs it through `scripts/ci/validate-logging.sh`.

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

Metrics-native rules in the `fluent-bit-pipeline` group page through VMAlertmanager when a node's collector target disappears (`FluentBitCollectorTargetMissing`), a node reads no container logs (`FluentBitNodeLogIngestionSilent`) or receives no Talos logs (`TalosLogIngestionSilent`), delivery keeps retrying (`FluentBitDeliveryRetrying`), the output buffer passes 75% (`FluentBitBufferNearCapacity`) or records are dropped, including container log lines over 1 MiB that the collector skips (`FluentBitRecordsDropped`), and when no UniFi syslog arrives for two days (`UniFiSyslogIngestionSilent`). The retry, buffer and drop alerts also cover the `fluent-bit-events` collector, and `KubeEventsIngestionSilent` fires when it reads no [Kubernetes events](#event-delivery) for 45 minutes. The `logging-pipeline` group covers VictoriaLogs itself, including `VictoriaLogsIngestionSilent` on `vl_rows_ingested_total{type="opentelemetry_protobuf"}`. These alerts depend on the metrics path rather than querying the log backend they diagnose. Follow the [alert-delivery runbook](alerting.md#end-to-end-delivery-test) when retesting delivery.

Fluent Bit's drop counter undercounts loss when its buffer is exhausted (a local drill dropped 2,336 records and counted 1,072), so treat `FluentBitBufferNearCapacity` as the actionable signal and the drop alert as a lower bound.

Before restarting anything, inspect the affected pod's metrics (`/api/v2/metrics/prometheus` on port 2020: `fluentbit_input_records_total`, `fluentbit_output_retries_total`, `fluentbit_output_errors_total`, `fluentbit_output_chunk_available_capacity_percent`, `fluentbit_storage_fs_chunks`), its log, the `victoria-logs-single-server-0` workload and PVC, and vmagent scrape targets. Preserve `/var/lib/fluent-bit` on the node: it holds the tail positions and the buffered chunks. Restore backend health first; Fluent Bit retries without limit and drains its buffer on its own.

## Log alerts

Log content pages only where the log is the only evidence of a failure. Crash loops, readiness, Flux reconciliation, certificates, Longhorn and this pipeline already page from metrics, so their log lines do not; generic error-rate, new-pattern and fatal-level alerts were rejected because they fire on rollouts and transient upstream errors. The [log alerting evaluation](../evaluations/log-alerting.md) records the evidence. Besides the [UniFi alerts](#alerts), `vmalert-victoria-logs` evaluates [kanidm's log rules](../../kubernetes/apps/observability/victoria-logs-single/app/kanidm-alerts.yaml), because kanidm exports no metrics.

| Alert | Fires when | Severity |
| --- | --- | --- |
| `KanidmReplicationFailing` | One pod logs replication errors in at least 15 of the last 20 minutes. Each consumer polls its suppliers every 15 seconds and logs every failed poll; rolling restarts log for a minute or two. It resolves within about 7 minutes of replication recovering. A node down for more than 15 minutes can also fire it, alongside `KubeNodeNotReady`. | warning |
| `KanidmSignInsRejected` | Five or more rejected sign-in attempts in 15 minutes, counted once per request: wrong password, TOTP, passkey or backup code, a softlocked account, or an unknown account name. There were none in the 30 days to October 19, 2026, apart from one unknown account name. | warning |

For `KanidmReplicationFailing`, read the pod's errors with `{k8s.namespace.name="identity", service.name="kanidm"} severity_number:>=17 _msg:~"(?i)supplier|consumer|replica|RUV"`. "Unable to connect to supplier" while every pod is Ready points at the replication path on port 8444, including the kanidm CiliumNetworkPolicy. "Consumer is out of date and must be refreshed" follows a replica that was offline for more than 7 days; refresh that consumer as kanidm's [replication administration](https://kanidm.github.io/kanidm/stable/repl/administration.html) describes.

For `KanidmSignInsRejected`, `{k8s.namespace.name="identity", service.name="kanidm"} ("Result::Denied" OR "Auth result: Denied" OR "Auth result: NoMatchingEntries")` lists the rejections. Each line starts with its request ID; the request line with the same ID gives the client address, which is Traefik for browser sign-ins, so match the time in Traefik's access log for `auth.home.kelch.io`.

To add a log alert, put a VMRule labelled `alerting.home.kelch.io/datasource: victorialogs` in the VictoriaLogs app, which `vmalert-victoria-logs` reads, using `type: vlogs`, a `_time` window in the expression, a stream filter on `service.name` and `k8s.namespace.name`, `k8s.cluster.name:=k8s-prod` and the group label `cluster: k8s-prod`. Rename dotted fields in the result (`| rename k8s.pod.name as pod`), because alert labels cannot contain dots. Add cases to [logsql-rules-fixtures.json](../../scripts/logging/logsql-rules-fixtures.json): `python3 scripts/logging/test-logsql-rules.py`, run in CI by `scripts/ci/validate-logging.sh`, fails when a rule has no cases. It has vmalert parse the rules, evaluates each expression in VictoriaLogs at each case's time less vmalert's 30-second evaluation delay and checks the result labels, the values and the labels the annotations use. It does not exercise `for`, so express persistence in the expression, as `KanidmReplicationFailing` counts minutes with errors.

## Accepted warnings

These sources log warnings or errors in normal operation. Each was traced to its cause and judged harmless; do not suppress them in the pipeline. Counts cover 8 hours ending 2026-10-19 21:40 UTC unless a row states its own window.

| Source | Message | Cause | Revisit |
| --- | --- | --- | --- |
| `kube-apiserver` | `grpc: addrConn.createTransport failed to connect to {Addr: "127.0.0.1:2379" ...} ... operation was canceled`, about 2,800 rows (about 960 per apiserver) | Each request to the apiserver's `/metrics` builds a fresh etcd client for the `apiserver_storage_size_bytes` collector and closes it before its spare connection finishes dialing. Almost every row lands within 50 ms of vmagent's 30-second scrape of that apiserver; the few between scrapes are consistent with other `/metrics` requests. Upstream [kubernetes#134080](https://github.com/kubernetes/kubernetes/issues/134080) and [#139744](https://github.com/kubernetes/kubernetes/issues/139744); the fix, [#138075](https://github.com/kubernetes/kubernetes/pull/138075), ships in v1.37 and was not backported. | After the upgrade to Kubernetes 1.37, confirm the rows stop. |
| `kube-multus-ds` | `[error] Multus: GetPod failed: pod not found ...` followed by `[error] Multus: failed to get the cached delegates file ... cannot properly delete`, about 240 pairs | The container runtime repeats CNI DEL when it removes a sandbox after the pod object is gone. The first DEL, with the network namespace, already tore the attachments down and consumed Multus's cache file. Multus v4.2.4 and v4.3.1 log the repeat at error and return success. In the sample, 122 of the 123 affected sandboxes had an earlier successful DEL; the other one's earlier DEL predates the window. | If the cached-delegates error appears without an earlier `DEL finished` for the same container ID and a non-empty `Netns`. |
| `prometheus-node-exporter` | `error encoding and sending metric family: write tcp ...: write: broken pipe`, a burst of one row per remaining metric family (166 on 2026-10-19) | vmagent closed a scrape in progress when service discovery replaced node-exporter targets during a DaemonSet rollout. It is the only burst since the earliest stored node-exporter record, on 2026-10-04. Collectors take under 0.35 s against a 10 s scrape timeout. | If bursts appear outside rollouts, or scrape durations approach the timeout. |
| `grafana` | `[SHOULD NOT HAPPEN] failed to update managedFields` from `grafana-apiserver`, with `field not declared in schema` for `.spec.elements.*.kind`, `.spec.layout.kind` and `.spec.variables[*].kind`; 2 rows in 24 hours | Grafana's managed-fields bookkeeping cannot type-check the v2 dashboard schema's kind-tagged elements, so it logs once per save of a v2 dashboard. The save succeeds. Upstream [grafana#128991](https://github.com/grafana/grafana/issues/128991), [grafana-app-sdk#1485](https://github.com/grafana/grafana-app-sdk/issues/1485). | After a Grafana upgrade, save a v2 dashboard and check for the row. |
| `mcphub` | `[SESSION ERROR] No session ID provided for non-initialize request (method: server/discover)`, 128 rows in 8 hours to 21:50 UTC, spread across all six groups | Claude Code probes `server/discover` (MCP `2026-07-28`) on each new connection. MCPhub 1.0.41 does not serve that method, logs this, and the client falls back to `initialize` normally. | When [#707](https://github.com/kelchm/home-lab/issues/707) upgrades MCPhub past 1.0.41, confirm the rows stop. |
| `qbittorrent` (`gluetun` container) | `ERROR [port forwarding] adding port mapping: ... 10.2.0.1:5351: recvfrom: connection refused` (or `i/o timeout`), then `starting port forwarding service: ... getting external IPv4 address: ...`; 154 rows in 8 hours to 21:50 UTC, and 60–330 port re-establishments a day since at least 2026-10-03 | ProtonVPN's NAT-PMP gateway refuses or drops a 45-second renewal at random, median 4.5 minutes apart. gluetun v3.41.3 restarts port forwarding after one failed renewal and gets the same port (51200) back within 5–80 seconds. qBittorrent reports `connected` throughout. | If qBittorrent's `connection_status` is `firewalled`, the forwarded port changes, or re-establishment takes minutes. |

## Fluent Bit collector

The DaemonSet runs the official chart with the pinned upstream image. Its pipeline is plain files under [`kubernetes/apps/observability/fluent-bit/app/config/`](../../kubernetes/apps/observability/fluent-bit/app/config/), delivered as the `fluent-bit-config` ConfigMap; Reloader restarts the pods when it changes. The `fluent-bit-events` release runs the [Kubernetes events](#kubernetes-events) pipeline as a single-replica Deployment from the same chart and image.

- **Inputs:** `tail` on `/var/log/containers/*.log` with the `cri` multiline parser, which joins CRI partial records. The path deliberately excludes kubelet-rotated files: matching them replays rotated files as duplicates after every restart. `Read_from_Head` keeps the start of files discovered after startup, so new pods' first lines are collected. `tcp` on 5170 receives [Talos logs](#talos-node-logs) as JSON. `syslog` on UDP 5140 receives [UniFi syslog](#unifi-console-logs); a `rewrite_tag` filter gives each device its own tag, by hostname or else sender address, because the OTLP output takes a chunk's resource from its first record.
- **Enrichment:** the `kubernetes` filter adds labels, annotations and owner references from the API server (get/list/watch on pods and namespaces).
- **Mapping:** [`normalize.lua`](../../kubernetes/apps/observability/fluent-bit/app/config/normalize.lua) runs in two passes around the JSON parser filter. The first saves identity and the raw line in record metadata, so application payload keys can never overwrite identity; the second builds the OpenTelemetry record described in [Log schema](#log-schema). Text-format level patterns are listed per application at the top of the file. `talos_normalize` in the same file maps Talos records in one pass. [`unifi.lua`](../../kubernetes/apps/observability/fluent-bit/app/config/unifi.lua) maps UniFi syslog and CEF.
- **Outputs:** OTLP/HTTP to `victoria-logs-single-server:9428/insert/opentelemetry/v1/logs` with the stream fields in the `VL-Stream-Fields` header and unlimited retries: `victorialogs` for container logs, `talos` for Talos logs and `unifi` for UniFi syslog.

`python3 scripts/logging/test-fluent-bit.py` runs the production configuration and Lua through the pinned image against [fixtures](../../scripts/logging/fluent-bit-fixtures.json), replacing only the Kubernetes filter, the output target and state paths, and sends the UniFi fixtures to the syslog input as UDP datagrams and the Talos fixtures to the tcp input as `json_lines`. CI runs it through `scripts/ci/validate-logging.sh`. Add a fixture with every mapping change.

### State, buffering and delivery limits

Tail positions (`tail.db`) and the filesystem buffer persist under the node-local hostPath `/var/lib/fluent-bit`, both with full sync. The output buffers are limited per node to 4 GiB for container logs, several days of the measured input rate, and 256 MiB each for Talos logs, months at the measured rate, and UniFi syslog, more than a week at the 48,419 datagrams stored in the 24 hours to October 19, 2026. A node replacement or disk loss loses this state.

Chunk checksums are off (`storage.checksum Off`). ChunkIO finalizes a chunk's CRC only when it syncs the chunk, so with checksums on, a SIGKILL or OOM kill discards every chunk written since its last sync, about a flush interval of each busy file's records, although its content is intact. Local delivery drills (numbered fixture, kubelet-style rotation) recovered every record across an 11-minute backend outage with a graceful restart, and across six SIGKILLs at 250 lines/s on XFS, without duplicates, as did two SIGKILLs of k8s-prod-3's collector on October 21, 2026; with checksums on, the same kills lost 43 to 226 records each. No drill duplicated a record, but a kill between a tail read and its position update can replay records. Without the checksum nothing verifies chunk content: a chunk damaged by a power loss, or by a kill during its first copy, can deliver a record with a zeroed or missing span, and drops the records after damage that breaks decoding without a log line. A chunk whose header page is lost or whose file is truncated is rejected (`format check failed`) and stays on disk.

A power loss can lose each file's records read since its chunk was last synced, about one flush interval, with either setting: tail positions are synced on every update, chunk data only when the output takes the chunk. Four simulated power losses (XFS shutdown) at 250 lines/s lost 35 to 209 records, the same with checksums on or off. If a container's file rotates while the collector is stopped, for any reason, the unread end of the rotated file is lost: 33 records when the October drill's collector was killed during an outage, and 184 when the kubelet rotated the fixture's file during a restart after a kill. The [evaluation](../evaluations/log-schema-and-collector.md#delivery) records the runs.

To repeat the kill drill, run [`numbered-fixture.py`](../../scripts/logging/numbered-fixture.py) as a pod labelled `app.kubernetes.io/name: drill` in a `log-drill` namespace with `FIXTURE_RUN` set to a unique name, `FIXTURE_COUNT=20000` and `FIXTURE_RATE=250`. Kill the collector with SIGKILL from the node: `pkill -9 -f '^/fluent-bit/bin/fluent-bit '` in a kind node or a host-PID pod, whose namespace needs `pod-security.kubernetes.io/enforce=privileged` (BusyBox's `pkill -x` does not match the collector). Kill it once or twice, at least 10 seconds apart and away from the fixture's 10 MiB rotations, about 70 seconds apart at this rate. Then, with VictoriaLogs port-forwarded to `localhost:9428`, run `python3 scripts/logging/verify-numbered.py --url http://localhost:9428 --app drill --run <run> --expected 20000 --start <RFC3339 before the pod started> --end <RFC3339 after the last record>`. It checks every record's stream, severity, timestamp and body and lists missing records as one range per loss. A SIGKILL drill on a production collector needs the owner's approval.

### Cutover from vlagent

Fluent Bit starts with no tail positions, so it reads every container's current log file from the beginning and VictoriaLogs stores those records a second time, with their original timestamps. Expect up to one file's worth of duplicates per container (kubelet rotates at 10 MiB) in the hours before the cutover; retention removes them. The same replay covers any gap between vlagent stopping and Fluent Bit starting, unless a file rotates in between.

After the rollout, verify on each node: the Fluent Bit pod is Ready, `fluentbit_input_records_total{name="pods"}` increases, `fluentbit_output_retries_total` stays flat, and fresh records for that node's pods arrive with `service.name` set. `/var/lib/vlagent` was removed from every node on 2026-10-19.

## Collector maintenance and rollback

The DaemonSet rolls one node at a time. Cilium advertises the UniFi syslog address only from nodes with a Ready collector, so the gateway sends to the other nodes while one restarts. Before a planned collector change, restore backend health and check `fluentbit_storage_fs_chunks` is back at its usual low level; a delivery backlog holds hundreds. Never run two Fluent Bit processes against the same `/var/lib/fluent-bit`. Chunks written with checksums off fail the check if checksums are turned on again, so drain the buffer before such a change.

To roll back, revert the Fluent Bit change in a reviewed PR: Git history keeps the vlagent DaemonSet, alerts and dashboards. Its checkpoints were removed with `/var/lib/vlagent`, so a restored vlagent starts without read positions; records already sent by Fluent Bit stay in the new schema until retention removes them.

## Backend crash durability

The deployed VictoriaLogs v1.53.0 binary confirms the upstream [in-memory flush setting](https://docs.victoriametrics.com/victorialogs/#list-of-command-line-flags): `-inmemoryDataFlushInterval` defaults to five seconds, has a one-second minimum, and trades more frequent durable writes for additional disk I/O. Accepted data can remain in memory until that flush; an unclean shutdown can lose it without collector drop counters increasing. The interval concerns backend persistence, not application event timestamps, so old/replayed records can also be affected.

Keep the five-second default for this CPU-constrained homelab and the 4 GiB backend memory limit. Reducing the interval would narrow the persistence window but would not provide synchronous acknowledgement or exactly-once recovery. Revisit if backend crashes recur or the loss tolerance changes. The observed cutover gap is consistent with unflushed data lost at the OOM, but the exact fate of every missing record was not traced through request acknowledgements.
