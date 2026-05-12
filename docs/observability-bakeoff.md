# Observability stack bake-off

## Why

The cluster currently runs OpenObserve as a unified observability stack
(metrics + logs + traces, single binary, local-disk storage). OpenObserve
is functionally fine but requires significant manual work to be useful in a
Kubernetes context — no pre-baked dashboards, no auto-discovered scrape
configs, manual rule authoring. The Grafana ecosystem ships with
substantially more out-of-the-box integration.

After working through criteria around ergonomics, retention, and ingest
robustness when the Synology is unavailable, OpenObserve fell out of
contention on every axis. The remaining real comparison is between two
Grafana-front-door stacks:

- **kube-prometheus-stack + Loki** — most ecosystem-pre-baked artifacts
  (dashboards, ServiceMonitor-emitting Helm charts), but Loki has a real
  reputation for day-2 operational pain.
- **VictoriaMetrics + VictoriaLogs** — better ingest robustness
  (`vmagent` persistent queue, ~10× compression), simpler operational
  story, but materially fewer pre-built dashboards/queries on the
  VictoriaLogs side.

This doc is the canonical record of the bake-off going forward. Each
commit on the bake-off branch references it; findings are appended below
as they accumulate; the final decision lands in this same doc.

## Stacks under test

Both pipelines run in parallel in the `observability` namespace, sharing
a single standalone Grafana instance as the front-door UI. OpenObserve
remains running but is not part of the evaluation.

### Pipeline A — kube-prometheus-stack + Loki

- `prometheus-community/kube-prometheus-stack` with `grafana.enabled=false`
- Prometheus scrapes `ServiceMonitor`s → local TSDB on Longhorn (30 d retention)
- Alertmanager (no notification target wired during the bake-off; rules
  evaluate, alerts visible in AM UI but do not deliver)
- kube-state-metrics, node-exporter
- ~30 default dashboards published as ConfigMaps for the standalone Grafana
- ~100 default `PrometheusRule`s
- Loki in single-binary mode, filesystem store on Longhorn
- Alloy DaemonSet → Loki via Loki ingestion API

`kubeControllerManager` / `kubeScheduler` / `kubeEtcd` scrape disabled in
KPS — Talos binds these to localhost by default and surfacing them
requires a Talos config change we declined to take during the bake-off.

### Pipeline B — VictoriaMetrics + VictoriaLogs

- `vm/victoria-metrics-k8s-stack` (operator-based)
- VMSingle on Longhorn
- vmagent scraping the same `ServiceMonitor` resources via the operator's
  compatibility layer
- vmalert for rule evaluation
- VLSingle on Longhorn
- **Same Alloy DaemonSet** as Pipeline A — Alloy dual-writes to Loki and
  to VL via the Loki ingestion API. Single agent, byte-identical input
  on both log backends.

Differences in ingest path are only on the metrics side: `vmagent` and
Prometheus scrape independently. `vmagent`'s persistent queue is part of
the VM defensive value prop and is in scope for the comparison.

## Evaluation criteria

| Axis | What to capture |
|---|---|
| Dashboards out of box | How many surfaces (cluster, node, namespace, pod, infra components like Cilium/Longhorn/Traefik) work with zero manual dashboard authoring |
| Time-to-useful | Effort to get each stack to "I would actually use this" — track dashboard tweaks, query rewrites, plugin installs |
| Query language ergonomics | Pick 3 real queries you'd run; write each in PromQL+LogQL on A and PromQL+LogsQL on B; compare clarity and pain |
| Rule authoring | Author the same 3 alerts ("node disk >80%", "pod crashlooping", "Longhorn replica unhealthy") on both; compare `PrometheusRule` vs `VMRule`, Loki Ruler vs vmalert+LogsQL |
| Day-2 operational feel | Each time you tune something, fix something, or read docs to understand a behavior, log it here. This is the "Loki is a beast" claim under direct test |
| Resource footprint | After ~1 week of ingest: CPU, RAM, Longhorn disk usage per stack |
| Helm/GitOps fit | How cleanly does the chart drop into Flux? Upgrade path? |
| Backup/restore story | What does a Synology-targeted backup look like for each? |
| Ingest survival | At least once during the bake-off, simulate a Longhorn replica becoming unavailable for the storage backend; observe agent behavior on each stack |

## Findings

_Populated as we go; dated entries._

