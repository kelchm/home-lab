# Estate observability — converge the stack, cover the hardware, and make incidents navigable

**Status:** Active — 2026-08-27; revised from Kubernetes plus a few external targets into an estate-wide metrics, logs, flow, and security-observability design. Background: [observability bake-off](../observability-bakeoff.md).

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

The manifests still show the core shape of this baseline on 2026-08-27: both metric stacks and three log backends remain declared, Alloy still dual-writes, and vmalert still points at the KPS Alertmanager. Runtime sizes and counts must be re-baselined before implementation because the July measurements are evidence, not permanent facts.

### The reframe

The stated pain was “I do not have time to hand-build Grafana.” The live inventory showed the opposite problem: the estate already had 51 generated dashboards and 296 generated rules. The priorities are therefore:

1. Alert delivery is dead. Hundreds of evaluated rules that nobody receives provide little operational value.
2. Five backends and duplicate pipelines create maintenance cost without continuing bake-off value.
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

## Current state

### Central stack

- VictoriaMetrics k8s-stack is already deployed with a 30-second vmagent scrape interval, 30-day metric retention, and `externalLabels.cluster=k8s-prod`.
- VictoriaLogs is already deployed with 30-day retention and a 30 Gi volume.
- Alloy runs as a DaemonSet, tails Kubernetes CRI logs, labels them with namespace, pod, container, and node, and currently dual-writes to Loki and VictoriaLogs.
- Grafana already provisions dashboards from labelled ConfigMaps. Its sidecar supports the `grafana_folder` annotation and `foldersFromFilesStructure`; a Grafana operator is not required for repository-owned dashboards.
- Prometheus/KPS, Loki, and OpenObserve still exist as bake-off overlap. The convergence and dependency gates documented below remain necessary before removing them.
- Alert evaluation exists but delivery still needs to be made trustworthy and tested end to end.

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
- Alertmanager handles notification routing. It needs persistent storage, a stable Grafana datasource and route, real receivers, and a continuously checked Watchdog before dependent backends are retired.
- Metric and LogsQL rules use separate VMAlert instances. LogsQL rule sources carry `observability.kelch.io/rule-datasource=vlogs`; the vlogs evaluator positively selects that label, while the metric evaluator selects every rule except `vlogs`, preserving a safe default for future unlabeled metric rules. The label may live on a native VMRule, chart default-rule metadata, or PrometheusRule metadata preserved through vm-operator conversion; no mandatory resource rewrite is implied. Vlogs groups use `type: vlogs`, query VictoriaLogs, persist alert state through VictoriaMetrics remote-read/write, and notify the shared Alertmanager. A standing CI/rendered-config coverage check detects rules selected by zero or multiple evaluators.
- Native platform alerts remain enabled for failures that could take the central stack with them, including DSM disk/storage alerts, UniFi console notifications, and Proxmox cluster notifications.
- A healthchecks-style dead-man endpoint outside the estate receives the central Alertmanager Watchdog. One edge Pi runs a second small Gatus instance, sends its own heartbeat and direct alerts to a notification route that does not depend on `k8s-prod`, and probes a minimal set of observability, gateway, WAN, and critical-service outcomes without becoming another storage backend.

### Collection posture

- Pull slow-changing infrastructure metrics centrally at 30-second intervals by default.
- Scrape Spark GPU and inference-service metrics at 5 seconds where the added resolution changes diagnosis.
- Prefer a platform API or built-in SNMP/syslog export over installing a general telemetry agent.
- Use one narrowly scoped exporter on a host only when the platform does not expose the required data centrally.
- Do not run Prometheus, Grafana, Alloy, Loki, or an OpenTelemetry collector on either Spark.
- Do not install software on Synology or UniFi devices solely for this plan.

### Security posture

- General exporter listeners are reachable only from the three `k8s-prod` node addresses used by centrally scraped traffic. The Spark exception rejects direct routed access to `nv-monitor` and permits only the dedicated monitoring SSH principal from those node addresses for the pod-local tunnel instead. Target ports and positive and negative reachability tests are part of each collector’s acceptance gate; the current UniFi firewall matrix is intent, not a prerequisite assumed to exist.
- Proxmox and UniFi use local read-only service accounts or API tokens stored through the repository's existing SOPS path.
- Synology uses SNMPv3 rather than SNMPv2c.
- `nv-monitor` never receives a bearer token across a plaintext network hop. An extra container in the vmagent pod binds two forwards only to pod loopback, authenticates to each Spark's existing `sshd`, and reaches `nv-monitor` over Spark-local loopback; the host firewall rejects direct routed access to the exporter port. The token remains in a root-readable Spark environment file and a SOPS-managed vmagent scrape secret. The request crosses only pod loopback, the encrypted SSH connection, and Spark loopback. This preserves exactly one new monitoring binary on each Spark.
- Client names, MAC addresses, IP addresses, usernames, and flow tuples are not metric labels unless a bounded use case requires them. Full identity belongs in short-retention logs or flows, not permanent dashboard labels.
- Before external authentication logs, CEF, or flows are admitted, internal VictoriaMetrics and VictoriaLogs access moves behind VMAuth or equivalently authenticated read/write paths, and default-deny Cilium policies admit only Grafana, vmagent, vmalert, Alloy, the approved syslog/flow senders, and operator access.

