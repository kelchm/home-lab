# Estate observability — converge the stack, cover the hardware, and make incidents navigable

**Status:** Active — 2026-09-02; the VictoriaMetrics/VictoriaLogs survivor path is live and verified, OpenObserve is retired, and KPS plus Loki remain only for the post-cutover correctness soak and cleanup tracked in #470. This revision carries that deployed baseline into an estate-wide metrics, logs, flow, and security-observability design. Background: [observability bake-off](../observability-bakeoff.md).

## Context and origin

This is the execution plan that concludes the [observability bake-off](../observability-bakeoff.md) and turns the result into a low-maintenance, single-pane setup covering the whole estate. It began with Kubernetes, Synology, UniFi, and the `rpi-nixos` edge fleet; the 2026-08-27 revision adds the three-node Proxmox cluster, both DGX Sparks, inference telemetry, centralized external logs, routed-flow analysis, and a bounded SIEM capability.

The original question was “does Netdata fit here?” It widened into “what gets Netdata-class usefulness—automatic dashboards and anomaly detection—without the dashboard-authoring tax, while remaining self-hosted?” The bake-off answered the backend question, but its evidence and the reasons behind the resulting choices remain part of this plan rather than disposable history.

### Verified bake-off baseline

The following baseline was verified live on 2026-07-01/02 through Grafana, Kubernetes, and Flux read-only inspection:

- Five observability backends ran in parallel in `observability`: kube-prometheus-stack (Prometheus, Alertmanager, kube-state-metrics, and node-exporter), VictoriaMetrics k8s-stack (vmagent, vmsingle, and vmalert), Loki, VictoriaLogs, and OpenObserve. OpenObserve was already out of the bake-off, consumed about 246 MiB, and had no downstream dependency. Alloy supplied the shared log stream.
- The namespace consumed about 5.5 GiB of RAM: vmsingle 1.4 GiB, Prometheus 1.1 GiB, Loki about 0.5 GiB, Grafana about 0.5 GiB, three Alloy pods about 0.8 GiB, VictoriaLogs 0.26 GiB, OpenObserve 0.25 GiB, plus operators and exporters.
- Grafana contained 51 dashboards and none was hand-authored. They came from Kubernetes, node-exporter, Loki, VictoriaMetrics, Cilium, CoreDNS, Kaniop, Prometheus, and Alertmanager mixins or charts. Longhorn, Traefik, cert-manager, Flux, and Alloy were the confirmed gaps.
- There were 296 alerting rules across 66 VMRule groups and none was hand-authored. They consisted of KPS mixin rules and VictoriaMetrics-stack defaults rendered as VMRules. There were no Grafana-managed rules and no anomaly or ML rules.
- Alert delivery was effectively absent. Grafana’s notification policy pointed at an empty `grafana-default-email` receiver, while vmalert sent to the KPS Alertmanager with no external receiver. Rules evaluated, but nobody was notified.
- Cilium supplied the eBPF datapath with agent and operator metrics, but `hubble.enabled: false`; the observability half of the installed datapath remained disabled.

This baseline is historical evidence rather than the implementation starting point. PRs #478, #480, and #481 retired OpenObserve, established independent CRD ownership, moved native Kubernetes scraping and rule ownership to VictoriaMetrics, made VMAlertmanager the Pushover delivery authority, and made VictoriaMetrics Grafana's default datasource; PR #482 records the successful live gates. The current rollout state and remaining cleanup are recorded below.

### The reframe

The stated pain was “I do not have time to hand-build Grafana.” The live inventory showed the opposite problem: the estate already had 51 generated dashboards and 296 generated rules. After the survivor cutover, the priorities are:

1. Production alert delivery now works through VMAlertmanager and Pushover, but the stale-backup delivery exercise and an independent Watchdog/dead-man path are still open under #218.
2. The duplicate KPS and Loki paths remain only as rollback components. Their cleanup follows the post-cutover and corrected-log-path soak under #470.
3. The dashboard gap is bounded, but imported artifacts need curation, stable datasource ownership, and useful navigation.
4. No anomaly layer exists, but deterministic alerting, data quality, and delivery must work before adding one.
5. Most of the physical estate was uncovered; adding PVE and the Sparks makes that gap larger and more important.
6. Logs, flows, and security events need an explicit role and retention boundary rather than accumulating as an unstructured second project.

## Outcome

Run one central observability control plane in `k8s-prod`, keep monitored systems extremely light, and make every alert lead from estate health to a platform or service view and then to relevant logs or flows.

The target is not a wall of imported dashboards. It is a coherent operating model:

1. Metrics identify what is unhealthy and provide capacity and performance history.
2. Logs explain state transitions, failures, and operator or security activity.
3. Network flows answer who communicated with whom across routed boundaries.
4. Grafana is the single entry point; VictoriaMetrics and VictoriaLogs are the durable stores.
5. Alerts are actionable, delivered, and tied to runbooks or a useful drill-down path.

## Scope

This plan covers:

- `k8s-prod`, including Kubernetes control-plane, node, workload, storage, ingress, certificate, GitOps, and Cilium signals.
- The three-node `pve-sbx` Proxmox cluster, its hosts, guests, cluster state, storage, backup jobs, and Corosync health.
- `spark-1` and `spark-2`, including GB10 host and GPU health, inference-server behavior, storage-network use, and the private RDMA fabric.
- The Synology NAS, including chassis, disks, volumes, interfaces, NFS/SMB outcomes, and backup-task outcomes where the available DSM interfaces expose them reliably.
- The UniFi estate: gateway, dual WAN, switches, APs, PoE, Wi-Fi, clients at safe cardinality, activity/security events, sampled routed flows, and limited Protect health.
- The `rpi-nixos` edge fleet and explicit service/outcome probes.
- Central operational logging and a deliberately bounded SIEM-lite capability.

Video, packet capture, full endpoint detection and response, and indefinite flow retention are not in scope.

## Decision history and current decisions

### Backend decision from the bake-off

- VictoriaMetrics won the metrics bake-off because vmagent’s buffering behavior during storage-maintenance windows was operationally preferable to the gaps observed with the monolithic Prometheus path. The current vmagent queue is still ephemeral and must be made an explicit RPO decision; choosing VictoriaMetrics did not make that failure mode disappear.
- VictoriaLogs won the log bake-off because it survived storage maintenance intact while the Loki chart’s scale-to-zero/PVC-retention behavior produced destructive data loss. This is the evidence behind retiring Loki, not merely a product preference.
- Grafana remains the single pane, with Kanidm OIDC and the admin Traefik gateway already in place.
- Netdata Cloud remains rejected. The self-hosted options and residual anomaly decision are retained below.
- Edge Pis use NixOS `node_exporter`, not streaming Netdata children, to avoid unnecessary SD-card and operational load.

### Decisions changed by this revision

- The original plan proposed Grafana Operator for dashboard imports. That is superseded: the deployed Grafana sidecar already supports labelled ConfigMaps, `grafana_folder`, and folders derived from file structure. Repository-owned, pinned dashboard JSON is the lower-toil path.
- The original convergence phase was described as one body of work. It is now split into independently merged checkpoints because Flux reconciliation does not execute numbered prose sequentially and multiple Kustomizations depend on the backends being retired.
- Original sub-day effort estimates are superseded. The work now spans independent convergence, external-metrics, external-logs, and flow/security changes with manual host and controller gates.
- The survivor cutover is no longer proposed work: PRs #478, #480, and #481 completed the CRD, native scrape, rule, alert-delivery, and Grafana ownership transfers, and PR #482 recorded their successful live gates. The remaining convergence work is the soak, KPS/Loki cleanup, estate-label migration before external targets, and independent dead-man coverage.

## Current state

### Central stack

- VictoriaMetrics k8s-stack owns native API-server, kubelet, CoreDNS, kube-state-metrics, node-exporter, controller-manager, scheduler, and etcd scraping. vmagent uses a 30-second default scrape interval, VMSingle retains metrics for 30 days, and both vmagent and metric vmalert still apply `externalLabels.cluster=k8s-prod` pending the estate-label migration.
- VictoriaLogs is already deployed with nominal 30-day retention, a 30 Gi volume, and an 80% disk-retention ceiling, so effective retention is whichever bound is reached first. Its live Cilium policy default-denies ingress and grants workload-specific HTTP paths plus a broad L4 `host`-entity exception for health checks and port-forward handoffs.
- Alloy runs as a hardened DaemonSet, tails Kubernetes CRI logs, labels them with cluster, namespace, pod, container, and node, and temporarily dual-writes to Loki and VictoriaLogs. PR #483 corrected path correlation to exact pod UIDs and Talos mirror-pod config hashes after the soak found 461 incorrect label/file pairs among 742 active pairs; pre-fix log-volume and label-quality measurements are invalid, and contaminated retained history must age out naturally. PR #484 then bounded position-corruption recovery and VictoriaLogs retries at `max_backoff_retries=15`, added collision-safe `msg.*` payload fields and fixed stream identity, hardened the pod, installed Alloy and VictoriaLogs ingress policies, and added the metrics-native `logging-pipeline` alerts. Those changes restart the correctness, field-contract, and reliability observation window from #484.
- Grafana provisions dashboards from labelled ConfigMaps, uses VictoriaMetrics as the default metrics datasource, and points its Alertmanager datasource at VMAlertmanager. Its sidecar supports the `grafana_folder` annotation and `foldersFromFilesStructure`; a Grafana operator is not required for repository-owned dashboards.
- Standalone `prometheus-operator-crds` owns the shared CRDs. OpenObserve is retired. KPS owns no production exporter/control-plane scrape objects, rules, notification route, default datasource, CRDs, or downstream Flux dependencies; its Prometheus and explicitly named non-default datasource remain only for Git-revert rollback. Loki remains only as the temporary comparison sink during the corrected-log-path soak.
- Metric vmalert evaluates the production rules and sends to persistent VMAlertmanager. A live synthetic warning produced exactly one Pushover notification and one resolution with zero delivery failures. The stale-backup delivery exercise and external Watchdog/dead-man path remain unverified.

### Live UniFi inventory

Read-only inspection on 2026-08-27 established the following current inventory:

- Gateway: `Gateway DMP`, UDM Pro, `10.32.1.1`, UniFi OS 5.1.31, Network 10.5.67.
- WAN: T-Mobile on WAN1 and Starlink on WAN2.
- Switches: `Core Aggregation` (USW Pro Aggregation, `10.32.1.10`), `Core Switch` (USW Enterprise 24 PoE, `10.32.1.11`), `Lab Switch` (USW Enterprise 24 PoE, `10.32.1.12`), `Garage Switch` (USW Flex, `10.32.1.21`), a USW Flex 2.5G 8 PoE at `10.32.1.146`, and a USW Flex 2.5G 5 at `10.32.1.209` that was offline during inspection.
- APs: `Basement AP` (U7 Pro XG, `10.32.1.30`), `Sunroom AP` (`10.32.1.31`), `Office AP` (`10.32.1.32`), and `Garage AP` (`10.32.1.33`); the remaining model inventory is two U6 Mesh and one U6 Pro.
- Protect devices: two cameras, currently visible as `Garage - Front Right` and `Entryway`.
- Network shape: 11 networks/VLANs, four WLANs, six named port profiles, and a dedicated `spark-trunk` profile. The active networks include Default, Cameras, Main, Infra Mgmt, Workloads, Storage, K8s Prod, K8s Sandbox, legacy Services, IoT, and Guest.
- Live dashboard count at inspection: one gateway, six switches, four APs, two Protect devices, and 81 clients.

The offline Flex must be classified in inventory as `expected_offline`, `spare`, `retired`, or `active`. Monitoring must use desired state rather than alerting forever on every controller-known device.

## Current architecture decisions

### Central backends

- VictoriaMetrics is the metric system of record.
- VictoriaLogs is the operational log and security-event system of record.
- Grafana remains the single pane, using the existing ConfigMap sidecar for dashboards-as-code.
- Alloy remains the Kubernetes log collector. External systems use built-in or explicitly installed syslog forwarding and send to a centrally exposed VictoriaLogs syslog ingress; another general agent fleet is not introduced.
- VMAlertmanager handles notification routing with a Longhorn-backed 1 GiB volume, a stable Grafana datasource and OIDC-protected route, and SOPS-managed Pushover delivery. The independent Watchdog receiver remains a gate before the observability design can claim protection from central-cluster failure.
- The current metric vmalert owns production metric rules, stamps `evaluator=vmalert`, and notifies the VMAlertmanager route that authorizes that evaluator label. When LogsQL rules are introduced, use a separate VMAlert instance that also stamps `evaluator=vmalert` as the notification-authority identity while `signal=logs` distinguishes its output. LogsQL sources carry `observability.kelch.io/rule-datasource=vlogs`; the vlogs evaluator positively selects that label, while the metric evaluator selects every rule except `vlogs`, preserving a safe default for future unlabeled metric rules. The selector label may live on a native VMRule, chart default-rule metadata, or PrometheusRule metadata preserved through vm-operator conversion; no mandatory resource rewrite is implied. Vlogs groups use `type: vlogs`, query VictoriaLogs, persist alert state through VictoriaMetrics remote-read/write, and notify VMAlertmanager. A standing CI/rendered-config coverage check detects rules selected by zero or multiple evaluators, and the Alertmanager render check proves both evaluator instances reach a non-null route.
- Native platform alerts are the preferred independent path for failures that could take the central stack with them, but they are not assumed healthy merely because the platform supports them. DSM disk/storage alerts, UniFi console notifications, and Proxmox cluster notifications each require a configured destination plus a synthetic delivery test; PVE currently has no authoritative notification path because direct mail failed with Gmail `550 5.7.1` and authenticated SMTP remains open work.
- The planned independent failure path uses a healthchecks-style endpoint outside the estate for the central VMAlertmanager Watchdog plus a second small Gatus instance on one edge Pi. The Pi sends its own heartbeat and direct alerts through a route that does not depend on `k8s-prod` and probes a minimal set of observability, gateway, WAN, and critical-service outcomes without becoming another storage backend.

### Collection posture

- Pull slow-changing infrastructure metrics centrally at 30-second intervals by default.
- Scrape Spark GPU and inference-service metrics at 5 seconds where the added resolution changes diagnosis.
- Prefer a platform API or built-in SNMP/syslog export over installing a general telemetry agent.
- Use one narrowly scoped exporter on a host only when the platform does not expose the required data centrally.
- Do not run Prometheus, Grafana, Alloy, Loki, or an OpenTelemetry collector on either Spark.
- Do not install software on Synology or UniFi devices solely for this plan.

### Security posture

- General exporter listeners are reachable only from a dedicated observability egress identity assigned to approved collector pods in Phase 0. The current cluster performs node SNAT and has no Cilium egress gateway, so the three node addresses are not an acceptable external allowlist. A Cilium egress IP isolates ordinary pods but is assigned to a gateway node interface and can be deliberately bound by a node-privileged or `hostNetwork` process on that node; node compromise remains an accepted trust boundary, not a pod-policy guarantee. The Spark exception rejects direct access to `nv-monitor` on every non-loopback interface and permits only the dedicated monitoring SSH principal from that observability identity for the pod-local tunnel. Target ports and positive and negative reachability tests are part of each collector’s acceptance gate; the current UniFi firewall matrix is mostly intent, not a prerequisite assumed to exist.
- Proxmox and UniFi use local read-only service accounts or API tokens stored through the repository's existing SOPS path.
- Synology uses SNMPv3 rather than SNMPv2c.
- `nv-monitor` never receives a bearer token across a plaintext network hop. An extra container in the vmagent pod binds two forwards only to pod loopback, authenticates to each Spark's existing `sshd`, and reaches `nv-monitor` through the Spark's loopback address; the host firewall rejects direct access to the exporter port on every non-loopback interface. The token remains in a root-readable Spark environment file and a SOPS-managed vmagent scrape secret. The request crosses only pod loopback, the encrypted SSH connection, and Spark loopback. This preserves exactly one new monitoring binary on each Spark even though the current upstream exporter itself listens on `0.0.0.0`.
- Client names, MAC addresses, IP addresses, usernames, and flow tuples are not metric labels unless a bounded use case requires them. Full identity belongs in short-retention logs or flows, not permanent dashboard labels.
- Before external authentication logs, CEF, or flows are admitted, internal VictoriaMetrics and VictoriaLogs access moves behind VMAuth or equivalently authenticated read/write paths. The live Alloy and VictoriaLogs ingress policies are extended for the authentication proxy, LogsQL evaluator, and explicit probes before those clients move; their current `host`-entity carve-outs are narrowed to the proven health-check surface or retained as a documented node-privileged exception. Later receiver policies admit only the approved syslog/flow senders and authenticated internal clients.

### Reliability and capacity posture

- Time retention is not a capacity limit. Phase 0 sets numeric budgets for active series, series churn, samples per second, log and flow bytes per day, query latency, queue bytes/hours, and at least 20% free-space headroom on the 50 GiB metrics and 30 GiB operational-log volumes.
- vmagent’s current on-disk queue is an `emptyDir`, so a collector restart during a backend outage loses buffered data. Define its acceptable RPO, persistent-storage need, and `maxDiskUsagePerURL` before external targets increase reliance on that queue.
- Alloy’s `/var/lib/alloy` hostPath persists file positions, not a durable VictoriaLogs outbound queue; no write-ahead log is enabled for the `loki.write` path. PR #484 bounded the in-memory VictoriaLogs writer to 15 backoff retries and installed live alerts for sustained retries and dropped entries, but the accepted data-loss RPO remains undecided. Define that RPO or enable and bound a supported WAL before Loki is removed, then test an outage longer than the configured retry window and verify the live alerts. The primary residual risk is bounded but accepted log loss, not automatic host-disk growth.
- Establish log-ingest, cardinality, field-contract, and effective-retention baselines only from post-#484 data. Queries spanning the pre-#483 contaminated retention window must be treated as potentially duplicated or mislabeled until that history expires.
- Test backend outage, backpressure, collector restart, queue recovery, dropped samples/events, and store read-only behavior before calling the pipeline durable. “Durable” here means a documented and tested RPO, not that the current single-instance PVCs are highly available.

## Architecture

| Signal | Producers | Central ingestion | Store | Primary use |
|---|---|---|---|---|
| Infrastructure metrics | Kubernetes, PVE, Sparks, Synology, UniFi, Pis | vmagent, central exporters | VictoriaMetrics | Health, performance, capacity, alerts |
| Application metrics | vLLM and monitored services | vmagent | VictoriaMetrics | Request rate, latency, queueing, tokens, cache behavior |
| Kubernetes logs | CRI files | Alloy DaemonSet | VictoriaLogs | Workload and platform diagnosis |
| External operational logs | PVE, Sparks, Synology | Native or explicitly installed syslog forwarding to a restricted VictoriaLogs listener | VictoriaLogs | Host, storage, authentication, and audit diagnosis |
| UniFi activity/security events | UDM Pro | Native CEF-over-syslog export | VictoriaLogs | Configuration changes, WAN, PoE, firewall, IPS, honeypot, device events |
| Routed traffic samples | UDM Pro | Sampled IPFIX to GoFlow2; dedicated flow-routing sidecar | Separate short-retention VictoriaLogs flow instance | Cross-zone communication and security investigations |
| Synthetic outcomes | In-cluster Gatus/blackbox, edge-Pi Gatus, and Alertmanager Watchdog | Prometheus-compatible metrics, direct external notifications, and two independent heartbeats | VictoriaMetrics plus a healthchecks-style receiver outside the estate | User-visible reachability and detection of central-stack, Pi, or WAN failure |

Metrics are the index; logs and flows are the evidence. Dashboards should link to filtered Explore queries rather than reproduce a log viewer in every platform folder.

### Collector exposure design

External push collectors use dedicated BGP LoadBalancer addresses from `admin-prod` and `externalTrafficPolicy: Local` so the sender address survives Kubernetes ingress. The pool is not broadly restricted today: the Workloads-to-`admin-prod` deny is live, while the documented IoT/Guest denies and source-specific listener controls remain unapplied intent. Apply and negative-test those pool denies, then add per-listener source allows above a broad deny for each collector VIP before exposing a receiver. A single-active receiver endpoint is scheduled with an availability policy appropriate to its storage and protocol. Before relying on Cilium's advertisement behavior, drain the receiver node and observe BGP routes plus packet captures to prove the VIP is withdrawn from nodes without a local ready endpoint. Failover RPO is protocol- and storage-specific: UDP senders lose traffic throughout endpoint rescheduling and any RWO detach/attach, so IPFIX explicitly measures and accepts that full interval rather than describing it as a brief reconnection.

UniFi rules allow only the documented sender addresses and ports: the UDM Pro for CEF and IPFIX, the three PVE hosts and both Sparks for their syslog listener, and the Synology for its listener. Kubernetes NetworkPolicies repeat those allowlists at the receiver. General external scraping is the reverse path: the dedicated observability egress identity is allowed to each declared exporter/SNMP port, with every other source denied. `nv-monitor` is the exception: that identity may reach Spark SSH for the pod-local forward but may not reach the exporter port directly.

Live policy currently blocks K8s Prod from initiating into Workloads and blocks Workloads from protected networks and `admin-prod`, but UniFi's default inter-VLAN posture still permits K8s Prod to Infra Mgmt and most other matrix paths. Observability therefore starts by installing the missing broad topology denies, not by assuming they exist. Before each platform is commissioned, update the firewall-policy matrix in the network-topology plan and the applied-rule record in `network/unifi/README.md`, negative-test the broad deny, and only then add the narrowly ordered exception:

| Direction | Exact source | Exact destination | Purpose |
|---|---|---|---|
| Pull | Dedicated observability egress IP assigned in Phase 0 | PVE `10.32.20.21-.23` | TCP 8006 for the PVE API and TCP 9100 for node-exporter |
| Pull | Dedicated observability egress IP assigned in Phase 0 | Synology management `10.32.20.5` | UDP 161 for SNMPv3 |
| Pull | Dedicated observability egress IP assigned in Phase 0 | Sparks `10.32.21.31-.32` | TCP 22 for the restricted SSH forwards plus only the separately declared inference metrics ports |
| Pull | Dedicated observability egress IP assigned in Phase 0 | UDM Pro `10.32.1.1` | The verified UniFi controller API port for Unpoller |
| Push | PVE `10.32.20.21-.23`, Synology `10.32.20.5`, and Sparks `10.32.21.31-.32` | Their dedicated `admin-prod` listener VIPs | Only the source-class log transport and port chosen in Phase 0 |
| Push | Verified UDM Pro egress identity, expected to be its VLAN 30 interface `10.32.30.1` rather than inventory address `10.32.1.1` | Dedicated `admin-prod` CEF and IPFIX VIPs | Only the selected CEF and IPFIX ports |

Do not merge zones, allow whole source subnets, or assume the inventory address is the sender identity. Packet-capture each push path before committing its allow rule. For unauthenticated UDM protocols, the containment boundary is the trusted VLAN 30 segment and receiver policy; a host on that segment may spoof the gateway source, so the address is provenance metadata rather than authentication.

Phase 0 must deploy and validate a distinct source identity for approved collector pods, such as a Cilium egress-gateway IP or a narrowly scoped egress proxy; this is a new dependency because neither is enabled today. The collector namespace also receives egress default-deny with explicit DNS and target allowances selected by service account. UniFi permits only the dedicated identity, so ordinary node-SNAT traffic and an unselected non-`hostNetwork` pod remain denied. A Cilium egress-gateway IP is present on a node interface, is not highly available by itself, and remains usable by a deliberately node-privileged process; if chosen, its address assignment requires the repository's guarded Talos rollout process, an explicit node-trust acceptance, and measured gateway-node outage/recovery behavior. A proxy alternative must provide the same stable identity and outage test. If a distinct, testable identity cannot be provided, external pulls do not ship; selecting a service account without an egress-isolation policy is not enforcement.

VLAN 25 is a separate L2 bypass around both UniFi routing policy and Cilium identity: PVE storage interfaces, the Synology storage interface, Kubernetes storage bridges, and Multus-attached Longhorn workloads can communicate without traversing the gateway. PVE `node_exporter` therefore binds only its VLAN 20 management address, PVE API targets use management FQDNs, and DSM SNMPv3 is limited to the management interface through supported binding or DSM firewall policy. The Spark exporter cannot bind selectively in current upstream, so its host firewall drops port 9101 on every non-loopback interface, including Workloads, Storage, and both private fabric legs. Commissioning includes negative probes from a PVE/Synology storage address and a disposable Multus-attached pod; if DSM cannot enforce and prove the management-only SNMP boundary, do not enable SNMP.

VictoriaLogs uses separate source-class listeners where enrichment differs. Each listener explicitly configures TCP/TLS or UDP, `useRemoteIP`, `streamFields`, fixed extra fields such as `site` and `platform`, ignored fields, timestamp behavior, and timezone. OSS VictoriaLogs does not provide syslog mTLS, so controllable host forwarders use client-certificate authentication only through a named central rsyslog or syslog-ng TLS terminator that forwards to a policy-restricted VictoriaLogs listener; if Phase 0 does not select and fixture such a relay, every external syslog source is declared `transport_auth=none`. Server-authenticated TLS alone encrypts transport but does not authenticate the sender. Appliance exports that offer only unauthenticated syslog or UDP receive a dedicated listener and an ingress-derived `source_identity` plus `transport_auth=none`; payload hostname or source fields cannot override that identity. Their path must remain inside the trusted wired estate, and their events are advisory rather than the sole authority for destructive automation. Parser fixtures cover UniFi CEF, PVE, Spark, and Synology messages; known-event canaries and sender heartbeats make quiet-source loss distinguishable from “nothing happened.”

Acceptance includes source-IP preservation, positive tests from each sender, negative tests from an unapproved VLAN, proof that a forged payload identity cannot replace ingress-derived identity, failover to a new receiver node, malformed payloads, clock skew, backpressure, packet loss where measurable, and alerting on listener/decode failure. For an unauthenticated protocol, the acceptance record explicitly says that a packet forged with the permitted network source cannot be rejected cryptographically; ACLs are containment, not proof of origin.

## Data contract

### Metric labels

The canonical dimensions are:

| Label | Meaning | Examples |
|---|---|---|
| `site` | Physical or administrative site | `home` |
| `environment` | Lifecycle or trust context | `prod`, `sandbox` |
| `domain` | Broad operating domain | `compute`, `storage`, `network`, `security` |
| `platform` | Owning platform | `kubernetes`, `proxmox`, `dgx_spark`, `synology`, `unifi` |
| `cluster` | A real clustered system only | `k8s-prod`, `pve-sbx` |
| `host` | Stable host or managed-device name | `spark-1`, `pve-sbx-1`, `core-switch` |
| `service` | Stable logical service | `vllm`, `nfs`, `traefik` |
| `instance` | Canonical scrape endpoint after relabeling | `spark-1:9101`, `10.32.20.21:9100` |

`instance` is not a durable identity. Dashboards and alerts use `host`, `service`, and the relevant platform dimensions.

Tunnelled Spark jobs relabel pod-loopback targets such as `127.0.0.1:19101` and `:19102` to canonical `spark-1:9101` and `spark-2:9101` instances while setting the durable `host` label explicitly. Loopback port numbers never escape into dashboards, alerts, or stored identity.

The current global `externalLabels.cluster=k8s-prod` is unsafe once vmagent scrapes the rest of the estate, and vmalert independently applies the same global label to every alert. Migrate both components: add `site=home`, explicitly label Kubernetes jobs and rules with `cluster=k8s-prod`, label clustered external targets with their real cluster, omit `cluster` for standalone systems, and then remove both global cluster defaults. Alloy's VictoriaLogs writer already stamps the correct fixed `cluster=k8s-prod`; retain it and add fixed `site=home`, `environment=prod`, and `platform=kubernetes` fields under the log contract. Gate the migration on dashboard and recording-rule compatibility and zero unexpected `exported_cluster`, missing-identity, or wrongly stamped metric, alert, or log rows.

### Log fields and streams

Normalize at least `site`, `environment`, `domain`, `platform`, `host`, `service`, `severity`, and `event_type` when the source supplies them reliably.

Only stable, low-cardinality fields become VictoriaLogs stream fields. Source/destination IPs, ports, client MACs, usernames, trace IDs, request IDs, model request IDs, and free-form event names remain ordinary searchable fields. This prevents a stream explosion while retaining security and diagnostic detail.

### Inventory and desired state

The repository must hold a non-secret external-target inventory with address, platform, role, scrape class, expected state, criticality, maintenance window, credential reference, firewall-policy reference, ports, expected metric families, probe vantages, owner, and runbook. Credentials remain in SOPS secrets. The inventory distinguishes active, absent, expected-offline, maintenance, and retired equipment so alerts describe drift from intent rather than raw discovery state.

## Collection plan by platform

### Kubernetes

- Retain the VictoriaMetrics k8s-stack, its native kube-state-metrics and node-exporter, platform ServiceMonitors converted under explicit owner references, the standalone Prometheus Operator CRDs, and Alloy CRI collection.
- Complete the corrected Alloy log-path soak, then remove the rollback-only KPS and Loki paths through the cleanup tracked in #470. Their scrape, rule, notification, datasource, CRD, and Flux ownership has already moved.
- Add the missing curated dashboards for Longhorn, Traefik, cert-manager, Flux, Alloy, and the observability pipeline itself; preserve and extend the curated Longhorn alerts already present in the repository.
- Keep the current 30-second platform scrape cadence unless a specific alert or dashboard requires otherwise.
- Turn on Hubble metrics only after the core estate is covered. L7 visibility remains a separate decision because it changes the datapath and cardinality profile.
- Preserve the live VM-native controller-manager, scheduler, and etcd scrapes at three healthy targets each. Controller-manager and scheduler use the vmagent service-account token over HTTPS to the Talos node-address listeners. Their self-signed certificates contain only `localhost` and `127.0.0.1` subject alternatives, so scraping by node IP retains transport encryption and authorization but disables certificate verification. Etcd uses its separate plaintext metrics-only listener on port 2381 without a bearer token.

The original component research remains useful source material:

- cert-manager: dashboard 11001 and its certificate-expiry rules.
- Alloy: the rendered Alloy mixin dashboards and alerts, which corrected the bake-off’s original assumption that Alloy had no upstream dashboard.
- Flux: the `flux2-monitoring-example` dashboards; its alerts are example-grade and its resource-state panels may require kube-state-metrics CustomResourceState configuration.
- Traefik: dashboard 17347 as a starting point, with the known absence of Gateway API-specific coverage.
- Longhorn: dashboard 16888 remains useful source material. Thirteen curated Longhorn backup, health, capacity, and metrics-missing alerts already exist in-repo, so example rules are comparison material rather than an alert gap to re-import.

These references are discovery inputs, not runtime dependencies. Vendor the useful JSON and rules, pin them in Git, and validate unique dashboard UID, title, folder, and datasource combinations. No mutable dashboard-sync job currently exists; chart-generated ConfigMaps remain until an explicitly reviewed replacement disables their owning chart values.

### Proxmox `pve-sbx`