### 2026-04-29 — bake-off setup complete

All five planned commits landed. State at end-of-day:

- Standalone Grafana on `grafana.home.kelch.io`, four datasources
  registered via labeled ConfigMaps (Prometheus, Loki, VictoriaMetrics,
  VictoriaLogs), 41 bundled dashboards from KPS + VM-stack + VL-stack
  picked up by the sidecar.
- KPS: Prometheus (49 healthy targets), Alertmanager, KSM, node-exporter
  ×3, prometheus-operator. ServiceMonitor selectors set cluster-wide so
  cilium / longhorn / cert-manager / traefik / Loki / Alloy / VM
  components are all being scraped.
- VM stack: VMSingle (67 targets — slightly more than Prom because
  vmagent also scrapes VMServiceScrapes and VL/VM internals), vmagent,
  vmalert (firing to KPS Alertmanager). VMAlertmanager disabled.
- Loki single-binary on Longhorn, ingesting via Alloy.
- VictoriaLogs single-node on Longhorn, ingesting via the same Alloy
  DaemonSet's second `loki.write` sink — stream labels flow through
  identically.
- OpenObserve unchanged, not part of evaluation.

Snags hit during setup, recorded for "operational ergonomics" axis:

- KPS chart version assumed (8.5.2) was wildly stale; current is 84.3.0.
  My fault, not the chart's. Caught before push.
- VM-k8s-stack: chart's value path for the bundled VMAlertmanager is
  `alertmanager:` at the values root, not `vmalertmanager:` as I'd
  assumed by analogy with `vmsingle` / `vmagent` / `vmalert`. Disable
  flag was silently accepted and ignored — required a fix-up commit.
- VM-k8s-stack: with VMAlertmanager disabled, vmalert errors at
  template-render unless given an explicit notifier or
  `vmalert.spec.extraArgs.['notifier.blackhole']: 'true'`. Resolved by
  pointing vmalert at KPS Alertmanager. Single AM UI for both stacks
  is a win incidentally.