### Reliability and capacity posture

- Time retention is not a capacity limit. Phase 0 sets numeric budgets for active series, series churn, samples per second, log and flow bytes per day, query latency, queue bytes/hours, and at least 20% free-space headroom on the 50 GiB metrics and 30 GiB operational-log volumes.
- vmagent’s current on-disk queue is an `emptyDir`, so a collector restart during a backend outage loses buffered data. Define its acceptable RPO, persistent-storage need, and `maxDiskUsagePerURL` before convergence.
- Alloy’s hostPath queue must be bounded and alerted so a VictoriaLogs outage cannot consume node disks after Loki is removed.
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

External push collectors use dedicated BGP LoadBalancer addresses from the restricted admin pool and `externalTrafficPolicy: Local` so the sender address survives Kubernetes ingress. A single-active receiver endpoint is scheduled with an availability policy appropriate to its storage and protocol; Cilium advertises the VIP only from nodes with a local ready endpoint. The design accepts a brief reconnection/template-learning interval during failover rather than hiding source identity behind cluster-mode SNAT.

UniFi rules allow only the documented sender addresses and ports: the UDM Pro for CEF and IPFIX, the three PVE hosts and both Sparks for their syslog listener, and the Synology for its listener. Kubernetes NetworkPolicies repeat those allowlists at the receiver. General external scraping is the reverse path: the three `k8s-prod` node addresses are allowed to each declared exporter/SNMP port, with every other source denied. `nv-monitor` is the exception: the nodes may reach Spark SSH for the pod-local forward but may not reach the exporter port directly.

VictoriaLogs uses separate source-class listeners where enrichment differs. Each listener explicitly configures TCP/TLS or UDP, `useRemoteIP`, `streamFields`, fixed extra fields such as `site` and `platform`, ignored fields, timestamp behavior, and timezone. Controllable host forwarders use TLS and sender authentication through a capable ingress or local relay where supported; server-authenticated TLS alone encrypts transport but does not authenticate the sender. Appliance exports that offer only unauthenticated syslog or UDP receive a dedicated listener and an ingress-derived `source_identity` plus `transport_auth=none`; payload hostname or source fields cannot override that identity. Their path must remain inside the trusted wired estate, and their events are advisory rather than the sole authority for destructive automation. Parser fixtures cover UniFi CEF, PVE, Spark, and Synology messages; known-event canaries and sender heartbeats make quiet-source loss distinguishable from “nothing happened.”

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

The current global `externalLabels.cluster=k8s-prod` is unsafe once vmagent scrapes the rest of the estate, and vmalert independently applies the same global label to every alert. Migrate both components: add `site=home`, explicitly label Kubernetes jobs and rules with `cluster=k8s-prod`, label clustered external targets with their real cluster, omit `cluster` for standalone systems, and then remove both global cluster defaults. Gate the migration on dashboard and recording-rule compatibility and zero unexpected `exported_cluster`, missing-identity, or wrongly stamped alert series.

### Log fields and streams

Normalize at least `site`, `environment`, `domain`, `platform`, `host`, `service`, `severity`, and `event_type` when the source supplies them reliably.

Only stable, low-cardinality fields become VictoriaLogs stream fields. Source/destination IPs, ports, client MACs, usernames, trace IDs, request IDs, model request IDs, and free-form event names remain ordinary searchable fields. This prevents a stream explosion while retaining security and diagnostic detail.

### Inventory and desired state

The repository must hold a non-secret external-target inventory with address, platform, role, scrape class, expected state, criticality, maintenance window, credential reference, firewall-policy reference, ports, expected metric families, probe vantages, owner, and runbook. Credentials remain in SOPS secrets. The inventory distinguishes active, absent, expected-offline, maintenance, and retired equipment so alerts describe drift from intent rather than raw discovery state.

## Collection plan by platform

### Kubernetes

- Retain the VictoriaMetrics k8s-stack, kube-state-metrics, node-exporter, platform ServiceMonitors, and Alloy CRI collection.
- Complete the existing KPS dependency decoupling before removing KPS-owned components or CRDs.
- Add the missing curated dashboards for Longhorn, Traefik, cert-manager, Flux, Alloy, and the observability pipeline itself; preserve and extend the curated Longhorn alerts already present in the repository.
- Keep the current 30-second platform scrape cadence unless a specific alert or dashboard requires otherwise.
- Turn on Hubble metrics only after the core estate is covered. L7 visibility remains a separate decision because it changes the datapath and cardinality profile.
- Treat direct etcd, scheduler, and controller-manager coverage as partial. Both metric stacks disable those targets because Talos binds them locally. A later Talos metrics-exposure change requires its own guarded configuration rollout; until then, dashboards must not claim direct quorum or component metrics that do not exist.