- Run one `node_exporter` systemd service on each of the three PVE hosts for Linux CPU, memory, filesystem, disk, thermal, and interface metrics. Bind it only to that host's `10.32.20.x` management address; the VLAN 25 storage address must not listen.
- Run `prometheus-pve-exporter` centrally in Kubernetes. There is no PVE cluster VIP and round-robin DNS is intentionally forbidden, so query the `pve-cluster` class with cluster metrics enabled and node metrics disabled through all three API nodes. Configure the API targets as `pve-sbx-1.home.kelch.io`, `pve-sbx-2.home.kelch.io`, and `pve-sbx-3.home.kelch.io` with TLS verification enabled because the node certificates cover those names; UniFi rules still key on resolved management IPs `10.32.20.21-.23`. Retain the three raw target identities for source health, normalize duplicate cluster facts through `estate_*` recording rules for dashboards and alerts, and alert only when every cluster-class source is stale or failed. Use a separate `pve-node` class with cluster metrics disabled and node metrics enabled against each corresponding node.
- Use a least-privilege Proxmox API token and validate its behavior during quorum loss and when one node is unreachable.
- PVE 9 is journald-first and does not provide an existing rsyslog path to assume. Forward selected `pvedaemon`, `pveproxy`, `pvestatd`, Corosync, kernel, ZFS/storage, backup, authentication, and systemd failure events only after Phase 0 chooses and tests either an explicit rsyslog installation or a journal-native forwarder/receiver path.
- Keep guest monitoring service-oriented. Do not install a node exporter in every VM merely because the VM exists.
- Alert on quorum risk, node loss, Corosync degradation, storage pressure, host temperature, interface errors, exporter API failure/staleness, and guest state only where desired state says the guest should be running.
- Establish and test a native PVE notification target for backup and task outcomes before calling that path authoritative. Direct-to-MX delivery currently fails with Gmail `550 5.7.1`; choose and exercise authenticated SMTP, Pushover, or another PVE-owned target that does not depend on the central observability stack. Add central backup/task alerts only after a real log or API fixture proves the relevant result fields; the exporter’s object-state metrics must not be described as task-history coverage.

### DGX Sparks

Start with exactly one new monitoring binary on each Spark: `nv-monitor` in headless Prometheus mode under systemd. It is purpose-built for Grace/GB10, handles unified memory and ARM core topology, and supplies useful CPU, memory, filesystem, GPU, aggregate network, and endpoint RDMA telemetry with no runtime dependency beyond the existing NVIDIA driver/NVML path.

The only other metric endpoints are supplied by software already serving inference. vLLM or another inference server exposes its own `/metrics`; scraping it does not add a telemetry daemon. The initial cadence is 5 seconds for `nv-monitor` and active inference endpoints, with 30 seconds for lower-value host checks if separate jobs are needed.

Use journald and any already-installed syslog facility for selected OOM, kernel, NVIDIA driver/XID, systemd service, SSH, sudo, inference-service, storage-mount, and RDMA link events. Do not assume rsyslog exists, and do not install Alloy, Prometheus, Grafana, DCGM, a container monitoring stack, or a general tracing collector on the Sparks in the initial design.

`nv-monitor` is not a complete Linux host exporter. It does not establish full SMART/NVMe health, service state, NFS mount state, per-interface link/error health, fabric peer identity, topology, or congestion. Before building an alert or panel, map it to an observed metric or log field in a Spark coverage matrix. Preserve the one-binary posture by accepting or log-checking non-critical gaps first; add a restricted node-exporter/textfile path only if a required alert cannot be sourced otherwise.

The private `198.19.240.0/24` and `198.19.241.0/24` Spark fabric never reaches UniFi and is not covered by gateway flows. Use endpoint RDMA state/rate/byte/packet/error counters for basic link and asymmetry detection, while treating peer/topology/congestion diagnosis as deferred.

The systemd unit sets `nv-monitor`’s internal refresh explicitly rather than assuming the 5-second Prometheus scrape controls its own sampling loop. Acceptance records exporter CPU/memory, scrape duration and timeout rate, and verifies no material TTFT or queue regression under the repository’s known three-session inference load.

Because `nv-monitor` intentionally omits TLS and current upstream commit `755aae35b6ee66b5c1fca7f1ad5773539513a48a` hardcodes `INADDR_ANY`, the systemd service cannot satisfy a loopback-bind requirement by configuration. A declared extra container in the vmagent pod maintains one SSH local forward to each Spark using a dedicated key and an account restricted to forwarding to host loopback `127.0.0.1:9101`. Both forward listeners bind only to the shared pod loopback namespace, so vmagent's bearer-authenticated HTTP request reaches the tunnel without crossing the cluster network in plaintext. Before the exporter starts, a separate `inet nv_monitor_guard` nftables table permits port 9101 on `lo` and drops it on every non-loopback interface, including VLAN 21, VLAN 25, and both private fabric legs; this is the primary boundary, not defense in depth. Load it through a dedicated oneshot unit ordered before `nv-monitor.service`, mirroring but not modifying the existing `dgx_fabric_guard` unit/table lifecycle. The stock `nftables.service` remains disabled because its `flush ruleset` behavior would replace Docker-managed state. The dedicated principal accepts SSH only from the Phase 0 observability egress identity. Validate both guard tables, Docker networking, and port behavior after application and reboot. Test `ss` output, direct denial through every non-loopback address, access through the SSH forward, `authorized_keys`/`sshd` forwarding restrictions, reconnect, host-key pinning, credential rotation, tunnel failure alerting, and five-second scrape behavior during a tunnel restart. If a reliable restricted SSH-forward account and host-firewall boundary cannot be established, use a host TLS proxy and count it as an explicit exception to the one-binary posture rather than exposing a bearer token over HTTP.

The source restriction applies only to the dedicated monitoring principal; existing operator and host-automation SSH accounts and their approved source paths remain intact. SSH is nevertheless now part of the telemetry path. The documented unified-memory-collapse failure can make a Spark disappear from SSH before diagnosis is complete, so central scraping cannot always distinguish tunnel loss from host collapse. Acceptance must set a measured memory-headroom alert with useful lead time before the known collapse region, prove tunnel-down alerting, and use independent ICMP/inference probes to separate an SSH-path failure from broader host loss where the host still responds.

The inference-target inventory records which mutually exclusive serving mode is active, its port and image/version, expected metric families, and a PromQL fixture for request success, queueing, TTFT, preemption, cache, speculative decode, token throughput, and memory pressure. Five-second scraping begins only after the active mode’s cardinality and metric names are captured.

#### Spark dashboards

- Keep NVIDIA's built-in DGX Dashboard as a local break-glass and real-time operations UI; it is not the historical Grafana source of truth.
- Vendor an inference-oriented community dashboard as source material for tokens, request throughput, time to first token, queueing, KV cache, prefix-cache, and speculative-decoding panels.
- Rework host panels against `nv-monitor`'s `nv_*` metric names rather than installing another exporter solely to satisfy imported queries.
- Add a two-node comparison view for the metrics actually proven by the coverage matrix: utilization, temperature, power, unified memory, storage capacity, aggregate Ethernet, and endpoint RDMA traffic initially.
- Pin the resulting dashboard JSON or YAML in Git and use repository-owned datasource UIDs and variables. Do not depend on a mutable Grafana.com dashboard ID at runtime.

### Synology

- Enable SNMPv3 and poll it centrally through `snmp_exporter`; do not install a package in DSM.
- The original plan identified community dashboards 14284 and 13516 and a one-time `snmp.yml` DisplayString pass as starting points. Reverify their queries against the actual DSM walk before vendoring either dashboard.
- Start with supported Synology and standard HOST-RESOURCES/IF-MIB metrics: system status, temperature, fans, disk/volume state, capacity, interfaces, and uptime.
- Treat DSM's model- and version-dependent MIB coverage as incomplete. Pair device metrics with direct NFS, SMB, HTTPS, DNS, and backup-outcome checks where those outcomes matter.
- Export selected DSM, storage, authentication, network, and backup events using Log Center/syslog where available.
- Configure and synthetically test a native DSM notification destination for disk, RAID, volume, power, and backup failures because the NAS may outlive or host dependencies of the central stack; do not infer delivery merely from enabled local alert rules.
- Do not make the primary telemetry store dependent on Synology availability. Long-term archival to NAS-backed object storage remains a later, explicitly tested retention project.
- Before enabling Synology alerts, record the exact model and DSM version, walk the available MIB modules, map usable OIDs to alerts, record absent or malformed values, require SNMPv3 `authPriv`, and define NFS/SMB/HTTPS/DNS and backup-artifact-freshness probes for outcomes SNMP cannot prove.

### UniFi

#### Metrics and reachability

- Deploy Unpoller centrally with a read-only local account. Collect controller, site, gateway, WAN, switch, AP, PoE, radio, and bounded aggregate client metrics into VictoriaMetrics.
- The original plan pinned Unpoller v3.3.1 and identified its six dashboards 11310–11315. Reverify the current release and dashboard revisions, then vendor the compatible JSON rather than retaining mutable runtime imports.
- Start with Unpoller's upstream dashboard set as a compatibility and discovery aid, then vendor the useful panels and build repository-owned overview dashboards.
- Pin an Unpoller release with the Prometheus background-cache behavior introduced in v3.2.0 or later. Keep its cache refresh at the documented 60-second default and allow vmagent to scrape the cached endpoint every 30 seconds; a faster scrape does not create finer controller data. Verify cache age and controller request cadence during acceptance rather than relying only on configuration intent.
- Start Unpoller in an explicit Prometheus-only profile. Disable event, syslog, IDS, DPI, and traffic persistence; drop client MAC/name labels rather than relying on hashing, which preserves cardinality and is only pseudonymization. Enable additional data only after controller load and series growth are measured.
- Probe the UDM Pro, core switches, and AP management addresses using in-cluster ICMP/TCP for fast diagnosis and an edge-Pi vantage for central-stack-independent reachability. A controller/API outage must not look identical to every network device failing simultaneously.
- Classify the offline `10.32.1.209` switch before enabling device-offline alerts.

#### Logs and security events

- Use UniFi's native System Logging/SIEM export for activity logs. It emits structured CEF covering monitoring, WAN, power, security, and system events.
- Preflight the live UniFi export controls and use TLS for transport encryption if the installed version actually offers it. Mark the connection `transport_auth=client-cert` only if the sender and selected ingress prove client-certificate authentication; server-authenticated TLS remains `transport_auth=none`. Otherwise send native CEF to a dedicated source-restricted VictoriaLogs listener, mark it `transport_auth=none`, derive `source_identity=unifi-gateway` at ingress, and let VictoriaLogs perform native CEF parsing. Do not mistake a source-address allowlist or payload hostname for cryptographic sender authentication.
- Do not simultaneously ingest the same UniFi events through Unpoller's Loki/event path.
- Retain admin changes, device adoption/offline, WAN outage/failover, PoE problems, firewall/IPS/honeypot activity, and selected client events. Drop or sample routine association churn if it dominates volume without diagnostic value.
- Treat unauthenticated CEF as corroborating evidence. High-impact conclusions and any future automated response require a second signal such as native UniFi notification state, controller/API state, firewall configuration, or endpoint evidence.
- Configure and synthetically test the native UniFi notification destination before treating it as independent corroboration or a central-stack-independent alert path.

#### Routed flows

