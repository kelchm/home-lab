# Logging

Kubernetes application and control-plane container stdout/stderr is collected into VictoriaLogs, which is the only log backend. The UniFi console's event export also lands there; see [UniFi console logs](#unifi-console-logs). **vlagent runs on all three production nodes as of September 26, 2026.** Each node is labeled `logging.home.kelch.io/collector=vlagent`; add that label when provisioning another logging node. Alloy is retired from Flux. Its node-local `/var/lib/alloy` checkpoints are retained through October 3, 2026 as a short rollback precaution; [#583](https://github.com/kelchm/home-lab/issues/583) tracks their final removal.

The pinned collector discovers regular containers and init containers, including native sidecars and static control-plane pods. It does not enumerate ephemeral debug containers; none were present in the September 26 coverage audit. Collection covers kubelet-managed container logs, not Talos service/kernel journals or application files outside stdout/stderr. Quiet or empty containers do not establish a collection failure: compare Kubernetes inventory, node log files, read checkpoints and stored records before declaring a source missing.

## Query logs

- **Kubernetes logs dashboard:** [Kubernetes / Logs](https://grafana.home.kelch.io/d/kubernetes-logs) — the production starting point for application logs. Its namespace/service selectors and stream/severity filters cover retained Alloy and new vlagent data.
- **Grafana Explore:** `https://grafana.home.kelch.io/explore` — select the `VictoriaLogs` datasource for the query editor, visual builder and live logs. The query editor's `Run in VMUI` action opens the query in the external VMUI. Open a row's log menu and select `Show log details` to inspect its structured fields alongside `_msg`.
- **VictoriaLogs VMUI:** `https://vlogs.home.kelch.io/select/vmui/` — use the native LogsQL explorer and live-stream view. The bare hostname redirects here.

For routine application browsing:

1. Open **Kubernetes / Logs** and set the time picker around the event. The initial view is the last 15 minutes in `observability`.
2. Choose **Namespace**, then type the application name in **Service**. Suggestions come from logs in that time window; service choices follow the namespace selection. Multiple selections and **All** are supported. If an older application is absent, move the time window to when it was running.
3. Start with **Stream** and **Severity** at **All**, then narrow them as needed. `stderr` is not synonymous with errors. **unknown** includes messages without a recognized severity. The panel shows up to 1,000 matching lines; narrow the application/time selection when that limit is reached.
4. Open a row's log menu and choose **Show log details** to inspect its pod, container and application fields. Use **Show context** there for surrounding records from that pod/container; these can include both stdout/stderr, other severities and times outside the dashboard window. Use Explore for additional LogsQL filtering, following [Query compatibility](#query-compatibility) when combining old and new data.

The datasource plugin v0.31.0 serializes the VMUI handoff's absolute end time as UTC without a timezone suffix; VictoriaLogs v1.52.0 VMUI interprets it in its selected timezone. In an America/New_York browser, the September 26 check shifted a historical window by four hours. The plugin also omits seconds. For exact historical windows, set and verify the time range explicitly in VMUI. This limitation is accepted ([#485](https://github.com/kelchm/home-lab/issues/485)); upstream datasource v0.32.0 still uses the same serialization. Revisit when a datasource release sends a timezone-qualified end time.

Grafana authenticates through its native `auth.generic_oauth` integration and dedicated Kanidm client; VictoriaLogs VMUI uses the OIDC middleware on its HTTPRoute. Useful LogsQL starting points are `namespace:observability`, `service_name:grafana`, `level:in("error", "critical")`, `stream:stderr`, and `namespace:observability | stats by (service_name) count()`. The time picker supplies the query window. These raw metadata filters select retained Alloy rows; use the dual-schema namespace selector and compatibility pipe described under [Query compatibility](#query-compatibility) to include current vlagent rows.

## UniFi console logs

The UniFi console (`Gateway DMP`, Network 10.6.106) exports CEF and ordinary gateway syslog to the `victoria-logs-syslog` LoadBalancer at `10.32.130.3` UDP/514. **Verified October 18, 2026:** export is enabled and the Service still selects VictoriaLogs v1.53.0 directly; live records include CEF classes 400/401/544/546 and ordinary gateway messages. The dedicated relay below is qualified locally and awaits the separately authorized merge/cutover and live acceptance in [#856](https://github.com/kelchm/home-lab/issues/856).

The proposed path is console → `unifi-syslog` stock vlagent v1.53.0 UDP/5514 → cluster-internal backend `/insert/jsonline`. The same Service and IP remain owned by the VictoriaLogs Flux Kustomization; it depends on the relay being Ready before changing the Service selector. `externalTrafficPolicy: Local` preserves the sender address through Service translation; the relay policy admits external syslog only from the gateway's VLAN 30 interface, `10.32.30.1`, alongside the namespace baseline's in-cluster/node allowances. Only UDP/514 is published; relay HTTP/9429 and backend HTTP/9428 stay cluster-internal. The backend's old UDP listener remains unexposed for rollback.

Every record lands in the default tenant beside Kubernetes logs. `_time` remains arrival time at the receiver; the original syslog header time, interpreted in America/New_York for RFC3164, remains in `timestamp`, and `remote_ip` remains the console sender. JSON re-ingestion explicitly restores the six original stream fields, including `proc_id`; it uses the relay's `_time` rather than the backend arrival time.

The single-replica relay uses a 1 GiB persistent sender queue on a 3 GiB Longhorn RWO PVC, two remote-write workers, a 60-second termination grace period and a Recreate update strategy. Buffering protects received data during a finite backend outage; it cannot recover UDP lost before reception, during relay downtime, or after queue eviction. A successful HTTP response and zero loss counters do not establish complete or exactly-once delivery. A crash can lose data still in memory. The PVC is excluded from Flux pruning so a revert preserves queued data. This changes future UniFi records only: retained history and Traefik access JSON messages are not rewritten.

### Console setup

Export is already enabled to `10.32.130.3` UDP/514. Keep that target during cutover; the agent's console account is View Only. These are the setup/recovery settings, not an outstanding enablement step. Page names vary between Network releases.

1. Open **Settings → CyberSecure → Traffic Logging** and find **Activity Logging (Syslog)**.
2. Select **SIEM Server**. Set **Server Address** to `10.32.130.3` and **Port** to `514`.
3. Enable every content category except **Firewall Default Policy**, which users report multiplies volume about 200 times. Leave **Debug Logs** and **Netconsole** off; they are gateway diagnostics, not events.
4. Apply, then leave the separate **Control Plane → Integrations** syslog export off. Exporting the same categories through both pages duplicates every event.

After naturally occurring console traffic, `_time:15m remote_ip:=10.32.30.1 | stats count()` should be nonzero. A short empty window alone is not a failure; the baseline includes 29 hours between CEF events. If expected traffic remains absent, check `hubble_drop_total{destination_namespace="observability", reason="POLICY_DENIED", protocol="UDP"}` for a rise; it also counts unrelated traffic, so confirm with `hubble observe --verdict DROPPED --since 10m` in the `cilium-agent` container on the node hosting the selected syslog receiver. A dropped UDP packet to that pod's port 5514 shows the gateway's actual source address. Verify the source before changing the CIDR in the [relay policy](../../kubernetes/apps/observability/unifi-syslog/app/networkpolicy.yaml).

### Fields

The relay uses VictoriaLogs' stock syslog/CEF parser. It disables the placeholder with the explicit CLI argument `-defaultMsgValue=` and sends JSONLine with `VL-Msg-Field:cef.extension.msg,_msg,cef.name`. The backend selects the first available candidate and renames it to `_msg`. Native transport ignores this header, so this dedicated receiver uses JSONLine; the Kubernetes collector stays unchanged. [Upstream #1362](https://github.com/VictoriaMetrics/VictoriaLogs/issues/1362#issuecomment-4350319170) describes the duplicate-message problem and workaround; maintainers have discussed a root fix. Latest release v1.53.0 and upstream master `2721f2111df153f58fecdd8955641c35baaa2d10` still use only `message` for syslog as of October 18. Revisit the relay workaround when a released fix supports native transport with these semantics.

| Field | Content |
| --- | --- |
| `hostname`, `app_name`, `proc_id` | Syslog header; stream fields. CEF lines have `app_name=CEF`; firewall rule logs have `app_name=kernel`. |
| `cef.device_vendor`, `cef.device_product`, `cef.device_event_class_id` | CEF header; also stream fields. Product is `UniFi Network` for network events and `UniFi OS` for console events. |
| `cef.name`, `cef.severity`, `cef.device_version` | Event name, 0–10 severity, and application version. Missing-`msg` CEF uses the name as `_msg`, consuming `cef.name` on that path. |
| `cef.extension.<key>` | Every CEF extension, such as `UNIFIcategory`, `msg`, `src`, `dst`, `UNIFIwanName`, `UNIFIipsSignature`, `UNIFIclientAlias` and `UNIFIdeviceName`. Ubiquiti lists the keys in [UniFi System Logs & SIEM Integration](https://help.ui.com/hc/en-us/articles/33349041044119). |
| `timestamp`, `remote_ip`, `format`, `facility_keyword`, `level` | Header time, sender address, `rfc3164` or `rfc5424`, and the syslog facility and severity. |

Retained direct-ingestion CEF rows have their readable text in `cef.extension.msg` and a missing-message placeholder in `_msg`. Future relay rows have the real text immediately in `_msg`; `cef.extension.msg` is consumed by promotion. If `msg` is absent, `cef.name` is promoted and consumed instead. Ordinary syslog keeps its existing `_msg`, including kernel firewall lines with `DESCR="<policy name>"`. Unknown CEF classes keep parsed fields; malformed CEF remains observable as plain message text. Missing-`msg` events retain their other structured fields, but name filters must allow the `_msg` fallback as the alerts do.

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

For a mixed retained/new result, append `| format if (cef.extension.msg:*) "<cef.extension.msg>" as _msg` before selecting display fields. This supported [conditional format](https://docs.victoriametrics.com/victorialogs/logsql/#conditional-format) changes only old rows with an extension message; an unconditional `copy` can erase new `_msg` values, and `copy if` is unsupported in v1.53.0.

- Everything the console sent: `remote_ip:=10.32.30.1`
- Events by class: `{cef.device_product="UniFi Network"} | stats by (cef.device_event_class_id, cef.name) count()`
- WAN events: `{cef.device_product="UniFi Network"} cef.extension.UNIFIcategory:="Internet and WAN" | format if (cef.extension.msg:*) "<cef.extension.msg>" as _msg | fields _time, cef.name, cef.extension.UNIFIwanName, _msg`
- IDS detections by pair: `{cef.device_product="UniFi Network", cef.device_event_class_id=~"200|201"} | stats by (cef.extension.src, cef.extension.dst, cef.extension.UNIFIipsSignature) count()`
- One client's connections: `{cef.device_product="UniFi Network"} cef.extension.UNIFIclientAlias:="Hyperion" | format if (cef.extension.msg:*) "<cef.extension.msg>" as _msg | fields _time, cef.name, _msg`
- Devices going offline: `{cef.device_product="UniFi Network", cef.device_event_class_id="512"}`
- Admin activity: `cef.extension.UNIFIadmin:* OR cef.extension.UNIFIcategory:=Audit OR {cef.device_product="UniFi OS"}` (Network 10.x files admin events under `Audit`; 9.3 used `System` with `UNIFIsubCategory=Admin`)
- Firewall rule hits: `{app_name="kernel"} DESCR`

### Baseline

The console's own system log for the 30 days to October 18, 2026 held 7,439 events, about 250 a day. Client connect, disconnect and roam events made up 89%. The longest gap between two events was 29 hours.

- **IDS:** IPS runs in detect-only (`ids`) mode. All 525 detections had a LAN source, and 523 came from one operator Mac (`Hyperion`, `10.32.10.244`), mostly to the Sparks. Eleven had an external destination, outside multicast: ten from the Mac (nine to CloudFront addresses, one to `192.0.2.10`) and one from `k8s-prod-2`. `UniFiIntrusionDetectedOutbound` would have fired for each of those eleven.
- **WAN:** one T-Mobile outage lasting 3 days 16 hours, three drops of 24 to 90 seconds (two T-Mobile, one Starlink), 26 high-latency and 11 packet-loss events. `UniFiWANFlapping` would not have fired.
- **Other:** 177 admin logins, 16 configuration changes, 27 SFP receive faults, and 5 firewall-policy blocks: `spark-240a`'s storage address to `admin-prod` Traefik, and `k8s-prod-1` to `10.32.21.100`.

### Alerts

`vmalert-victoria-logs` evaluates the LogsQL rules in [unifi-alerts.yaml](../../kubernetes/apps/observability/victoria-logs-single/app/unifi-alerts.yaml) every minute and sends them through VMAlertmanager like any other `evaluator=vmalert` alert ([alerting runbook](alerting.md)). Each expression sets its own `_time` window, so one event keeps its alert firing for that long and then it resolves. The metrics vmalert never loads these rules, and a broken expression raises `AlertingRulesError` from the chart's vmalert rules.

| Alert | Fires when | Severity |
| --- | --- | --- |
| `UniFiWANFlapping` | Three or more Internet Down, temporary-disconnection or failover events for one WAN within an hour. `UniFiWANNotActive` already pages a WAN down for 5 minutes; this catches shorter repeated drops. | warning |
| `UniFiIntrusionDetectedOutbound` | An IDS detection, in the last 15 minutes, whose destination is outside private, CGNAT, link-local and multicast space. Lab-internal detections are queryable but do not page, because the baseline is dominated by operator traffic. | warning |
| `UniFiHoneypotTriggered` | Any honeypot event in the last 15 minutes. | warning |
| `UniFiSyslogSilent` | For an hour, the dedicated relay's `vl_rows_ingested_total{job="unifi-syslog",type="syslog_udp"}` shows no source rows in two days, including never-received or missing series. The two-day window exceeds the 29-hour baseline gap; a new empty receiver can fire after an hour. Ordinary gateway messages also count as source activity. | warning |
| `UniFiSyslogReceiverUnavailable` | No healthy relay scrape target for five minutes, including absent discovery. | critical |
| `UniFiSyslogDeliveryStalled` | Source rows arrive or delivery retries continue in 15 minutes, but there are no successful backend requests, for five minutes. This covers in-flight batches after source traffic stops; quiet periods without retries do not fire. | warning |
| `UniFiSyslogBacklogSustained` | Disk bytes or memory blocks remain queued for ten minutes, including after the source becomes quiet. | warning |
| `UniFiSyslogQueueNearCapacity` | Pending disk bytes exceed 75% of the 1 GiB budget for two minutes. | critical |
| `UniFiSyslogLoss` | Queue eviction, remote-write rejection or receiver row-drop counters increase. These counters use different units, not a lost-event total. | critical |
| `UniFiSyslogReceiveErrors` | UDP receive or syslog parse errors increase. Zero errors cannot prove complete UDP delivery. | warning |

WAN temporary-disconnection filters accept `cef.extension.msg` and `_msg`; failover and honeypot name filters also check `_msg` only when `cef.name` is absent, covering missing-`msg` fallback without matching arbitrary normal messages. Source and delivery rules use the dedicated relay metrics job, independent of backend log queries or unrelated backend traffic. The class catalog comes from Ubiquiti's documentation and published captures; only the classes noted above have been observed in this console's current export. A local VictoriaLogs v1.53.0 and vmalert v1.154.0 fired each rule on matching fixtures and ignored internal and multicast detections. Recheck the selectors against the first live records, especially the temporary-disconnection text, the honeypot event name and whether detections carry `dst`. To silence a benign signature, exclude its `cef.extension.UNIFIipsSignatureId` in the rule rather than adding an Alertmanager silence.

### Relay qualification and transport cutover

[Local qualification](../evaluations/unifi-syslog-relay.md) covers synthetic records and finite backend-outage recovery, not a production rollout. **Merging the relay PR changes the transport through Flux within seconds.** It requires a separately authorized cutover window; none was performed during qualification. No console target change is needed.

1. Before approving merge, inspect the current Service selector/VIP, VictoriaLogs health, Longhorn health/free capacity, observability quota, PodMonitor conversion and the relay's Flux dependency. Confirm the rollback below is available and accept a possible UDP gap during endpoint/BGP convergence. Do not induce a production outage or generate synthetic alerts.
2. On authorized merge, Flux first reconciles `unifi-syslog` and waits for its Deployment/PVC to be Ready. The relay has no backend dependency and can queue while the backend is unavailable. Only then can `victoria-logs-single` update the existing Service selector. The Service stays in its original Kustomization inventory: do not move/delete/recreate it or allocate a second Service with the same VIP.
3. Check `flux get kustomizations -n flux-system`, `kubectl -n observability rollout status deployment/unifi-syslog`, the Service's unchanged `10.32.130.3` UDP/514/Local configuration, and `kubectl -n observability get endpointslice -l kubernetes.io/service-name=victoria-logs-syslog -o yaml`. Endpoint targetRef/address must resolve to the relay, and the relay's node must advertise the VIP. Recheck external source enforcement with Cilium policy/flow observations; HTTP ports must remain unpublished. If the relay cannot become Ready, the old endpoint stays selected; repair scheduling/PVC before attempting a cutover.
4. Observe naturally occurring console traffic. Confirm a healthy `up{job="unifi-syslog"}` scrape, increasing relay `vl_rows_ingested_total{type="syslog_udp"}` and `vlagent_remotewrite_requests_total{status_code="2XX"}`, a draining disk/memory queue and unchanged drop counters. Check receiver logs and PVC free space. Backend syslog counters will stop increasing and are no longer the source-health signal.
5. Query `_time:15m remote_ip:=10.32.30.1 | fields _time, timestamp, remote_ip, _stream, _msg, cef.name, cef.device_event_class_id` directly and in Grafana/VMUI. Check real CEF text immediately, ordinary gateway/firewall text, retained sender timestamps, all six applicable stream fields and native event IDs. Test the mixed-history examples above. Compare live WAN/IDS/honeypot rule results with the underlying events; observe missing-message fallback if it occurs naturally. Absence of a rare event class is an outstanding acceptance item, not proof of failure or permission to inject one.
6. Record live evidence and any unobserved event classes in #856, including metrics alert discovery/evaluation and operator-approved delivery evidence when available. Leave it open while live acceptance or previously required alert delivery is unperformed. No test notifications are part of this procedure.

### Relay rollback

Restore direct syslog with a focused rollback commit: restore the Service selector (`app=server`, `app.kubernetes.io/instance=victoria-logs-single`, `app.kubernetes.io/name=victoria-logs-single`) and the backend policy's `10.32.30.1/32` UDP/5514 ingress rule from the pre-relay revision. **In that same commit remove `dependsOn: unifi-syslog` from `victoria-logs-single/ks.yaml`.** The dependency gates the entire backend Kustomization; an unhealthy relay would otherwise block applying the rollback and backend repairs. The parent `cluster-apps` has `wait: false` and can apply that child-spec change independently of the relay's health. After authorizing/merging the rollback, reconcile/check `cluster-apps` first and confirm the backend dependency is removed, then reconcile/check `victoria-logs-single` and its restored Service endpoint. Do not bypass policy or change the console target.

The old backend UDP listener/timezone/local-timestamp/remote-IP flags are retained. Keep the relay Deployment, PVC and metrics while any queued records drain into the backend; changing the Service alone does not discard already received data. Verify real traffic and the direct-ingestion counter. Direct CEF resumes the old placeholder behavior; mixed-history queries/alerts remain compatible. Expect possible UDP loss during the switch and possible retry duplicates; compare event identifiers where available.

After both relay disk bytes and in-memory blocks stay at zero and writes are successful, remove the relay resources and the `default-deny` dependency in a second reviewed change. A full PR revert before draining can strand data: the PVC survives Flux pruning, but replay requires recreating the same relay with the same mount, queue path and remote-write URL (the URL contributes to the queue identity). Preserve that PVC and old image/args until delivery is reconciled; do not delete it as rollback cleanup. The finite graceful-restart test does not qualify abrupt power loss, Longhorn failover or saturated queues.

## Alloy field model (retained history)

Retained Alloy rows use `cluster`, `namespace`, `service_name`, `pod`, `container`, and `node` as their stream identity. Alloy derives `service_name` from `app.kubernetes.io/name`, then legacy `app`, then the container name. `app_instance` preserves `app.kubernetes.io/instance` when present, but remains an ordinary field because it normally identifies an application or Helm release rather than a unique OpenTelemetry service instance.

Alloy normalizes explicit JSON or credible logfmt severity aliases to the Grafana-native top-level `level` values `trace`, `debug`, `info`, `warning`, `error`, and `critical`; fatal and panic aliases fold into `critical` while the original payload remains intact. `level` is an ordinary VictoriaLogs field rather than part of stream identity. Grafana also has bounded compatibility rules for retained rows whose pre-normalization severity exists only in `msg.level`; no general message-text inference is used.

Structured application fields are stored below `msg.*`, preventing payload keys such as `namespace` from replacing Kubernetes metadata. VictoriaLogs derives `_msg` from `message`, `msg`, `log`, `event`, or `record.message` after applying that prefix; non-JSON lines remain unchanged in `_msg`. Alloy strips ANSI terminal color escapes after reconstructing CRI partial records so stored messages remain readable and searchable.

Use ordinary field filters for `app_instance`, `level`, `filename`, and `stream`. For example, `stream:stderr` works across both pre-hardening and later Alloy data, while a stream selector such as `{stream="stderr"}` matches only older rows where `stream` was part of `_stream`.

Grafana and all three Traefik instances emit their supported JSON console/access-log formats. Other applications keep their native output until a measured source-specific change is justified. Do not add a global multiline rule: CRI partial records are already reconstructed, and legitimate separator output can resemble continuation lines. Likewise, do not infer stored severity from arbitrary words such as `error` or `info`; an honest unknown level is safer than a false classification.

Retention is nominally 30 days, but VictoriaLogs begins deleting the oldest partitions at 80% disk use. The effective retention is whichever limit is reached first. Replayed records are stored as additional rows until retention removes them; the dashboard does not deduplicate them, and ordinary counts include them.

## Source inventory

Measured 2026-09-18 over a 24-hour window with `stats by (service_name) count()` and its `NOT level:~".+"` variant. "No recognized severity" means the stored top-level `level` is absent or empty; it does not establish that a row is unstructured. This is a dated inventory of the deployed Alloy pipeline, not a complete list of sources or a current traffic baseline. The later [collector evaluation](../evaluations/kubernetes-log-collectors.md#stdoutstderr-and-severity) records additional source-level findings, including Broadsheet renderer warnings whose explicit prefix is not normalized by Alloy.

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

Traefik access-log JSON has queryable request/status fields but lacks a message field selected by the ingestion configuration, so `_msg` displays a missing-message placeholder. Use Grafana's `Show log details` to inspect these rows. The accepted workflow uses these structured fields directly; revisit message presentation if it obstructs routine investigation. Do not infer severity from arbitrary access-log text. Native source-format changes, noisy-log reduction and secret/PII redaction remain separate from the collector migration.

## Pipeline failures

Metrics-native rules page through VMAlertmanager when a selected vlagent target disappears, source ingestion stops, queue backlog persists, the queue approaches capacity, either queue eviction or remote-write loss occurs, or VictoriaLogs reports ingestion/storage failures. These alerts depend on the metrics path rather than querying the log backend they diagnose. The rule definitions and evaluation are verified. On September 26, a temporary `VlagentRemoteWriteLoss` rule with `test=synthetic` exercised the production evaluator and critical Pushover route without inducing data loss; receiver-delivery evidence is recorded in #583. Follow the [alert-delivery runbook](alerting.md#end-to-end-delivery-test) when retesting.

Inspect the affected vlagent pod's source counters, pending queue bytes, in-memory blocks, loss counters and backend errors, the `victoria-logs-single-server-0` workload/PVC, and vmagent scrape targets before restarting anything. Preserve `/var/lib/vlagent`; it contains both checkpoints and the persistent sender queue. Restore backend health and wait for pending data and in-memory blocks to drain before planned collector changes.

The [logging alert rules](../../kubernetes/apps/observability/victoria-metrics-k8s-stack/app/platform-alerts.yaml) provide partial detection. `VlagentNodeLogIngestionSilent` evaluates a ten-minute source-input window and waits ten minutes before firing. It cannot detect partial loss while other records arrive. Queue-eviction and remote-write-drop counters cover specific loss paths; zero counters and quiet alerts do not establish complete delivery or absence of replay duplicates.

Alloy's deployment and four transitional alerts have been removed. The [collector evaluation](../evaluations/kubernetes-log-collectors.md) preserves its measured delivery limits; retained Alloy log records and compatibility queries remain available through backend retention.

## vlagent implementation

The DaemonSet pins the unmodified upstream image `victoriametrics/vlagent:heads-master-0-gff8635e43@sha256:7492cfbb1468432ca9db753bf9c3e4478b298f72945adaa902254627e9e3dba9`, built from `ff8635e43c1dce4fe1d8b4dc5c57bdf3b62db248`. It includes `output_stream` (upstream #1791), which the tested v1.52.0 release lacks. There is no downstream patch or custom image build. Requalify image upgrades against the delivery and compatibility fixtures.

Stock vlagent flattens application JSON into top-level fields. Collector metadata remains under `kubernetes.*`, with static `cluster=k8s-prod`. An application emitting the same `kubernetes.*` stream keys can cause backend rejection; see the [diagnostic reproduction and production audit](../evaluations/kubernetes-log-collectors.md#metadata-collision-supported-diagnostic-output-2026-09-23). This is a documented upstream limitation, not a prerequisite to fork or switch collectors. Avoid treating arbitrary payload fields or labels as a security boundary. The rejection path currently logs a warning without a dedicated drop counter; inspect backend warnings when diagnosing discrepancies. Upstream [#1603](https://github.com/VictoriaMetrics/VictoriaLogs/issues/1603) and [#1286](https://github.com/VictoriaMetrics/VictoriaLogs/issues/1286) track duplicate-field handling; open [#1664](https://github.com/VictoriaMetrics/VictoriaLogs/pull/1664) is not a deployed fix.

vlagent reads the kubelet-managed `/var/log/containers` symlinks and their `/var/log/pods` targets, both read-only. Discovery is scoped to the current node and uses exact container IDs; this covers static mirror pods without reconstructing paths from their API UIDs. The root owner-read exception drops all capabilities, uses RuntimeDefault seccomp and an immutable root filesystem, and exposes neither host PID nor host networking. RBAC permits pod get/list/watch, node get and namespace list. The observability namespace baseline permits DNS, API access and intra-namespace traffic; the workload policy records metrics ingress and backend egress. Cilium policies are additive, so this does not restrict the broader namespace baseline.

Checkpoints and the remote-write queue persist together under the node-local `/var/lib/vlagent` hostPath. Each node has a 4 GiB compressed queue budget. This is a payload limit, not a filesystem quota: leave at least another 1 GiB for segment allocation, checkpoints and operational headroom, in addition to the node's normal free-space reserve. A node replacement or disk loss loses this state; a PVC shared between nodes would not make their checkpoints interchangeable. Do not delete, move or concurrently open an active queue.

### Query compatibility

New stream identity is `cluster`, `kubernetes.pod_namespace`, `kubernetes.pod_name`, `kubernetes.container_name`, and `kubernetes.pod_node_name`. Service labels remain ordinary metadata: grouping at query time does not add a service facet to `_stream`. `output_stream`, application severity and request IDs are not stream fields. New application fields use their upstream top-level names; retained Alloy fields remain under `msg.*`. `_msg` uses the same `message,msg,log,event,record.message` precedence as Alloy. ANSI cleanup applies to `_msg`. CRI partial stdout/stderr records are reconstructed independently; unrelated multiline application events remain separate. Stock timestamp parsing is retained: recognized JSON time fields and klog timestamps supply `_time`; CRI time is the fallback when no valid parsed timestamp exists. Application clock skew can therefore affect the query window. The delivery fixture uses current timestamps and checks them against a separate `emitted_at` value.

The provisioned **Kubernetes / Logs** Grafana dashboard uses the [shared compatibility pipe](../../scripts/logging/compat.logsql). It aliases Kubernetes namespace/pod/container/node, derives service from the recommended app label, then legacy app label, then container, and aliases `output_stream` to `stream`. Its searchable namespace/service variables use the same metadata aliases and selected time range, with up to 1,000 suggestions; the service query is scoped by namespace. Dropdown selections use quoted exact values rather than user-entered regexes. It supports retained Alloy rows with the same query. Explicit JSON/klog severity, credible logfmt with a message/time companion, and the measured Broadsheet, Bambuddy and MCPHub headers normalize to `trace`, `debug`, `info`, `warning`, `error` or `critical`. Unknown severity and continuation lines stay unknown; the dashboard names the empty canonical level `unknown` so it can be selected explicitly. stdout/stderr never implies severity. App source-format changes remain with #485 / #556.

For Explore or VMUI, begin with a narrow time and authoritative namespace filter, append the shared pipe, then filter or group the resulting aliases. Examples of final pipes are `| filter service_name:=bambuddy stream:=stderr`, `| filter level:in(error,critical)` and `| stats by (namespace,service_name) count()`. Before the compatibility pipe, select both schemas with `(kubernetes.pod_namespace:=printing OR namespace:=printing)`. A raw `namespace:=printing` query alone only selects Alloy history. The Grafana datasource rules color both new `level` and retained `msg.level` values; they cannot parse logfmt by themselves. Use the dashboard's severity field, or append `| filter level:=warning` after the compatibility pipe. Explore's quick severity buttons filter stored fields before that pipe and can silently omit logfmt/source-prefix matches: a browser check reduced two TRACE fixtures to only the retained JSON row. Clear those buttons when using compatibility queries. The 1,000-line default remains configured; the absolute-time VMUI handoff limitation above also applies to these queries.

Pod labels are cached while a file is tailed. Prefer replacing a pod when changing its logging identity; otherwise drain the collector queue and restart it to refresh metadata. New rows do not contain Alloy's `filename`; use authoritative pod/container/node fields to locate the source. Retained historical filename queries still work. Fields parsed by klog other than `_msg` are top-level fields in new rows.

### Buffer capacity and monitoring

Size from per-node compressed input, including metadata, using `uv run scripts/logging/measure-input.py --kubeconfig <path> --namespace observability --selector app.kubernetes.io/name=vlagent --seconds 300`. It uses a Prometheus parser so spaces inside label sets cannot turn nonzero samples into zero. Discard intervals with restarted collectors, initial history replay, active flood fixtures or counter resets. The seven-day production baseline measured 1.07–1.40 million rows and 180–212 MB of message text per node; message text excludes metadata and is not the compressed queue rate. A 120-second sample on September 23 measured 316, 356 and 1,094 compressed bytes/second across the three nodes, including the countable drill on node 1. At four times that maximum rate, a 24-hour outage needs about 378 MB; 4 GiB supplies over eleven times that incident budget. This deliberately includes synthetic overhead, but does not cover unbounded storms. Recheck representative working-day and incident rates after cutover; do not interpret a short sample as a days-long guarantee.

The PodMonitor produces `job=vlagent` with a `node` label. Alerts detect missing selected-node collectors, unread source silence, sustained backlog, 75% queue occupancy, persistent-queue evictions and rejected remote-write packets. Dropped-block and dropped-byte counters represent compressed queue loss, not a row count, and fire without a `for` delay. Backend ingestion requires production `job=victoria-logs-single-server` with `type="nativeinsert"`; Loki traffic and collector/scratch ingestion cannot satisfy it. Existing backend rejection, PVC, read-only and stream-churn alerts remain.

The 1,500 new-streams/hour envelope is retained deliberately: the new schema contains one identity per pod/container/node, with no per-event values or extra stdout/stderr split. Initial adoption adds roughly one stream per existing container, on top of the measured prior peak of 819/hour. Revisit this threshold after a representative week of production traffic without the scratch generators.

### Implementation validation (September 23, 2026)

The final upstream configuration was tested in scratch with its own `/var/lib/vlagent-583-stock` state. Results from the earlier rejected downstream-patched candidate are historical and do not qualify this image. Production still used Alloy at the time of these scratch tests. After the tests, all three candidate queues drained, temporary fixture pods and candidate RBAC/DaemonSet were removed, and the original scratch vlagent DaemonSet returned to 3/3 Ready. Their sanitized results are retained here and in #583; candidate hostPath contents were removed during retirement.

| Check | Final stock-image result |
| --- | --- |
| Query compatibility | 64/64 fixtures passed for upstream fields, retained Alloy fields, source formats and alias precedence. |
| Backend outage without collector restart | 1,200/1,200 unique records recovered; zero missing or duplicate records. Alloy reference also stored exactly 1,200. |
| Backend outage with confirmed SIGKILL | Exact scratch container terminated with exit 137 at `2026-09-23T23:40:38Z`. All 2,400 distinct records recovered, zero missing; 2,929 total rows included 529 duplicates. Alloy reference stored exactly 2,400. |
| Manifest and alert validation | Recursive Flux render passed; 103 schema-validated resources, zero invalid; all 24 alert rules and behavior fixtures passed. Production backend v1.52.0 and its `job=victoria-logs-single-server` scrape label were verified; receiver delivery was verified separately during production retirement. |

Use `python3 scripts/logging/validate-compat.py --url http://127.0.0.1:<scratch-port>` for new upstream fields and retained Alloy query fixtures in tenant `583:0`. The [numbered fixture](../../scripts/logging/numbered-fixture.py) emits ordinary JSON with sequential IDs, known stderr parity, severity, current timestamps and 40 KB records. It deliberately does not inject metadata collisions into delivery tests. Verify with `python3 scripts/logging/verify-numbered.py --url <scratch-url> --run <FIXTURE_RUN> --expected <FIXTURE_COUNT> --start <RFC3339> --end <RFC3339>`; use `--schema alloy` for the reference path. A nonzero exit reports missing, unexpected or duplicate records. The time bounds must cover generation and recovery. Do not suppress failures or interpret an empty queue as complete delivery.

Fault injection is confined to the scratch backend and exact scratch collector container IDs. Stop only `deployment/vl-vlagent` in `log-drill`, start the finite fixture, optionally terminate the selected scratch collector process, restore the backend, verify restart state and wait for its queue to drain before comparing IDs. Save the original scratch deployment and collector specs first, and restore the backend even if the test fails. Never reboot a node or interrupt the production backend for this collector test.

The SIGKILL result is one observed run, not an exactly-once guarantee or a worst-case bound. The queue drained to zero and eviction counters stayed zero; these counters do not account for replay duplicates. A first attempted kill lacked timely termination confirmation and was excluded from the qualification counts. The successful run was independently checked against the exact container ID and restart count. The earlier downstream-patched experiment lost 42 of 2,400 records; it does not establish a stock-image loss rate or mechanism.

The September 26 production rollout used the accepted measured recovery limits and brief per-node delivery checks. Alert receiver verification and scratch retirement are recorded in #583. Node/disk failure is not tested. Persistent queue eviction counters measure overflow and cannot prove absence of other loss paths.

### Production cutover (September 26, 2026)

The three nodes were switched sequentially after explicit approval. vlagent reached 3/3 Ready and Alloy reached zero desired/running pods. A fresh 100-record fixture on each node delivered all 300 records with zero missing, unexpected or duplicate records; the checks covered stdout/stderr parity, severity, timestamps and 40 KB CRI reconstruction. Fresh application logs and kube-apiserver logs arrived from the selected nodes, all three queues drained, and queue-eviction/remote-write-drop counters stayed zero. The production dashboard displayed new logs through the existing filters. Initial history replay was visible as duplicate historical rows; the clean fresh fixtures do not establish exactly-once recovery.

The backend was OOM-killed once at 18:51 UTC while history replay was in progress under its former 1 GiB limit. vlagent retried the interrupted writes and its queues drained. The backend memory request is now 2 GiB and its limit is 4 GiB, using the nodes' available memory headroom; monitor actual use and restarts. A subsequent source audit found 46 of 898 available production records from 18:50–18:52 UTC absent from both the Alloy and vlagent data. The missing records were emitted at 18:50:49–18:51:01 UTC. This is a confirmed cutover gap, not a count of all possible loss during the restart. The user accepted this gap without backfill on September 26; details remain in #583. The successful numbered fixtures did not exercise those application records.

The full cutover and retirement were accepted on September 26. The temporary comparison stack, Grafana preview and candidate buffers are retired; Alloy checkpoints alone remain for the short rollback precaution below.

## Collector maintenance and rollback

Before a planned collector restart or replacement, restore backend health and verify each vlagent queue's pending bytes and in-memory blocks reach zero. Preserve `/var/lib/vlagent`, which contains the read checkpoints and persistent sender queue. The DaemonSet rolls one node at a time; verify readiness, source input and fresh stored records on that node before continuing. Never run two vlagent processes against the same hostPath.

Alloy is no longer deployed. Removing a node's collector label now stops logging on that node; it does not automatically restore Alloy. If rollback is necessary, restore the Alloy manifests, Flux reference and complete `logging-pipeline` rule group from commit `cd3654ec7f031234de1a21b9485cdd50a7b52b85` in a reviewed change. Keep the node labels until Flux has reconciled Alloy with zero desired pods. Then drain vlagent and remove the label from one node at a time, verifying Alloy resumes and fresh logs arrive. Alloy may replay records collected since its previous stop, and its saved positions cannot recover files that have already rotated away. Its writer WAL was disabled, so unsent sender data remains memory-only. If the backend is unavailable, preserve the queues and decide recovery before changing collectors.

Retain production `/var/lib/alloy` through October 3, 2026. At that point, if no rollback is in progress and production ingestion remains healthy, remove its contents using exact-path cleanup mounts and record the result in #583. Git history retains the old configuration. Production `/var/lib/vlagent` must remain.

### Backend crash durability

The deployed VictoriaLogs v1.52.0 binary confirms the upstream [in-memory flush setting](https://docs.victoriametrics.com/victorialogs/#list-of-command-line-flags): `-inmemoryDataFlushInterval` defaults to five seconds, has a one-second minimum, and trades more frequent durable writes for additional disk I/O. Accepted data can remain in memory until that flush; an unclean shutdown can lose it without collector drop counters increasing. The interval concerns backend persistence, not application event timestamps, so old/replayed records can also be affected.

Keep the five-second default for this CPU-constrained homelab and the 4 GiB backend memory limit. Reducing the interval would narrow the persistence window but would not provide synchronous acknowledgement or exactly-once recovery. Revisit if backend crashes recur or the loss tolerance changes. The observed cutover gap is consistent with unflushed data lost at the OOM, but the exact fate of every missing record was not traced through request acknowledgements.

### Scratch retirement

The September 26 cleanup stopped the generators, kept all four scratch backends healthy while collectors drained, and recorded graceful collector shutdowns before removing the inventoried `log-drill` workloads, PVCs, temporary Grafana preview and cluster RBAC. The four PVCs held 20 GiB of scratch capacity; retained production logs were untouched.

Exact node-local paths were checked against the running or preserved candidate manifests: `/var/lib/alloy-drill`, `/var/lib/alloy-wal-drill`, `/var/lib/vector-drill`, `/var/lib/vlagent-drill`, `/var/lib/vlagent-583` and `/var/lib/vlagent-583-stock`. Cleanup mounts only these directories, removes their contents after collector shutdown, and verifies that they are empty on every node. Empty mount-root directories may remain; do not mount `/var/lib` wholesale merely to remove them. Temporary cleanup pods are removed after verification. Production `/var/lib/alloy` has the separate retention date above; production `/var/lib/vlagent` is never a scratch cleanup target.