The original component research remains useful source material:

- cert-manager: dashboard 11001 and its certificate-expiry rules.
- Alloy: the rendered Alloy mixin dashboards and alerts, which corrected the bake-off’s original assumption that Alloy had no upstream dashboard.
- Flux: the `flux2-monitoring-example` dashboards; its alerts are example-grade and its resource-state panels may require kube-state-metrics CustomResourceState configuration.
- Traefik: dashboard 17347 as a starting point, with the known absence of Gateway API-specific coverage.
- Longhorn: dashboard 16888 remains useful source material. Ten curated Longhorn backup, health, capacity, and metrics-missing alerts already exist in-repo, so example rules are comparison material rather than an alert gap to re-import.

These references are discovery inputs, not runtime dependencies. Vendor the useful JSON and rules, pin them in Git, remove mutable chart-sync sources once replaced, and validate unique dashboard UID, title, folder, and datasource combinations.

### Proxmox `pve-sbx`

- Run one `node_exporter` systemd service on each of the three PVE hosts for Linux CPU, memory, filesystem, disk, thermal, and interface metrics.
- Run `prometheus-pve-exporter` centrally in Kubernetes. Use one `pve-cluster` scrape class with cluster metrics enabled and node metrics disabled against one healthy/failover API target, plus a `pve-node` scrape class with cluster metrics disabled and node metrics enabled against all three nodes. This avoids duplicated cluster series while retaining per-node API state.
- Use a least-privilege Proxmox API token and validate its behavior during quorum loss and when one node is unreachable.
- PVE 9 is journald-first and does not provide an existing rsyslog path to assume. Forward selected `pvedaemon`, `pveproxy`, `pvestatd`, Corosync, kernel, ZFS/storage, backup, authentication, and systemd failure events only after Phase 0 chooses and tests either an explicit rsyslog installation or a journal-native forwarder/receiver path.
- Keep guest monitoring service-oriented. Do not install a node exporter in every VM merely because the VM exists.
- Alert on quorum risk, node loss, Corosync degradation, storage pressure, host temperature, interface errors, exporter API failure/staleness, and guest state only where desired state says the guest should be running.
- Keep native PVE notifications authoritative for backup and task outcomes. Add central backup/task alerts only after a real log or API fixture proves the relevant result fields; the exporter’s object-state metrics must not be described as task-history coverage.

### DGX Sparks

Start with exactly one new monitoring binary on each Spark: `nv-monitor` in headless Prometheus mode under systemd. It is purpose-built for Grace/GB10, handles unified memory and ARM core topology, and supplies useful CPU, memory, filesystem, GPU, aggregate network, and endpoint RDMA telemetry with no runtime dependency beyond the existing NVIDIA driver/NVML path.

The only other metric endpoints are supplied by software already serving inference. vLLM or another inference server exposes its own `/metrics`; scraping it does not add a telemetry daemon. The initial cadence is 5 seconds for `nv-monitor` and active inference endpoints, with 30 seconds for lower-value host checks if separate jobs are needed.

Use journald and any already-installed syslog facility for selected OOM, kernel, NVIDIA driver/XID, systemd service, SSH, sudo, inference-service, storage-mount, and RDMA link events. Do not assume rsyslog exists, and do not install Alloy, Prometheus, Grafana, DCGM, a container monitoring stack, or a general tracing collector on the Sparks in the initial design.

`nv-monitor` is not a complete Linux host exporter. It does not establish full SMART/NVMe health, service state, NFS mount state, per-interface link/error health, fabric peer identity, topology, or congestion. Before building an alert or panel, map it to an observed metric or log field in a Spark coverage matrix. Preserve the one-binary posture by accepting or log-checking non-critical gaps first; add a restricted node-exporter/textfile path only if a required alert cannot be sourced otherwise.

The private `198.19.240.0/24` and `198.19.241.0/24` Spark fabric never reaches UniFi and is not covered by gateway flows. Use endpoint RDMA state/rate/byte/packet/error counters for basic link and asymmetry detection, while treating peer/topology/congestion diagnosis as deferred.

The systemd unit sets `nv-monitor`’s internal refresh explicitly rather than assuming the 5-second Prometheus scrape controls its own sampling loop. Acceptance records exporter CPU/memory, scrape duration and timeout rate, and verifies no material TTFT or queue regression under the repository’s known three-session inference load.