- Send the UDM Pro's sampled NetFlow/IPFIX export to a small GoFlow2 Deployment in Kubernetes.
- Require a UniFi flow preflight before deployment: confirm Network and UniFi OS version compatibility, installed console storage and Traffic Flows availability, the current toggle state, and healthy local flow capture. The UDM Pro meets the documented software floor; its storage prerequisite remains unverified.
- Restrict the UDP collector to the packet-capture-verified gateway egress identity, expected to be VLAN 30 address `10.32.30.1` for an `admin-prod` VIP, and expose GoFlow2's own Prometheus health, socket-drop, sequence-gap, template, and decode-failure signals where available. Do not substitute the UDM inventory address `10.32.1.1` without observing it on this path. Native IPFIX-over-UDP is neither encrypted nor sender-authenticated; the allowlist constrains reachability but does not prove record origin, and another VLAN 30 participant may spoof the allowed source. Keep the first hop inside that trusted segment, derive `source_identity=udm-pro-flows` at ingress, mark records `transport_auth=none`, and disable export or add network-level encrypted transport before any future path crosses an untrusted network.
- Capture a real UDM Pro sample before designing fields or detections. Inventory exported templates and fields, verify sampling-rate interpretation, and commit a GoFlow2 mapping when vendor fields require one. Do not assume that the export includes the richer allowed/blocked/risk/policy metadata visible in UniFi's local flow UI.
- Preserve each decoded flow as JSON. Decoded source/destination addresses, ports, protocols, byte counts, and sampling metadata remain searchable fields, not stream labels. Treat them as advisory until corroborated; absence from a sampled feed is never evidence that communication did not occur.
- Start with the controller's conservative sampling and measure events/day, bytes/day, ingest CPU, query latency, and useful detections before changing sampling.
- Use a separate small VictoriaLogs flow instance with 7–14-day retention so unknown flow volume cannot evict operational logs. GoFlow2 writes decoded JSON to a size-bounded RWO PVC rather than stdout or `emptyDir`, and a co-scheduled central sidecar forwards only that file to the flow instance. Specify rotation, retention, rotation-aware tailing and drain semantics, the accepted in-memory loss before fsync, and the maximum backlog that survives a pod restart; alert before the volume limit and prove rotation, abrupt restart, graceful drain, and sustained input without loss beyond that RPO, duplication, or pod eviction. Preserve GoFlow2 and sidecar operational stdout/stderr in the 30-day store. Acceptance proves decoded payload never appears in CRI logs; add a narrowly container-scoped Alloy exclusion only as defense in depth if the selected image can emit payload to stdout.
- Cross-store correlation is initially a Grafana/manual investigation, not a single LogsQL alert. Automated detections operate within one store or publish a bounded derived metric/event; they do not pretend one vmalert datasource can join the operational-log, flow, and metric stores.
- Do not add ClickHouse or a separate flow UI until measured volume or query ergonomics prove VictoriaLogs inadequate.
- Gateway flows see sessions traversing the UDM Pro. They do not see same-VLAN L2 traffic, switched Storage traffic, or the private Spark RDMA fabric.

#### Protect boundary

- Monitor camera reachability, switch/AP attachment, PoE supply, and controller-native offline/recording alerts where exposed.
- Do not ingest video, thumbnails, or motion imagery into the observability stack.
- A dedicated Protect API exporter is deferred until a maintained exporter and a concrete recording-health gap justify its credentials and maintenance surface.
- Before claiming Protect health coverage, test one camera-offline and one recording-health event and record exactly which signals reach UniFi notifications, CEF, Unpoller, or only the Protect UI.

### Edge Pis and service outcomes

- Continue the planned NixOS `node_exporter` service for edge Pis, with SD-card-sensitive collectors disabled where writes or expensive scans are a concern.
- Use dashboard 1860 as node-exporter source material, then vendor the subset that remains useful for the Pi fleet.
- Deploy Gatus for declarative DNS, TCP, TLS, HTTP, and application checks. Its configuration and alert intent remain YAML in Git.
- Run the main Gatus deployment in-cluster and a second bounded instance on one edge Pi through the `rpi-nixos` repository. The Pi instance sends a heartbeat to the external dead-man endpoint and sends probe failures directly to the chosen external notification channel rather than through the central Alertmanager. Missing central Watchdog and missing Pi heartbeat are separate checks so cluster failure, Pi failure, and WAN loss do not collapse into one silent condition.
- Gatus remains preferred over Uptime Kuma because the desired checks and alerts stay reviewable in Git rather than living primarily in UI-edited SQLite.
- Use blackbox exporter where Prometheus-native probing or existing dashboards are more useful; avoid duplicating the same probe in both systems without a clear owner.
- Keep a probe inventory containing source vantage, target, protocol, expected success or expected denial, cadence, criticality, owner, and alert route. In-cluster probes cover service behavior; an edge Pi covers observability dead-man, gateway/WAN, and selected user-path checks during cluster degradation.

## Scrape and event cadence

| Class | Initial cadence | Rationale |
|---|---|---|
| Kubernetes, PVE, Synology, Pis | 30s | Normal fleet health and capacity resolution |
| Unpoller exporter | 30s scrape, approximately 60s controller refresh | Respects controller data freshness while fitting the common scrape loop |
| Critical WAN and management probes | 10–15s | Faster outage and failover visibility with very low series count |
| Spark `nv-monitor` | 5s | Captures short GPU, power, memory, and RDMA behavior during inference |
| Active inference `/metrics` | 5s | Useful queue, latency, cache, and token-rate resolution |
| Logs and CEF | Push/event driven | Preserve event time and avoid polling |
| IPFIX | Gateway-sampled push | Control volume at the source |

High-frequency inference diagnosis should eventually use request-level structured events or traces emitted by the serving path. Prometheus remains appropriate for five-second aggregates; it should not become a token-by-token event store.

## Dashboard information architecture

| Folder | Purpose | Initial dashboards |
|---|---|---|
| `Home` | Whole-estate status and current work | Estate Overview, Active Incidents, Capacity Outlook |
| `Platforms/Kubernetes` | Cluster and node operations | Cluster, Nodes, Control Plane, Longhorn, Cilium, Flux, Traefik, cert-manager |
| `Platforms/Proxmox` | Hypervisor and guest operations | Cluster, Hosts, Guests, Storage and Backups |
| `Platforms/DGX Spark` | Host, GPU, fabric, and inference | Spark Fleet, Spark Host Detail, Inference Service |
| `Platforms/Synology` | NAS and storage operations | NAS Overview, Disks and Volumes, File-Service Outcomes |
| `Platforms/UniFi` | Network operations | WAN and Gateway, Switching and PoE, Wi-Fi and APs, Device Detail |
| `Services` | User-visible workloads | Service Overview and service-specific drill-downs |
| `Network & Security` | Event and communication investigation | UniFi Security Events, Routed Flows, Authentication and Admin Activity |
| `Observability` | The monitoring system itself | Scrape Health, Log Ingestion, Alert Delivery, Cardinality and Retention |

Do not create one dashboard per host. Use variables for site, environment, platform, cluster, host, service, device role, and time range. Imported upstream dashboards may continue to use native exporter metrics; curated overview dashboards should use a small `estate_*` recording-rule interface so an exporter replacement does not rewrite the home page.

Every actionable alert should link to the most specific useful dashboard and, where applicable, a pre-filtered VictoriaLogs query. Dashboard rows containing recent events are useful; duplicating Explore in every dashboard is not.

The deployed sidecar only creates the folder structure when ConfigMaps carry the expected annotation/path; chart-generated dashboards currently do not obey this governance uniformly. Dashboard CI must validate unique UID/title/folder/datasource combinations, parse queries, detect missing datasources and common no-data errors, and ensure alert annotations point at valid dashboards and log queries. Set Estate Overview as Grafana’s actual home dashboard. Once repository-owned copies replace a chart's generated artifacts, disable those dashboards through the reviewed chart values that own them; no mutable `main`/`master` dashboard-sync job exists today.

Datasource convergence is completed baseline: retain `victoriametrics` as the default metrics UID and `alertmanager-vm` as the production Alertmanager UID. The datasource displayed as `Prometheus` with UID `prometheus-kps` and the datasource displayed as `Loki` with UID `loki` remain rollback-only until #470 cleanup. Grafana provisioning deletes by datasource name, so cleanup must add `Prometheus` and `Loki` to `deleteDatasources` or prove equivalent `prune` behavior, then verify through the Grafana API that both names and UIDs are absent. Do not rename or reuse either UID. Future dashboard work maps only to the production UIDs.

## Alerting strategy

Every rule carries a common alert contract: `severity`, `signal`, `site`, `platform`, optional real `cluster`, and the applicable `host` or `service`, plus owner, runbook, dashboard, and log-query annotations. Each production evaluator also stamps the internal notification-authority label `evaluator=vmalert`, which is preserved independently of the estate `cluster` migration and is required by the current non-null VMAlertmanager route. Alertmanager grouping, inhibition, send-resolved behavior, retry behavior, and page/notify/review routes are part of this contract rather than dashboard conventions.

### First alerts

- Alert delivery pipeline unavailable or untested.
- VictoriaMetrics/VictoriaLogs ingestion failure, exporter scrape failure, or unexpected cardinality/volume growth.
- Kubernetes API/node, Longhorn, ingress, certificate, and GitOps failures after default-rule noise is curated. Add target-count and `up` alerts for the live VM-native etcd, scheduler, and controller-manager pools now; introduce richer component-specific rules only after their actual Talos metric families and failure behavior are fixture-tested.
- Proxmox quorum risk, Corosync link degradation, node loss, storage exhaustion, and thermal or NIC faults; backup failure coverage remains incomplete until a tested PVE-owned notification target exists, and that native path remains the intended authority until central task-result ingestion is proven.
- Spark unavailable, sustained thermal/power throttle, unified-memory pressure, disk pressure, inference endpoint failure, queue saturation, severe TTFT regression, and RDMA link/error problems.
- Synology disk/RAID/volume degradation, capacity risk, interface loss, and failed NFS/SMB/backup outcome.
- UniFi controller/gateway failure, WAN outage/failover, device offline relative to desired state, uplink downgrade, LAG member loss, port errors, PoE budget/underpower, and sustained AP retry or channel-utilization problems.
- Security events with a concrete response: privileged admin change, IPS/honeypot event, repeated authentication failure, high-rate cross-zone block, or an unexpected communication path.

### Noise controls

- Do not alert on generic `error` strings or every nonzero CEF severity.
- Disable unwanted chart defaults through reviewed values such as `defaultRules.disabled.<AlertName>` rather than deleting rendered rules or maintaining ad hoc negative copies.
- Use `for` durations and maintenance/desired-state labels to prevent discovery churn from paging.
- Separate page-worthy availability/data-loss alerts from informational capacity and security-review notifications.
- Keep and periodically test native UniFi, DSM, and Proxmox notifications for central-stack-independent emergencies, but do not claim coverage before each destination succeeds and document the owner of each duplicate notification.
- Test the full path with controlled synthetic failures before declaring a platform covered.

