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

### 2026-04-29 — bake-off setup begun

Standalone Grafana deployed (commit 1). Pipelines A and B follow.

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