Because `nv-monitor` intentionally omits TLS, a declared extra container in the vmagent pod maintains one SSH local forward to each Spark using a dedicated key and an account restricted to forwarding to Spark loopback `:9101`. Both forward listeners bind only to the shared pod loopback namespace, so vmagent's bearer-authenticated HTTP request reaches the tunnel without crossing the cluster network in plaintext. The dedicated principal accepts SSH from the three node-SNAT addresses while the Sparks reject direct routed access to the exporter port. Test `authorized_keys`/`sshd` forwarding restrictions, reconnect, host-key pinning, credential rotation, tunnel failure alerting, and five-second scrape behavior during a tunnel restart. If a reliable restricted SSH-forward account cannot be established, use a host TLS proxy and count it as an explicit exception to the one-binary posture rather than exposing a bearer token over HTTP.

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
- Preserve native DSM alerts for disk, RAID, volume, power, and backup failures because the NAS may outlive or host dependencies of the central stack.
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

#### Routed flows

- Send the UDM Pro's sampled NetFlow/IPFIX export to a small GoFlow2 Deployment in Kubernetes.
- Require a UniFi flow preflight before deployment: confirm Network and UniFi OS version compatibility, installed console storage and Traffic Flows availability, the current toggle state, and healthy local flow capture. The UDM Pro meets the documented software floor; its storage prerequisite remains unverified.
- Restrict the UDP collector to the gateway source address and expose GoFlow2's own Prometheus health, socket-drop, sequence-gap, template, and decode-failure signals where available. Native IPFIX-over-UDP is neither encrypted nor sender-authenticated; the allowlist constrains reachability but does not prove record origin. Keep the first hop inside the trusted wired estate, derive `source_identity=udm-pro-flows` at ingress, mark records `transport_auth=none`, and disable export or add network-level encrypted transport before any future path crosses an untrusted network.
- Capture a real UDM Pro sample before designing fields or detections. Inventory exported templates and fields, verify sampling-rate interpretation, and commit a GoFlow2 mapping when vendor fields require one. Do not assume that the export includes the richer allowed/blocked/risk/policy metadata visible in UniFi's local flow UI.
- Preserve each decoded flow as JSON. Decoded source/destination addresses, ports, protocols, byte counts, and sampling metadata remain searchable fields, not stream labels. Treat them as advisory until corroborated; absence from a sampled feed is never evidence that communication did not occur.
- Start with the controller's conservative sampling and measure events/day, bytes/day, ingest CPU, query latency, and useful detections before changing sampling.
- Use a separate small VictoriaLogs flow instance with 7–14-day retention so unknown flow volume cannot evict operational logs. GoFlow2 writes decoded JSON to a shared file rather than stdout, and a dedicated central sidecar forwards only that file to the flow instance. Preserve GoFlow2 and sidecar operational stdout/stderr in the 30-day store. Acceptance proves decoded payload never appears in CRI logs; add a narrowly container-scoped Alloy exclusion only as defense in depth if the selected image can emit payload to stdout.
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

The deployed sidecar only creates the folder structure when ConfigMaps carry the expected annotation/path; chart-generated dashboards currently do not obey this governance uniformly. Dashboard CI must validate unique UID/title/folder/datasource combinations, parse queries, detect missing datasources and common no-data errors, and ensure alert annotations point at valid dashboards and log queries. Set Estate Overview as Grafana’s actual home dashboard. Once repository-owned copies replace upstream artifacts, disable mutable `main`/`master` dashboard sync jobs.

Datasource convergence uses stable UIDs and explicit pruning. Transfer the default Prometheus UID from KPS to VictoriaMetrics deliberately, add the replacement Alertmanager datasource and route, update every provisioned dashboard reference, and use Grafana provisioning `prune`/`deleteDatasources` behavior so stale KPS datasources cannot survive silently.

## Alerting strategy

Every rule carries a common alert contract: `severity`, `signal`, `site`, `platform`, optional real `cluster`, and the applicable `host` or `service`, plus owner, runbook, dashboard, and log-query annotations. Alertmanager grouping, inhibition, send-resolved behavior, retry behavior, and page/notify/review routes are part of this contract rather than dashboard conventions.

### First alerts

- Alert delivery pipeline unavailable or untested.
- VictoriaMetrics/VictoriaLogs ingestion failure, exporter scrape failure, or unexpected cardinality/volume growth.
- Kubernetes API/node, Longhorn, ingress, certificate, and GitOps failures after default-rule noise is curated; direct etcd/scheduler/controller-manager alerts wait for a real Talos metric source.
- Proxmox quorum risk, Corosync link degradation, node loss, storage exhaustion, and thermal or NIC faults; native PVE notifications remain authoritative for failed backups until central task-result ingestion is proven.
- Spark unavailable, sustained thermal/power throttle, unified-memory pressure, disk pressure, inference endpoint failure, queue saturation, severe TTFT regression, and RDMA link/error problems.
- Synology disk/RAID/volume degradation, capacity risk, interface loss, and failed NFS/SMB/backup outcome.
- UniFi controller/gateway failure, WAN outage/failover, device offline relative to desired state, uplink downgrade, LAG member loss, port errors, PoE budget/underpower, and sustained AP retry or channel-utilization problems.
- Security events with a concrete response: privileged admin change, IPS/honeypot event, repeated authentication failure, high-rate cross-zone block, or an unexpected communication path.