## SIEM-lite boundary

VictoriaLogs plus CEF/syslog, LogsQL alerting, Grafana, and flow search form the initial SIEM-lite capability. This provides centralized audit, network-security event search, limited correlation, and actionable detections without another agent fleet or a heavy security platform.

It is not an EDR, vulnerability manager, file-integrity monitor, malware sandbox, or packet-retention system. Add Wazuh, Security Onion, or another specialized platform only when a named requirement needs those capabilities and its operating cost is accepted.

Initial investigations and same-store rules should remain narrow. Cross-store items are dashboard or manual correlations until a bounded derived signal is deliberately published:

- UniFi admin configuration change near a network outage or device restart.
- Repeated SSH/sudo/authentication failures followed by a successful privileged action.
- IPS or honeypot event investigated alongside sampled flow activity from the same source; this is not a single LogsQL join while flows use their isolated store.
- Unexpected routed traffic between trust zones.
- Proxmox task or guest-state change near a Corosync, storage, or backup error.
- Spark inference regression investigated alongside an OOM, driver, storage-mount, or RDMA event; a derived alert can combine them later if a concrete response requires automation.

## Sequenced implementation plan

### Phase 0 — contracts, inventory, and budgets

1. Commit the label and log-field contracts in this plan and create the external-target inventory shape.
2. Inventory expected state for PVE hosts, Sparks, Synology, UniFi devices, and edge Pis, including the currently offline UniFi switch.
3. Re-baseline active series, churn, samples/sec, VictoriaMetrics disk growth, post-#484 VictoriaLogs bytes/day and stream cardinality, current alert volume, query latency, queue use, and free-space headroom; set numeric stop/go budgets for each new source. Exclude the duplicated/mislabeled retention window from log sizing.
4. Preserve the tested Pushover page path, define notify and review destinations, and conduct another delivery test before adding more rules. Inventory the actual native notification destinations on PVE, DSM, and UniFi; configure any missing path and synthetically prove delivery. Record PVE's current Gmail `550 5.7.1` failure as open until a replacement target succeeds.
5. Design the vmagent and vmalert `cluster=k8s-prod` external-label migration and its dashboard, recording-rule, alert, and historical-query compatibility matrix.
6. Allocate and prove a dedicated observability egress identity for collector pods before creating an external pull allow. Enable the required Cilium egress-gateway path or a narrowly scoped proxy, apply collector-namespace egress default-deny plus service-account-specific DNS and target allowances, and prove unselected non-`hostNetwork` pods and raw node-SNAT traffic cannot use that identity. Record node-privileged access as the trust boundary; for an egress gateway, schedule the guarded Talos address rollout and test gateway-node failure/recovery. Define the exporter/SNMP ports, collector VIPs, `externalTrafficPolicy`, UniFi rules, and positive/negative network tests. Apply the missing broad K8s-to-Mgmt deny and `admin-prod` IoT/Guest plus per-listener source restrictions before their narrow allows; amend the topology matrix explicitly for observability-egress-to-Spark TCP 22 and inference metrics, and record live versus intended state in both network documents.
7. Extend SOPS path rules if encrypted host-side material will live outside the existing Kubernetes/bootstrap/Talos paths; central exporter credentials remain Kubernetes SOPS Secrets, while Spark tokens use root-readable host environment files provisioned through the chosen host-management path.
8. Choose and fixture the external journal-forwarding mechanism for PVE 9 and confirm which syslog facility, if any, is already present on the Sparks.
9. Record a transport-capability and trust matrix for every pushed source: protocol, encryption, sender authentication, network path, ingress-derived identity, spoofing limitation, credential owner, and whether the signal may stand alone for alerting or automation.
10. Define the vmagent queue RPO and `maxDiskUsagePerURL`. Either explicitly accept loss when the current `emptyDir` queue and vmagent restart during a VMSingle outage coincide, or provision bounded persistent queue storage; test the chosen behavior before adding external targets.
11. Inventory every L2 path that bypasses routed policy. Require management-only binding or host firewall enforcement for PVE exporters and DSM SNMP, drop Spark exporter access on every non-loopback interface, and design negative probes from VLAN 25 plus a Multus-attached pod before enabling a target.

**Gate:** no external scrape target is added until it receives unambiguous `site`, `platform`, and `host` labels, plus `cluster` only when it belongs to a real cluster. Numeric budgets, credentials, firewall paths, expected metrics, independent reachability, and the accepted or remediated vmagent queue RPO are recorded and tested first.

### Phase 1 — close convergence and alert-resilience gaps

The risky ownership transfer is complete rather than future work. PR #478 retired OpenObserve, installed and adopted standalone Prometheus Operator CRDs, enabled converted-object owner references, and began dependency decoupling. PRs #480 and #481 moved Kubernetes infrastructure scraping, kube-state-metrics, node-exporter, custom and default rules, Pushover delivery, VMAlertmanager, and Grafana's default datasource to VictoriaMetrics-native ownership; PR #482 records the live validation. That validation found 67/67 Flux Kustomizations, 57/57 HelmReleases, every node and workload Ready, exact expected VM-native target counts without KPS overlap, a healthy Longhorn BackupTarget metric, one synthetic warning and resolution, zero Pushover failures, a clean final alert state, and representative Grafana queries through VictoriaMetrics.

PR #483 then found and corrected an independent Alloy log-path correlation defect: 461 of 742 active label/file pairs were wrong even though each physical file also had a correct mapping. That made the preceding log-volume and label-quality history unsuitable as a cleanup gate and restarted the VictoriaLogs/Loki correctness soak. Retained contaminated data is not deleted; it ages out under normal retention.

PR #484 hardened that corrected path with bounded retry and position recovery, collision-safe payload fields, a five-field stream identity, an 80% VictoriaLogs disk ceiling, metrics-native pipeline alerts, and live Cilium ingress policy for Alloy and VictoriaLogs. Because it changed field, stream, retry, and policy behavior, the final cleanup observation window begins after #484 rather than reusing #483-only evidence.

#### Phase 1A — completed survivor ownership

- Keep standalone CRD ownership, converted-object owner references, VM-native Kubernetes scrape ownership, the PVC label allowlist and Longhorn `BackupTarget` custom-resource metric, VM-owned rules, VMAlertmanager, and the VictoriaMetrics/VMAlertmanager Grafana datasources as the production path.
- Treat KPS Prometheus and its null-only Alertmanager as rollback-only. They must not regain scrape, rule, notification, datasource, CRD, or Flux authority during cleanup.

**Gate:** passed by PRs #478, #480, #481, and their recorded live checks. Any later change to these ownership boundaries requires the same target-count, duplicate-pool, rule, datasource, delivery, and dashboard checks.

#### Phase 1B — corrected-log soak and rollback-stack cleanup

- Observe post-#484 Alloy targets for at least 24 continuous hours. Sample every node and include ordinary pods plus Talos mirror pods; require exact filename-to-label agreement, collision-safe `msg.*` fields, the intended five-field stream identity, stable VictoriaLogs ingestion, zero unexpected send retries or drops, matching Loki/VictoriaLogs counts for named canary pods over bounded healthy-sink windows, and representative query correctness. After the healthy dual-write comparison window completes, make Loki unavailable long enough to fill its endpoint queue and measure whether the shared `loki.process` fanout stalls VictoriaLogs. If it does, do not attempt to preserve a broken dual-write topology: proceed only to the first cleanup commit that atomically removes the Loki forward and Flux dependency, then repeat the VictoriaLogs-only observation window before any manifest deletion. Use only post-#484 data for volume and stream-cardinality baselines.
- Before removing Loki, exercise a VictoriaLogs outage longer than Alloy's verified retry window. Record whether loss is accepted or enable and bound a supported WAL, prove recovery behavior, and confirm the pinned Alloy retry/drop metrics alert as expected.
- In the cleanup tracked by #470, use one first commit to remove Alloy's `loki.write` forward and `dependsOn: loki` together while retaining `dependsOn: victoria-logs-single`, then reconcile and observe that change alone. After uninterrupted VictoriaLogs delivery through the agreed window, remove the rollback-only Loki and KPS manifests plus stale converted children in a later commit/reconciliation step. Add Grafana `deleteDatasources` entries for the display names `Loki` and `Prometheus` to the surviving VictoriaMetrics `datasource.yaml` ConfigMap, and prove both old names and UIDs are absent through the Grafana API. Keep cleanup independently revertible from later external collection work.
- Name the data-retention decision for the KPS Prometheus, KPS Alertmanager, Loki, and retained OpenObserve claims before pruning them. The Loki chart's destructive scale-to-zero behavior means a rollback claim must not be inferred from a stopped workload.
- Re-run VM target counts, duplicate-pool checks, rule evaluation, Pushover delivery, Grafana metrics and Alertmanager datasources, VictoriaLogs queries, Alloy delivery health, Flux health, and workload readiness after cleanup. Confirm no KPS-owned mutating or validating webhook configuration or APIService remains, and server-side dry-run representative PrometheusRule and ServiceMonitor objects before declaring the standalone CRD path healthy.

**Gate:** one metrics path, one log path, and one notification authority remain; the post-#484 log pipeline is correct; no surviving resource depends on KPS or Loki; and the data-retention decision is explicit.

#### Phase 1C — independent alert resilience

- Preserve the tested VMAlertmanager-to-Pushover path and explicitly exercise the production Longhorn stale-backup condition tracked in #218.
- Provision the external healthchecks-style dead-man endpoint and route the central Watchdog heartbeat to it.
- Add the edge-Pi Gatus configuration in the `rpi-nixos` repository with its own heartbeat, direct external notification path, and minimal observability, gateway, WAN, and critical-service probes. Neither missed-heartbeat check may depend on Grafana, VictoriaMetrics, VictoriaLogs, or VMAlertmanager for delivery.
- Add the minimal Alert Delivery and Observability Pipeline dashboard without waiting for the general dashboard phase.

**Gate:** the stale-backup condition notifies and resolves; blocking the central Watchdog causes the external dead-man alert; blocking the Pi heartbeat also alerts externally; and a Pi-side probe failure delivers without the central stack.

#### Phase 1D — estate label migration

- Stamp `site=home` and `cluster=k8s-prod` explicitly on Kubernetes scrape and rule sources, add each real external cluster only at its own future target, and leave standalone target contracts without `cluster`. Retain Alloy's explicit `cluster=k8s-prod` writer label and add fixed `site=home`, `environment=prod`, and `platform=kubernetes` enrichment to new Kubernetes log rows.
- Remove `externalLabels.cluster=k8s-prod` from both vmagent and metric vmalert only after rendered configurations, dashboards, recording rules, alerts, and representative historical queries are compatible. Preserve `evaluator=vmalert` on every production evaluator, and preserve `site=home` globally only if the rendered test proves it cannot overwrite source identity.
- Check newly ingested samples, alerts, and VictoriaLogs rows for unexpected `exported_cluster`, missing `site`/`cluster` on Kubernetes data, or `cluster=k8s-prod` on standalone identities before Phase 2 adds an external target.