- VM datasource ConfigMap initially had port 8429 (vmagent's port);
  vmsingle is on 8428. Trivial off-by-one, fixed.

These are the kind of "operational footgun" findings that should
inform the comparison. Loki has not yet reciprocated with anything
similar, but it's been live for ~1h.

### 2026-05-02 — Loki wipes its PVC on scale-to-0

Discovered during the Longhorn storage-network maintenance window. The
Loki Helm chart sets the StatefulSet's
`persistentVolumeClaimRetentionPolicy` to `whenScaled: Delete,
whenDeleted: Delete`. Every other stateful workload in the bake-off
(Prometheus, Alertmanager, VictoriaLogs, OpenObserve) defaults to
`Retain`.

Combined with our Longhorn StorageClass `reclaimPolicy: Delete`, this
means **scaling Loki to 0 destroys its data**: Kubernetes deletes the
PVC, which deletes the underlying Longhorn volume. On scale-up Loki
gets a brand-new empty PVC. We saw the loki PVC UID change three
times across two cutover attempts before manual cleanup landed it on
a fresh `pvc-5991bea7-…`. The intermediate "faulted" state mid-window
turned out not to be a network bug — it was a brand-new volume that
never finished its first replica creation while the cluster was busy
churning.

Implications for the comparison:

- Loki's default-destructive scale behavior is a real operational
  footgun for routine cluster maintenance windows. VictoriaLogs sits
  through the same scale-to-0 → scale-to-1 with its data intact.
- Override is a one-line value (`singleBinary.persistentVolumeClaim
  RetentionPolicy.whenScaled: Retain`) but the fact that it ships
  destructive-by-default is worth weighing. For a homelab where
  Loki retention is short anyway it may be acceptable; in a setting
  where logs are correlated against historic traces it isn't.
- Worth checking what other Loki-chart deployment modes (simple-
  scalable, microservices) do here — they may behave differently.

### 2026-05-02 — VictoriaMetrics had no gaps; Prometheus did

Same maintenance window. After the cluster came back up, looking at
the same time range in Grafana across both metrics stacks: VM dashboards
show a continuous timeline with no missing samples; Prometheus dashboards
show clear gaps spanning the window.

Root cause is architectural, not configuration:

- `kube-prometheus-stack` deploys a **monolithic** Prometheus — one
  StatefulSet pod that both scrapes targets and stores TSDB on its
  PVC. To detach the PVC for the maintenance window we had to scale
  it to 0, which also stops scraping. No second writer is around to
  cover for it. Gaps in the data are real and unrecoverable.
- `victoria-metrics-k8s-stack` splits the role: **vmagent** (separate
  Deployment, no PVC) does scraping; **vmsingle** (separate, with
  PVC) does storage. To detach vmsingle's PVC we scaled vmsingle to
  0, but vmagent kept running. vmagent buffers remote_write traffic
  when its target is unavailable (in-memory + on-disk persistent
  queue) and flushes the buffer when vmsingle returns. From the
  query side the timeline looks unbroken.

Implications for the comparison:

- This isn't a tunable in Prometheus — fixing it requires running
  Prometheus in agent mode with remote_write to a separate storage
  backend, which is essentially rebuilding the VM architecture. As
  shipped by KPS, monolithic Prometheus loses data during any
  storage-PVC maintenance. VM as shipped does not.
- The observation generalizes beyond storage cutovers: anything that
  takes the storage layer offline (volume migrations, version upgrades
  involving incompatible state, replica rebuild windows) lands in the
  same bucket — VM hides it, Prometheus exposes it as gaps.
- Caveat we should verify: vmagent's buffer durability depends on
  whether its persistent queue is on a PVC or just emptyDir/tmpfs.
  In the chart's default deployment it's the latter, which means
  a vmagent restart during the same window would lose buffered
  samples. We didn't restart vmagent here — only vmsingle — so the
  buffer survived. Worth noting before claiming "VM never loses
  metrics during maintenance" categorically.

### 2026-05-09 — Cross-datasource portability of VM-authored dashboards

Two observations from real-world use of the VM-stack's
`kubernetes-views-pods` dashboard while looking at a Prowlarr pod in
`media/`. Reference:
[grafana.home.kelch.io/d/k8s_views_pods](https://grafana.home.kelch.io/d/k8s_views_pods/kubernetes-views-pods?orgId=1&var-namespace=media&var-job=kube-state-metrics).

- **Memory utilization shifts when switching datasource.** Same panel,
  same pod, same time range → different absolute values when toggling
  the panel's `$datasource` between `prometheus-kps` and the VM
  datasource. Root cause is architectural, not a bug: panel queries
  (`container_memory_working_set_bytes` etc.) are authored against VM's
  scrape cadence and recording-rule set. Pointing them at Prom feeds the
  same expression into a different scraper with a different scrape
  interval, different relabel rules, and no recording rules. Both
  samplings of the kubelet are valid; numerical drift is expected, not
  "wrong." It's a real cost of cross-stack dashboard portability — VM
  dashboards do *render* on Prom, they just don't *agree* with
  Prom-native dashboards at the value level.

- **Pods all legend as "app".** Series legends collapse to `app` rather
  than the pod name. The dashboard's legend format is `{{container}}`,
  and most workloads in this cluster (Prowlarr, Flaresolverr, the *arr
  suite) ship a single container literally named `app`. Per-panel fix:
  change legend to `{{pod}}` or `{{workload}}` on a forked copy. KPS's
  `Compute Resources / Pod` dashboard isn't affected — uses pod names
  directly.

### 2026-05-11 — Grafana HR unstuck; dashboard discoverability audit

Started as "are we missing pre-baked dashboards?" Ended as a
chart-rendering + Helm-patch-semantics rabbit hole. Notable findings:

- **Cross-namespace dashboards weren't loading because the Grafana HR
  was Stalled.** The `sidecar.dashboards.searchNamespace: ALL` change
  (commit `fba7e18`, 2026-05-09) had been rejected three times by Helm;
  the HR rolled back to v5 with `Stalled / RetriesExceeded`. Cilium and
  Kaniop's labeled ConfigMaps were sitting in the cluster waiting for a
  sidecar that was still scoped to `observability/`.

- **The rollouts deadlocked on RWO + RollingUpdate.** Grafana's PVC is
  `ReadWriteOnce` (longhorn). The chart's default
  `deploymentStrategy.type: RollingUpdate` with `maxSurge: 25%` tries
  to bring up a new pod while the old one still holds the volume. New
  pod sits in `ContainerCreating` until rollout deadline, Helm rolls
  back. Any future spec change would have hit the same wall.

- **`type: Recreate` is unreachable in this chart.** First fix attempt
  set `deploymentStrategy.type: Recreate`. Failed: Helm's three-way
  patch preserved the live Deployment's existing `rollingUpdate` block;
  k8s rejected with `rollingUpdate may not be specified when type is
  'Recreate'`. Adding `rollingUpdate: null` failed identically — the
  chart template is unconditional:

      {{- with .Values.deploymentStrategy }}
      strategy:
        {{- toYaml . | trim | nindent 4 }}
      {{- end }}

  `toYaml` emits `rollingUpdate: null` literally, and the API server
  treats explicit null as "specified." No clean fix without forking
  the chart or post-rendering.

- **Working fix: RollingUpdate with `maxSurge: 0, maxUnavailable: 1`.**
  For a single-replica Deployment this is runtime-equivalent to
  Recreate — the old pod must go unavailable (releasing the RWO volume)
  before a new one starts. Landed as `bf74b1d`. HR went Ready, sidecar
  picked up the cross-namespace ConfigMaps, three previously-invisible
  dashboards (Cilium agent, Cilium operator, Kaniop) loaded. End state:
  51 dashboards across 52 labeled ConfigMaps.

- **The 52→51 gap is a silent title collision.** KPS's `kubelet.json`
  and VM-stack's `kubernetes-kubelet.json` both register a dashboard
  titled `Kubernetes / Kubelet`. Grafana surfaces only one; the choice
  is unstable across sidecar resyncs. Right when we want symmetric
  side-by-side stack comparison this view goes asymmetric.

- **Several scraped workloads ship no bundled dashboard.** Longhorn,
  Traefik, cert-manager, Flux, and Alloy emit metrics that KPS / VM
  scrape, but their charts bundle no Grafana dashboard. There's no
  opt-in flag to flip — each requires a manual labeled ConfigMap from
  grafana.com or the upstream source.

- **Three KPS dashboards are intentionally absent.** Controller Manager,
  Scheduler, etcd — `kubeControllerManager / kubeScheduler / kubeEtcd`
  remain `enabled: false` per the original setup note above.

Implications for the comparison:

- The HR incident is stack-agnostic (same RWO PVC + chart pattern would
  have hit either stack). Not a differentiator.
- The portability quirks logged on 2026-05-09 are stack-specific
  though: VM-authored dashboards drift numerically when pointed at
  Prom, and the title-collision case erases the VM kubelet view in a
  single shared Grafana. Counts against the "just put both datasources
  behind one Grafana and let people compare" framing.
- Of the workloads with no bundled dashboard, Longhorn is the most
  bake-off-load-bearing — storage behavior is exactly what we care about
  during maintenance windows, and we have no canned view of it.

Tracked separately:

- [#50](https://github.com/kelchm/home-lab/issues/50) — dashboard
  inventory: kubelet collision, chart-bundles-no-dashboard gaps,
  Talos-bound control-plane scrapes
- [#51](https://github.com/kelchm/home-lab/issues/51) — `smartctl_exporter`
  DaemonSet for NVMe wear / SMART telemetry (surfaced while looking at
  low-level node metrics; hwmon already covers CPU / NVMe / GPU temps
  and voltages, but not SMART wear / media errors)

## Next

- Use the stack daily for real queries. Open Grafana, hit Explore,
  query each datasource. Build a small mental model of which UX feels
  better, which dashboards work without modification, what the day-2
  ops feel is.
- Author the three test alerts (node disk >80%, pod crashlooping,
  Longhorn replica unhealthy) on each stack and note the time + pain.
- Run for at least 2 weeks. Expect to update Findings as observations
  accumulate.

## Decision

_Filled in at the end of the bake-off with rationale._

## Out of scope

- Tracing (no instrumented apps yet; Tempo or VM-trace evaluation
  deferred to post-decision).
- External hosts (Synology node-exporter, UniFi switch SNMP) — same
  Prom-format scraping on either stack; doesn't differentiate them.
- Talos system/kernel logs — separate subtask, equal cost on both stacks
  (`machine.logging.destinations` syslog → either ingester).
- Long-term object-storage retention — post-decision project. If a stack
  with strong S3 demand wins, MinIO on the Synology becomes the target.
- Gatus uptime monitoring — orthogonal to this comparison; ideally lives
  outside the cluster anyway.