### Noise controls

- Do not alert on generic `error` strings or every nonzero CEF severity.
- Disable unwanted chart defaults through reviewed values such as `defaultRules.disabled.<AlertName>` rather than deleting rendered rules or maintaining ad hoc negative copies.
- Use `for` durations and maintenance/desired-state labels to prevent discovery churn from paging.
- Separate page-worthy availability/data-loss alerts from informational capacity and security-review notifications.
- Keep native UniFi, DSM, and Proxmox notifications for central-stack-independent emergencies, but document the owner of each duplicate notification.
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
3. Re-baseline active series, churn, samples/sec, VictoriaMetrics disk growth, VictoriaLogs bytes/day, current alert volume, query latency, queue use, and free-space headroom; set numeric stop/go budgets for each new source.
4. Define page, notify, and review destinations and conduct a delivery test before adding more rules.
5. Design the vmagent and vmalert `cluster=k8s-prod` external-label migration and its dashboard, recording-rule, alert, and historical-query compatibility matrix.
6. Define the scraper source addresses, exporter/SNMP ports, collector VIPs, `externalTrafficPolicy`, UniFi rules, Cilium policies, and positive/negative network tests.
7. Extend SOPS path rules if encrypted host-side material will live outside the existing Kubernetes/bootstrap/Talos paths; central exporter credentials remain Kubernetes SOPS Secrets, while Spark tokens use root-readable host environment files provisioned through the chosen host-management path.
8. Choose and fixture the external journal-forwarding mechanism for PVE 9 and confirm which syslog facility, if any, is already present on the Sparks.
9. Record a transport-capability and trust matrix for every pushed source: protocol, encryption, sender authentication, network path, ingress-derived identity, spoofing limitation, credential owner, and whether the signal may stand alone for alerting or automation.

**Gate:** no external scrape target is added until it receives unambiguous `site`, `platform`, and `host` labels, plus `cluster` only when it belongs to a real cluster. Numeric budgets, credentials, firewall paths, expected metrics, and independent reachability are recorded first.

### Phase 1 — finish convergence and alert delivery

Convergence is not “delete KPS.” The original 2026-07-03 repository and live-provenance review found five direct couplings, and the 2026-08-27 review found additional Flux and configuration ownership that must move:

1. **kube-state-metrics:** current `kube_*` series come from the KPS-owned service. The VM chart currently disables its copy. Its replacement must preserve job labels and KPS's `metricLabelsAllowlist` entry for the PVC label `recurring-job-group.longhorn.io/no-backup`, which the Longhorn backup-exemption alerts consume. This is not the separate CustomResourceState mechanism used for Flux metrics.
2. **node-exporter:** current `node_*` series come from the KPS-owned DaemonSet. Enabling both copies can produce port/scheduling conflicts and duplicate targets, so perform a no-overlap handoff rather than flipping both chart flags in one final state.
3. **Prometheus Operator CRDs:** KPS owns the live `monitoring.coreos.com` CRDs and bootstrap also sources them from KPS. Third-party charts emit ServiceMonitor objects consumed by vm-operator conversion. Install a standalone `prometheus-operator-crds` release and SSA-adopt ownership before KPS can prune the CRDs or their instances.
4. **Alertmanager:** vmalert points to `kube-prometheus-stack-alertmanager:9093`, which has no working external receiver. Provision the replacement, persistence, UI/datasource route, receivers, and external Watchdog before repointing evaluators.
5. **Flux dependencies:** VictoriaMetrics, smartctl-exporter, and grafana-mcp currently depend on KPS. Alloy depends on both Loki and VictoriaLogs. Rewire every dependency before deleting a producer; changing only the VictoriaMetrics Kustomization is insufficient.
6. **Rule sources:** KPS’s app Kustomization owns custom node rules, but Longhorn and grafana-mcp/AI also own PrometheusRules and the VictoriaMetrics chart renders default VMRules. Inventory and preserve every source, then prove every resulting evaluator rule is selected exactly once.
7. **Grafana ownership:** KPS owns the current default Prometheus and Alertmanager datasource UIDs. Transfer stable ownership, validate every provisioned dashboard, and explicitly prune stale datasources rather than assuming removed files delete them.

Flux does not execute this list as a transaction. Each destructive transition therefore lands as its own merged checkpoint:

#### Phase 1A — CRDs and dependency graph

- Install and adopt standalone Prometheus Operator CRDs, update bootstrap, inventory every ServiceMonitor/PodMonitor and Flux `dependsOn`, and verify no object was recreated or pruned.
- Rewire dependencies that do not require a backend cutover.