**Gate:** Kubernetes series, alerts, and new log rows retain correct identity without either metric global cluster default; standalone target fixtures remain clusterless; PVE fixtures carry only `cluster=pve-sbx`; and representative dashboards, rules, alerts, log queries, and historical queries pass the compatibility matrix defined in Phase 0.

**Phase 1 exit gate:** #470 cleanup is complete; #218's stale-backup and external dead-man work is complete; CRD, scrape, rule, notification, dashboard, datasource, and estate-label ownership is independent of KPS; Kubernetes metric continuity and post-#484 log correctness are accepted; and later external collection can proceed without reopening the bake-off architecture.

### Phase 2 — low-cost estate metrics

1. Before enabling each listener or API/SNMP path, apply its deny-by-default host, UniFi, and Kubernetes network controls and prove the intended scraper succeeds while an unapproved VLAN, unselected non-`hostNetwork` pod, ordinary node identity, VLAN 25 address, and Multus-attached pod fail as applicable. Record node-privileged/`hostNetwork` use of an interface-assigned egress IP as an accepted node-trust boundary and verify that losing its gateway node produces the documented alert and recovery behavior. Commission one platform at a time so no listener exists during an unprotected gap.
2. Deploy central `prometheus-pve-exporter`, `snmp_exporter`, Unpoller, blackbox exporter/Gatus, and their least-privilege secrets.
3. Install `node_exporter` on the three PVE hosts only after it is bound to each management address and denied on VLAN 25. Configure PVE API targets by their certificate-covered FQDNs with TLS verification enabled while retaining IP-based firewall rules.
4. Install the single `nv-monitor` binary and systemd unit on both Sparks, add the pod-loopback SSH-tunnel extra container to vmagent with pinned host keys, add a dedicated forwarding-only monitoring account restricted to the observability egress identity and host loopback `:9101`, leave existing operator/automation SSH access unchanged, apply and persist the additive port-9101 denial on every non-loopback interface before starting the exporter, and enable the required inference `/metrics` endpoints.
5. Enable SNMPv3 on Synology only after management-interface binding or DSM firewall enforcement denies VLAN 25, then validate actual MIB coverage. If DSM cannot enforce that boundary, leave SNMP disabled and use outcome probes plus native alerts until a safe path exists.
6. Add external vmagent targets using the Phase 0 label contract and the platform-specific scrape classes only after the Phase 1D label-migration gate passes.
7. Measure scrape duration, API-poll success and freshness, active-series growth, host/controller overhead, and data freshness before enabling alerts. `up=1` alone proves only that the exporter endpoint answered.

**Gate:** every target has an exporter-health signal, source-poll success/freshness where applicable, independent reachability, stable identity, bounded series count, documented credential ownership, and a coverage matrix stating what remains unmonitored. Every platform has a positive test from the real scraper and negative tests from an unapproved pod and VLAN. Spark acceptance additionally proves direct exporter access is denied, the authenticated tunnel reconnects, and no bearer token crosses plaintext transport.

### Phase 3 — dashboards and metric alerts

1. Keep the ConfigMap sidecar and create the folder hierarchy above.
2. Vendor and pin useful upstream dashboard JSON; adapt datasource UIDs and variable names in Git.
3. Build Estate Overview and one curated overview for each platform using `estate_*` recording rules where normalization is valuable.
4. Validate Estate Overview and then set its stable UID as Grafana’s home dashboard.
5. Add platform alerts in small batches, test controlled failures, and record useful links/runbooks.
6. Disable a chart's generated dashboards only after repository-owned replacements exist and pass compatibility checks; there is no mutable dashboard-sync job to remove in the current repository.
7. Run dashboard CI for UID/title/folder/datasource uniqueness, query validity, representative no-data behavior, and alert links.

**Gate:** an operator can start at a delivered alert, identify the affected platform or service, and reach supporting metrics in two dashboard hops or fewer.

### Phase 4 — authenticated internal paths, external logs, and UniFi CEF

#### Phase 4A — internal authentication and policy cutover

1. Inventory every VictoriaMetrics and VictoriaLogs data-path reader and writer. The current clients are vmagent writing metrics, metric vmalert reading metrics and persisting state through its generated paths, Grafana reading VictoriaMetrics and VictoriaLogs, Alloy writing VictoriaLogs, authenticated human access through the existing OIDC routes, and explicit health probes. Future clients are the LogsQL evaluator reading VictoriaLogs and using VictoriaMetrics state paths, an rsyslog/syslog-ng relay if selected, and the Phase 5 flow file-forwarding sidecar writing its isolated VictoriaLogs instance. The VictoriaMetrics operator reconciles Kubernetes resources through the Kubernetes API and is not assumed to be a backend data client without rendered or live evidence.
2. Provision VMAuth or equivalent authenticated routes and one credential identity per client. Move one client at a time and test its canary, authorization scope, and failure behavior.
3. Do not leave the legacy direct Services as a generally reachable rollback bypass. A temporary legacy route or port must sit behind default-deny policy and allow only the specific service account that has not yet cut over or is being rolled back. Remove that allowance after the client's observation window and remove the legacy route entirely when the named rollback window closes.
4. Audit the live Alloy and VictoriaLogs ingress policies and the remaining uncovered backend paths. Add the authentication proxy as the only backend data-plane principal where the design permits, authorize its exact insert/select/state paths, add the future LogsQL and flow client fixtures, and decide whether the current L4 `host`-entity carve-out can be narrowed to health checks or remains a documented node-privileged bypass. Enforce missing default-deny policy separately from external log admission, then prove every intended flow, an unapproved service account, and a representative denied network source.

**Gate:** vmagent ingestion, metric evaluation/state, Alloy delivery, Grafana queries, explicit probes, and authorized human access continue through authenticated paths; the future LogsQL evaluator, selected syslog relay, and flow sidecar have provisioned least-privilege route and credential fixtures before deployment; an unapproved ordinary pod and service account cannot use either the authenticated or temporary legacy route; the `host`-entity decision and node-privileged residual are explicit; pipeline dashboards and alerts remain green for the observation window; rollback grants access only to the named client; and the legacy route is absent after the rollback window.

#### Phase 4B — external logs and UniFi CEF

1. Preflight both sender and receiver capabilities, apply the exact source-to-VIP UniFi exception and receiver NetworkPolicy from the collector exposure table, and only then expose the source-restricted `externalTrafficPolicy: Local` syslog VIP. Where client authentication is required, deploy the Phase 0-selected rsyslog or syslog-ng mTLS terminator; its internal hop uses a credentialed HTTP write route if the relay converts to HTTP, or a dedicated NetworkPolicy-limited syslog listener otherwise. OSS VictoriaLogs alone is not claimed to provide syslog mTLS. Configure source-class listeners, `useRemoteIP`, stream fields, fixed enrichment, ignored fields, timestamp/timezone behavior, and TLS where supported. If no relay is selected, classify all external syslog as `transport_auth=none`. Give server-authenticated-TLS-only and unavoidable unauthenticated appliance/UDP sources dedicated listeners with ingress-derived identity and an explicit `transport_auth=none` field.
2. Forward selected PVE, Spark, and Synology logs using the fixture-tested mechanism chosen for each platform. PVE 9 requires either an explicit rsyslog installation or the selected journal-native path; Spark forwarding depends on the Phase 0 facility inventory; Synology uses its supported DSM log-forwarding facility.
3. Enable UniFi CEF activity export once and verify parsing, timestamps, remote identity, fields, and duplicate suppression.
4. Before adding a second evaluator, land a cluster-wide rendered-resource coverage check that enumerates rules in every namespace. Then change the existing metric vmalert from `selectAllByDefault: true` to `selectAllByDefault: false`, an explicit all-namespace `ruleNamespaceSelector: {}`, and the rule selector that excludes `observability.kelch.io/rule-datasource=vlogs`. Prove in CI and live rendered configuration that Longhorn, Kaniop, identity, AI, observability, and every other existing metric rule is selected exactly once, no vlogs fixture is selected, and metric alert state plus delivery remain stable through the observation window.
5. Deploy the selector-isolated LogsQL vmalert with VictoriaLogs datasource, VictoriaMetrics state storage, VMAlertmanager notifier, `evaluator=vmalert`, `signal=logs`, and a minimal first rule set. Prove the coverage check now assigns each metric or vlogs rule to exactly one evaluator before enabling notifications.
6. Exercise parser fixtures, malformed messages, canaries, heartbeats, clock skew, backpressure, sender loss, listener failover, unapproved-source rejection, and attempts to override ingress-derived identity from the payload. Record rather than conceal the residual same-source spoofing limitation of unauthenticated protocols.
7. Measure bytes/day and field/stream cardinality; retain operational logs for 30 days initially.

**Gate:** source loss, parser failure, clock skew, and delivery failure are themselves observable; HTTP readers/writers and internal relays use their authenticated paths, while unavoidable appliance syslog/CEF is admitted only through its explicitly unauthenticated, source-restricted listener with the spoofing residual recorded; the deployed LogsQL evaluator uses its authenticated query and state paths; and a synthetic LogsQL rule produces exactly one Pushover firing notification and one resolution through the non-null VMAlertmanager route.

### Phase 5 — sampled flows and SIEM-lite

1. Confirm the UDM Pro storage prerequisite and local Traffic Flows health, then capture and inventory a real IPFIX sample before finalizing the schema. Set the initial spool RPO to no more than five seconds of decoded records before persistence and at least one hour of measured peak backlog plus 20% headroom across a restart; revise those numbers only through a recorded capacity decision before deployment.
2. Deploy the separate short-retention VictoriaLogs flow instance, GoFlow2, its dedicated authenticated file-forwarding sidecar, a size-bounded Longhorn RWO PVC spool with five-second-or-better flush behavior, `logrotate` `copytruncate` rotation whose copy/truncate loss window is measured, retention and drain behavior, and a source-preserving VIP restricted to the UDM Pro. Keep operational container logs in the normal store and prove decoded payload does not leak there.
3. Enable sampled IPFIX and confirm template learning/expiry, abrupt pod restart and graceful-drain recovery, sequence/socket-drop behavior, bounded backlog preservation, sampler identity, field mapping, sampling-rate interpretation, `transport_auth=none`, and confinement to the trusted wired path. Drain and fail the receiver node, measure BGP withdrawal plus Longhorn detach/reattach through endpoint readiness, and record the entire interval as unavoidable UDP flow loss. Do not treat the permitted source address as cryptographic authenticity.
4. Measure volume, resource use, retention headroom, and query ergonomics before building the Routed Flows dashboard.
5. Add only same-store or derived-signal detections with a named response; label cross-store items as investigations rather than automatic joins.
6. Reassess whether VictoriaLogs remains sufficient. Promote to a specialized flow backend only on measured evidence.
7. Test rollback by disabling IPFIX at UniFi, verifying packets and template refresh stop, and removing the collector VIP, UniFi allow rule, and receiver NetworkPolicy so the flow store has no remaining writers. Stop and remove GoFlow2 and its file-forwarding sidecar in a separate reversible change, remove any defensive container-scoped payload exclusion only after their pods are gone, retain the isolated store until its short retention expires, and remove the store last.