**Gate:** CRD ownership is independent of KPS and all monitoring custom resources remain present and reconciled.

#### Phase 1B — alert delivery and dead-man

- Provision a persistent VM-owned or standalone Alertmanager, stable datasource and route, actual page/notify/review receivers, send-resolved and retry policy, grouping/inhibition, and a Watchdog checked from outside `k8s-prod`.
- Provision the external dead-man endpoint, route Alertmanager's Watchdog heartbeat to it, and add the edge-Pi Gatus configuration in the `rpi-nixos` repository with its own heartbeat and direct external notification path. Neither missed-heartbeat check may depend on Grafana, VictoriaMetrics, VictoriaLogs, or the central Alertmanager for delivery.
- Repoint the existing metric evaluator and test a controlled firing and resolution before adding new rules.
- Add the minimal Alert Delivery and Observability Pipeline dashboard now rather than waiting for the general dashboard phase.

**Gate:** a synthetic alert reaches the intended receiver, resolves, and appears in Grafana; blocking the central Watchdog causes the external dead-man alert; blocking the Pi heartbeat also alerts externally; and a Pi-side probe failure delivers without the central Alertmanager.

#### Phase 1C — rule and KSM configuration ownership

- Move KPS-owned custom node rules and the kube-state-metrics Longhorn allowlist into replacement ownership.
- Inventory chart `defaultRules` labels, KPS node PrometheusRules, Longhorn PrometheusRules, grafana-mcp/AI PrometheusRules, and any other converted or native rules.
- Label only LogsQL sources as `vlogs`; configure the metric evaluator to exclude `vlogs` and the vlogs evaluator to select it positively before the second evaluator starts.
- Add a standing CI/rendered-config zero-or-multiple-evaluator coverage check, then compare rule counts, generated evaluator configs, and representative query results.

**Gate:** every rule is selected once, Longhorn backup exemptions still work, and no rule disappears with KPS disabled in a rendered test.

#### Phase 1D — no-overlap KSM and node-exporter handoff

- Replace kube-state-metrics and node-exporter one component at a time, preserving expected job and identity labels without running conflicting DaemonSets or duplicate scrape targets.
- Record the brief expected scrape gap and validate Kubernetes/node dashboards, alerts, and recording rules across it.

**Gate:** metric and label continuity is understood, duplicate targets are zero, and the replacement components are healthy.

#### Phase 1E — Grafana datasource handoff

- Transfer the default Prometheus and Alertmanager datasource UIDs, update dashboard mappings, and validate all provisioned dashboard UID/title/folder/datasource combinations. Estate Overview becomes Grafana home only after Phase 3 provisions and validates it.
- Exercise explicit `prune`/`deleteDatasources` behavior for the KPS datasources in a rendered or staged test.

**Gate:** every dashboard and alert link uses the intended datasource and no stale KPS default remains.

#### Phase 1F — estate label migration

- Stamp `site=home` and `cluster=k8s-prod` explicitly on Kubernetes scrape and rule sources, add each real external cluster only at its own future target, and leave standalone target contracts without `cluster`.
- Remove `externalLabels.cluster=k8s-prod` from both vmagent and vmalert only after rendered configurations, dashboards, recording rules, alerts, and representative historical queries are compatible. Preserve `site=home` globally only if the rendered test proves it cannot overwrite source identity.
- Check newly ingested samples and alerts for unexpected `exported_cluster`, missing `site`/`cluster` on Kubernetes data, or `cluster=k8s-prod` on standalone identities before Phase 2 adds an external target.

**Gate:** Kubernetes series and alerts retain correct identity without either global cluster default; standalone target fixtures remain clusterless; PVE fixtures carry only `cluster=pve-sbx`; and representative dashboards, rules, alerts, and historical queries pass the compatibility matrix defined in Phase 0.

#### Phase 1G — remove OpenObserve

- Confirm again that nothing writes to, queries, or links to OpenObserve; retain any explicitly wanted evidence; remove it in its own reversible Git change.
- The original estimate was that retiring OpenObserve, Loki, and the duplicate KPS metric backend would reclaim about 1.9 GiB plus three upgrade surfaces; remeasure runtime use before quoting the final savings.

#### Phase 1H — remove Loki

- Prove VictoriaLogs completeness and query behavior, bound Alloy buffering, remove Alloy’s Loki sink and its Flux dependency first, then remove Loki in a later merge.
- Name the rollback/data-retention window before pruning its PVC, accounting for the chart behavior that caused the bake-off data-loss finding.

#### Phase 1I — remove KPS last

- Remove KPS only after CRDs, dependencies, custom rules, KSM/node-exporter, Alertmanager, and Grafana datasource ownership have all moved and remained green for a named observation window.
- Snapshot or explicitly abandon the old Prometheus and Alertmanager data with a documented rollback window; remove stale routes, datasources, and PVCs deliberately.

Expected repository areas include `victoria-metrics-k8s-stack/app/helmrelease.yaml`, `victoria-metrics-k8s-stack/ks.yaml`, the new standalone CRD release, `bootstrap/helmfile.d/00-crds.yaml`, KPS-owned rule and KSM configuration, smartctl/grafana-mcp/Alloy dependencies, Grafana datasource provisioning, and finally the KPS, Loki, and OpenObserve application trees.

**Phase 1 exit gate:** alert delivery and external dead-man work; CRD, rule, dashboard, datasource, and estate-label ownership is independent of KPS; Kubernetes metric continuity is accepted; VictoriaLogs ingestion and buffering meet the numeric budget; and every backend removal was a separate merge with a tested rollback boundary.

### Phase 2 — low-cost estate metrics

1. Deploy central `prometheus-pve-exporter`, `snmp_exporter`, Unpoller, blackbox exporter/Gatus, and their least-privilege secrets.
2. Install `node_exporter` on the three PVE hosts.
3. Install the single `nv-monitor` binary and systemd unit on both Sparks, add the pod-loopback SSH-tunnel extra container to vmagent with pinned host keys, add a dedicated forwarding-only monitoring account restricted to the three node-SNAT addresses and Spark loopback `:9101`, leave existing operator/automation SSH access unchanged, block direct routed access to the exporter port, and enable the required inference `/metrics` endpoints.
4. Enable SNMPv3 on Synology and validate actual MIB coverage.
5. Apply the narrowly scoped UniFi collector/exporter firewall rules and validate them from the actual scraper pods and an unapproved source.
6. Add external vmagent targets using the Phase 0 label contract and the platform-specific scrape classes only after the Phase 1F label-migration gate passes.
7. Measure scrape duration, API-poll success and freshness, active-series growth, host/controller overhead, and data freshness before enabling alerts. `up=1` alone proves only that the exporter endpoint answered.

**Gate:** every target has an exporter-health signal, source-poll success/freshness where applicable, independent reachability, stable identity, bounded series count, documented credential ownership, and a coverage matrix stating what remains unmonitored. Spark acceptance also proves direct exporter access is denied, the authenticated tunnel reconnects, and no bearer token crosses plaintext transport.

### Phase 3 — dashboards and metric alerts

1. Keep the ConfigMap sidecar and create the folder hierarchy above.
2. Vendor and pin useful upstream dashboard JSON; adapt datasource UIDs and variable names in Git.
3. Build Estate Overview and one curated overview for each platform using `estate_*` recording rules where normalization is valuable.
4. Validate Estate Overview and then set its stable UID as Grafana’s home dashboard.
5. Add platform alerts in small batches, test controlled failures, and record useful links/runbooks.
6. Disable mutable upstream dashboard sync only after repository-owned replacements exist.
7. Run dashboard CI for UID/title/folder/datasource uniqueness, query validity, representative no-data behavior, and alert links.

**Gate:** an operator can start at a delivered alert, identify the affected platform or service, and reach supporting metrics in two dashboard hops or fewer.

### Phase 4 — authenticated internal paths, external logs, and UniFi CEF

#### Phase 4A — internal authentication and policy cutover

1. Inventory every VictoriaMetrics and VictoriaLogs reader and writer: vmagent remote-write, metric and LogsQL vmalert query/state paths, Alloy, Grafana datasources, operators, probes, and human access.
2. Provision VMAuth or equivalent authenticated routes and per-client credentials alongside the existing paths. Move one client at a time, test its canary and failure behavior, and keep the old route available for the named rollback window.
3. Audit default-deny Cilium policies before enforcing them, then prove each intended flow and a representative denied flow. Do not combine first policy enforcement with external log admission.

**Gate:** vmagent ingestion, the metric evaluator, Alloy delivery, Grafana queries, operator reconciliation, every other currently deployed client, and authorized human access continue through authenticated paths; the future LogsQL evaluator has a provisioned least-privilege route and credential fixture; denied clients fail closed; pipeline dashboards and alerts remain green for the observation window; and reverting routes and policies restores the prior path.

#### Phase 4B — external logs and UniFi CEF

1. Preflight both sender and receiver capabilities, provision a capable authenticating ingress or local relay where client authentication is supported, then expose the source-restricted `externalTrafficPolicy: Local` syslog VIP. Configure source-class listeners, `useRemoteIP`, stream fields, fixed enrichment, ignored fields, timestamp/timezone behavior, and TLS where supported. Give server-authenticated-TLS-only and unavoidable unauthenticated appliance/UDP sources dedicated listeners with ingress-derived identity and an explicit `transport_auth=none` field.
2. Forward selected PVE, Spark, and Synology logs using their built-in facilities.
3. Enable UniFi CEF activity export once and verify parsing, timestamps, remote identity, fields, and duplicate suppression.
4. Deploy the selector-isolated LogsQL vmalert with VictoriaLogs datasource, VictoriaMetrics state storage, shared Alertmanager notifier, and a minimal first rule set.
5. Exercise parser fixtures, malformed messages, canaries, heartbeats, clock skew, backpressure, sender loss, listener failover, unapproved-source rejection, and attempts to override ingress-derived identity from the payload. Record rather than conceal the residual same-source spoofing limitation of unauthenticated protocols.
6. Measure bytes/day and field/stream cardinality; retain operational logs for 30 days initially.