**Gate:** the flow pipeline has a volume budget, privacy decision, retention boundary, spool size limit, explicit restart/drain RPO, rotation and sustained-input proof, pod-eviction headroom, and tested loss and disk-growth alerts.

### Phase 6 — enrichment after fundamentals

- Enable Hubble relay, UI, and flow/DNS/policy-drop metrics after the core estate is covered, using dashboard 16613 as source material. Cilium already supplies the privileged eBPF datapath, so basic Hubble does not create a new host trust boundary; L7 HTTP metrics require the Envoy path and remain a separate overhead/cardinality decision.
- Consider Beyla/OpenTelemetry OBI for service RED metrics or traces only when a named service has a concrete latency or trace-correlation gap. eBPF observes the wire rather than business semantics, TLS visibility is runtime-dependent, and neither Hubble nor Beyla is an anomaly engine.
- Revisit anomaly detection after normal alerts, dashboards, delivery, labels, and data quality are trustworthy. Trial Coroot Community only if its ClickHouse and second-UI footprint is justified by service-map and SLO value.
- Consider HolmesGPT with a SOPS-managed model credential for alert-triggered, evidence-backed RCA across Kubernetes, VictoriaMetrics, and VictoriaLogs. Optionally consider `grafana-llm-app` for panel explanation. AI-RCA remains downstream of reliable telemetry and may not silently mutate the estate.
- Treat Talos service and kernel log export as a separate guarded decision. `machine.logging.destinations` emits newline-delimited JSON over TCP/UDP rather than native syslog, so it needs a dedicated JSON-capable ingress and a manual Talos rollout with the repository's normal one-node-at-a-time validation; it is not part of Phase 4B.

## The Netdata and anomaly question

The original evaluation is retained because it explains why this plan does not install Netdata everywhere and identifies the still-open residual need.

- **Netdata Cloud remains rejected.** The evaluated free tier capped the local multi-node pane, the modern UI required unfree software, sensitive functions were tied to Cloud sign-in, and Cloud introduced metadata egress contrary to the self-hosted/GitOps posture.
- **Netdata Homelab was the buy-your-way-out option.** At the evaluated price of about $90/year it removed the fleet cap and VLAN aggregation problem and offered the lowest-effort blanket ML experience, but reopened the SaaS/egress decision. Price and product terms must be rechecked before any later comparison.
- **The free Netdata agent remains the residual technical option.** It was the only evaluated free, self-hosted blanket per-metric ML approach, but it adds a second agent/UI paradigm and can be noisy if every anomaly bit becomes an alert. If revisited, alert on node-level anomaly rate and curated alarms rather than every metric.
- **Coroot Community remains the first proposed experiment after fundamentals.** It can add service maps, inspections, and SLO/burn-rate heuristics over VictoriaMetrics, but adds ClickHouse and another UI; its free tier is not equivalent to Netdata’s per-metric ML, and AI-RCA capabilities may be commercial. Its Community MCP can still expose Coroot evidence to the chosen external reasoning client without adopting the commercial RCA tier.
- **vmanomaly and Grafana ML remained ruled out in the bake-off.** vmanomaly required VictoriaMetrics Enterprise licensing, while Grafana outlier/forecast/Sift features were Cloud-only. Recheck licensing before relying on this historical conclusion.

The decision is intentionally deferred: try deterministic alerts and Coroot only after Phases 1–5, then choose between accepting heuristic/SLO anomaly detection, running the free Netdata agent, or paying for a managed experience.

## Work packages

This is cross-cutting and should use a branch and PR. Keep deployment changes independently reversible:

1. **Planning and contracts:** this document, external inventory schema, label migration design, retention/cardinality budgets.
2. **Convergence and delivery:** corrected-log soak and #470 cleanup, execution of the estate-label migration, #218 stale-backup delivery exercise, external heartbeat receiver, and the edge-Pi Gatus change in `rpi-nixos`. The CRD, native scrape, rule, VMAlertmanager, Pushover, and datasource ownership transfers are completed baseline.
3. **External metrics:** central exporters, secrets, vmagent targets, PVE/Spark host bootstrap instructions, initial dashboards.
4. **Internal authentication and policy:** VMAuth or equivalent routes, per-client credentials, default-deny policy audit and enforcement, canaries, and rollback as a separately reversible change before external ingestion.
5. **External logs:** syslog ingress, PVE/Spark/Synology forwarding, UniFi CEF, LogsQL alerting.
6. **Flows and security:** GoFlow2, UniFi IPFIX, short-retention flow storage, security dashboards and detections.
7. **Optional enrichment:** Hubble, tracing, anomaly, and AI-RCA experiments.

Host and controller mutations are explicit manual steps after the relevant Git-managed receiver and rollback path exist. No plan phase should combine a destructive backend teardown with first-time collection from an external platform.

## Effort and day-two cost

The original plan estimated roughly one day for convergence, half a day for dashboards, half a day for estate coverage, and an afternoon for anomaly tooling. The dependency and security reviews invalidate that compression. Estimate by independently reviewable change and maintenance window instead:

- Planning, inventory, label/alert contracts, numeric budgets, and network design: one focused documentation/configuration pass before deployment.
- Remaining convergence: one corrected-log observation window and rollback-stack cleanup, followed independently by the estate-label migration and alert-resilience work. The CRD, delivery, rule/KSM, exporter, control-plane scrape, and datasource transfers are already complete.
- External metrics: one central-collector change plus bounded host/controller steps for PVE, Sparks, Synology, UniFi, Pis, and probes; commission one platform at a time.
- Dashboards and alerts: vendor upstream artifacts once their metrics exist, then budget deliberate curation and failure testing rather than assuming imports are complete.
- External logs and CEF: one receiver/security change plus per-platform sender configuration and parser fixtures.
- Flows/SIEM-lite: a separate capacity experiment with an explicit stop/go gate; it is not bundled into “turn on UniFi monitoring.”
- Hubble, tracing, anomaly, and AI-RCA: independent optional experiments after the base system is boring.

Day-two work should shrink after convergence but will not be zero: exporter/API compatibility across platform upgrades, credential rotation, dashboard pin refreshes, alert-noise tuning, retention/cardinality review, syslog and flow parser drift, Watchdog testing, and periodic restore/failure drills remain owned tasks.

## Open decisions and measurements

- Pushover is the tested page receiver. Decide which channel receives non-urgent security/capacity notifications, which channel receives review-only events, and how all delivery paths are exercised periodically.
- Exact Synology model/DSM MIB coverage and whether backup-task outcomes require API or log-derived monitoring beyond SNMP.
- Whether the offline UniFi Flex 2.5G 5 is active, spare, retired, or faulty.
- Actual UniFi CEF and IPFIX toggle state; the 2026-08-27 inspection was read-only and did not change or conclusively confirm those settings.
- Flow volume and retention sizing: the initial store is isolated at 7–14 days, but measured bytes/day determines its volume size, exact retention, and whether it remains viable.
- Whether inference is vLLM everywhere and which application metrics/labels are stable enough to normalize across model servers.
- Whether 30-day metric retention is enough. Revisit 90 days only after active-series and disk-growth measurements.
- Anomaly layer: Coroot Community remains the first experiment if a need remains after deterministic alerts; Netdata agent or paid Netdata are alternatives, not prerequisites.

## Out of scope or deferred

- Full packet capture, DPI outside UniFi, or storing packet payloads.
- Protect video, thumbnails, or motion imagery.
- Endpoint EDR, vulnerability management, and file-integrity monitoring.
- Per-token Prometheus series or high-cardinality request identifiers as labels.
- Long-term object-storage retention on Synology until restore, dependency, and failure-mode tests exist.
- A second dashboard UI or another metrics/log backend without a measured gap.
- Per-device or per-host dashboard copies when a variable-driven dashboard is sufficient.

## References

- [Observability bake-off](../observability-bakeoff.md)
- [Alerting runbook](../runbooks/alerting.md)
- [Convergence tracker](https://github.com/kelchm/home-lab/issues/470)
- [Alert delivery and dead-man tracker](https://github.com/kelchm/home-lab/issues/218)
- [PVE cluster plan](20260814-pve-cluster.md)
- [Network topology plan](20260821-network-topology.md)
- [DGX Spark bring-up runbook](../runbooks/dgx-spark-bringup.md)
- [NVIDIA DGX Spark Dashboard](https://docs.nvidia.com/dgx/dgx-spark/dgx-dashboard.html)
- [nv-monitor](https://github.com/wentbackward/nv-monitor)
- [DGX Spark Prometheus exporter and dashboard](https://github.com/ateska/dgx-spark-prometheus)
- [DGX Spark vLLM dashboard](https://github.com/darkmatter2222/DGX_Spark_Public_Docs)
- [vLLM metrics](https://docs.vllm.ai/en/stable/usage/metrics/)
- [Prometheus PVE exporter](https://github.com/prometheus-pve/prometheus-pve-exporter)
- [Unpoller](https://github.com/unpoller/unpoller)
- [UniFi System Logs and SIEM integration](https://help.ui.com/hc/en-us/articles/33349041044119-UniFi-System-Logs-SIEM-Integration)
- [UniFi Traffic Flows and Traffic Logging](https://help.ui.com/hc/en-us/articles/32201256219799-Traffic-Flows-and-Traffic-Logging-in-UniFi-Network)
- [GoFlow2](https://github.com/netsampler/goflow2)
- [VictoriaLogs](https://docs.victoriametrics.com/victorialogs/)
- [VictoriaLogs syslog ingestion](https://docs.victoriametrics.com/victorialogs/data-ingestion/syslog/)
- [VictoriaLogs alerting through vmalert](https://docs.victoriametrics.com/victorialogs/vmalert/)
- [Talos logging](https://docs.siderolabs.com/talos/v1.13/configure-your-talos-cluster/logging-and-telemetry/logging)
- [Cilium Hubble](https://docs.cilium.io/en/stable/observability/hubble/)
- [Grafana Beyla](https://grafana.com/docs/beyla/latest/)
- [Coroot Community](https://github.com/coroot/coroot)
- [HolmesGPT](https://github.com/robusta-dev/holmesgpt)