**Gate:** source loss, parser failure, clock skew, and delivery failure are themselves observable; the deployed LogsQL evaluator uses its authenticated query and state paths; and no external source bypasses the authenticated internal read/write boundary.

### Phase 5 — sampled flows and SIEM-lite

1. Confirm the UDM Pro storage prerequisite and local Traffic Flows health, then capture and inventory a real IPFIX sample before finalizing the schema.
2. Deploy the separate short-retention VictoriaLogs flow instance, GoFlow2, its dedicated file-forwarding sidecar, and a source-preserving VIP restricted to the UDM Pro. Keep operational container logs in the normal store and prove decoded payload does not leak there.
3. Enable sampled IPFIX and confirm template learning/expiry, restart recovery, sequence/socket-drop behavior, sampler identity, field mapping, sampling-rate interpretation, `transport_auth=none`, and confinement to the trusted wired path. Do not treat the permitted source address as cryptographic authenticity.
4. Measure volume, resource use, retention headroom, and query ergonomics before building the Routed Flows dashboard.
5. Add only same-store or derived-signal detections with a named response; label cross-store items as investigations rather than automatic joins.
6. Reassess whether VictoriaLogs remains sufficient. Promote to a specialized flow backend only on measured evidence.
7. Test rollback by disabling IPFIX at UniFi, verifying packets and template refresh stop, and removing the collector VIP, UniFi allow rule, and receiver NetworkPolicy so the flow store has no remaining writers. Stop and remove GoFlow2 and its file-forwarding sidecar in a separate reversible change, remove any defensive container-scoped payload exclusion only after their pods are gone, retain the isolated store until its short retention expires, and remove the store last.

**Gate:** the flow pipeline has a volume budget, privacy decision, retention boundary, and tested loss alert.

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
2. **Convergence and delivery:** KPS decoupling, Alertmanager, datasource migration, execution of the estate-label migration, external heartbeat receiver, edge-Pi Gatus change in `rpi-nixos`, and retirement of redundant backends.
3. **External metrics:** central exporters, secrets, vmagent targets, PVE/Spark host bootstrap instructions, initial dashboards.
4. **Internal authentication and policy:** VMAuth or equivalent routes, per-client credentials, default-deny policy audit and enforcement, canaries, and rollback as a separately reversible change before external ingestion.
5. **External logs:** syslog ingress, PVE/Spark/Synology forwarding, UniFi CEF, LogsQL alerting.
6. **Flows and security:** GoFlow2, UniFi IPFIX, short-retention flow storage, security dashboards and detections.
7. **Optional enrichment:** Hubble, tracing, anomaly, and AI-RCA experiments.

Host and controller mutations are explicit manual steps after the relevant Git-managed receiver and rollback path exist. No plan phase should combine a destructive backend teardown with first-time collection from an external platform.

## Effort and day-two cost

The original plan estimated roughly one day for convergence, half a day for dashboards, half a day for estate coverage, and an afternoon for anomaly tooling. The dependency and security reviews invalidate that compression. Estimate by independently reviewable change and maintenance window instead:

- Planning, inventory, label/alert contracts, numeric budgets, and network design: one focused documentation/configuration pass before deployment.
- Convergence: multiple PRs and observation windows across CRDs, delivery, rule/KSM ownership, exporter handoff, datasources, and three separate backend removals. Calendar time is dominated by safe observation, not typing YAML.
- External metrics: one central-collector change plus bounded host/controller steps for PVE, Sparks, Synology, UniFi, Pis, and probes; commission one platform at a time.
- Dashboards and alerts: vendor upstream artifacts once their metrics exist, then budget deliberate curation and failure testing rather than assuming imports are complete.
- External logs and CEF: one receiver/security change plus per-platform sender configuration and parser fixtures.
- Flows/SIEM-lite: a separate capacity experiment with an explicit stop/go gate; it is not bundled into “turn on UniFi monitoring.”
- Hubble, tracing, anomaly, and AI-RCA: independent optional experiments after the base system is boring.

Day-two work should shrink after convergence but will not be zero: exporter/API compatibility across platform upgrades, credential rotation, dashboard pin refreshes, alert-noise tuning, retention/cardinality review, syslog and flow parser drift, Watchdog testing, and periodic restore/failure drills remain owned tasks.

## Open decisions and measurements

- Alert receivers and escalation semantics: which channel pages immediately, which channel receives non-urgent security/capacity notifications, and how delivery is tested periodically.
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
