# Observability rework — conclude the bake-off, consolidate, kill the toil

**Status:** Active — 2026-08-31; alert delivery is live, OpenObserve is retired, and the first convergence slice transfers shared CRD ownership before either bake-off pipeline is removed. Background: [observability bake-off](../observability-bakeoff.md).

## Context

This is the execution plan that concludes the [observability bake-off](../observability-bakeoff.md)
and turns the result into a low-maintenance, single-pane setup covering the **whole
estate** (k8s cluster + Synology NAS + UniFi + the `rpi-nixos` edge fleet), with
anomaly/ML alerting as an explicit goal.

It started as a narrow question — "does Netdata fit here?" — and widened into "what
gets me Netdata-class *usefulness* (auto-dashboards + anomaly detection) without the
dashboard-authoring tax, self-hosted." The Netdata evaluation is settled below; the
rest is the actual work.

Baseline verified live on 2026-07-01/02 (read-only via the Grafana + Kubernetes + Flux MCPs):

- **5 observability backends running in parallel** in `observability`: kube-prometheus-stack
  (Prometheus + Alertmanager + kube-state-metrics + node-exporter), VictoriaMetrics k8s-stack
  (vmagent + vmsingle + vmalert), Loki, VictoriaLogs, and **OpenObserve still up** (out of the
  bake-off since setup, ~246 MB, nothing depends on it). Logs via a shared Alloy DaemonSet.
- **~5.5 GB RAM** in the namespace (vmsingle 1.4 GB, Prometheus 1.1 GB, Loki ~0.5 GB, Grafana
  ~0.5 GB, Alloy ×3 ~0.8 GB, VictoriaLogs 0.26 GB, OpenObserve 0.25 GB, plus operators/exporters).
- **51 Grafana dashboards — 0 hand-authored.** All chart/mixin-bundled (`kubernetes-mixin`,
  `node-exporter-mixin`, `loki`, `vm-k8s-stack`, Cilium, CoreDNS, Kaniop, Prometheus, Alertmanager).
  Confirmed absent: **Longhorn, Traefik, cert-manager, Flux, Alloy**.
- **296 alerting rules across 66 VMRule groups — 0 hand-authored** (the KPS mixin rules + VM-stack
  defaults, rendered as VMRules, evaluated by vmalert). **0 Grafana-managed rules. 0 anomaly/ML rules.**
- **At the time, alerting delivered to nothing.** Grafana-managed side was empty; the notification policy pointed at
  an empty `grafana-default-email` receiver; vmalert fired into the KPS Alertmanager, which had no
  wired target (matches the bake-off's own note). This was corrected on 2026-08-29 as described below.
- Cilium is the eBPF datapath (kube-proxy replacement, native routing) with agent/operator Prometheus
  scraped, but **`hubble.enabled: false`** — the eBPF *observability* half is switched off.

## The reframe (why this plan is shaped the way it is)

The stated pain was "I don't have time to hand-build Grafana." The inventory says the opposite is
already true: **nothing has been hand-built, and there are 51 dashboards + 296 rules.** The real
problems are not missing dashboards — they are:

1. **Alert delivery is tied to the losing stack** — Pushover works, but kube-prometheus-stack still owns the production Alertmanager and evaluator route.
2. **Two overlapping bake-off pipelines remain** — pure maintenance tax with no benefit now that the bake-off has a winner. OpenObserve, the unused fifth backend, was retired in the first convergence slice.
3. **A ~5-component dashboard gap** — real, but each is one declarative reference, not a build project.
4. **No anomaly/ML** — all 296 rules are static thresholds; nothing surfaces unknown-unknowns.
5. **The estate is uncovered** — NAS, UniFi, edge Pis: still zero visibility.

## Decisions made

- **VictoriaMetrics wins the bake-off.** Decided on the axes the bake-off's own findings recorded:
  ingest robustness during storage-maintenance windows (vmagent persistent queue vs monolithic
  Prometheus gaps — architectural, not tunable) and Loki's destructive scale-to-0 PVC-delete vs
  VictoriaLogs surviving intact. The bake-off's `## Decision` section records the result and points here for execution.
- **Grafana stays the single pane** (OIDC via kanidm, admin Traefik gateway). Everything folds into it.
- **Netdata Cloud is rejected**; the self-hosted path is the direction (see "The Netdata question").
- **Edge fleet uses `node_exporter`, not a streaming Netdata child** — SD-write-averse Pi Zero 2 W,
  keeps one paradigm, inherits the same alert pipeline. (Full rationale in the rpi-nixos discussion.)

## Open decisions (must resolve before the phase that needs them)

- **ML / anomaly layer — the one genuinely hard call.** Free-and-self-hosted has no per-metric-ML
  equal to Netdata's agent. Options, to decide after Phases 1–2 are in place:
  - **A. Coroot Community** (Apache-2.0) as a free auto service-map + inspections + SLO-burn-rate
    layer riding VM. Closest self-hosted "zero-config" feel. Cost: adds ClickHouse (a 3rd log store)
    + its own UI (a 2nd pane); free anomaly is heuristic/SLO, **not** per-metric ML; AI-RCA is
    Enterprise (but its **free MCP** lets our own Claude do RCA).
  - **B. Free Netdata *agent*** (GPLv3, no Cloud) — the only free blanket per-metric ML. Reintroduces
    Netdata software (a 2nd pane) despite the vendor-trajectory wariness; per-metric ML is chatty
    (alert on node-level anomaly-rate + prebuilt alarms, not every metric's anomaly bit).
  - **C. Pay Netdata Homelab $90/yr** — best blanket ML at least effort, but reopens the SaaS/egress
    door (cheaper than Coroot Enterprise for this core count).
  - **Recommended starting point:** trial **A (Coroot Community)**; if its heuristic+SLO anomaly
    doesn't scratch the itch, decide B vs C consciously.
  - **Ruled out:** vmanomaly (VictoriaMetrics **Enterprise**, license key mandatory since v1.5.0 —
    proprietary key + renewal fights GitOps permanence); Grafana ML outlier/forecast/Sift
    (Grafana **Cloud**-only, not in self-hosted OSS/Enterprise).
- **How far up the Hubble rung** (Phase 4): flows + service map (cheap) vs the L7 HTTP-metrics tier
  (needs the Envoy proxy path, currently disabled; per-request overhead) — a judgment call for a
  homelab's modest east-west traffic.

## The sequenced plan

| Phase | Change | Leverage | Risk |
|---|---|---|---|
| 1 | Preserve alert **delivery** while converging on VM (retire KPS-Prometheus/Alertmanager, Loki, OpenObserve) | Highest | Medium — staged ownership and live checks required |
| 2 | **Dashboards-as-code** for the 5 gap components via `grafana-operator` | High | Low |
| 3 | **Estate coverage** — UniFi, Synology, edge Pis, uptime | High | Low |
| 4 | **eBPF signals** — turn on Hubble (already own it); optionally Beyla | Medium | Low–Medium |
| 5 | **Anomaly + AI-RCA** — the open ML decision + HolmesGPT-on-our-Claude | Medium | Low |

Sequencing rule: **preserve delivery, finish convergence, then enrich signals.** Do not move the paging authority or remove a source until its replacement is live and verified.

### Phase 1 — convergence without losing delivery

Pushover delivery became operational on 2026-08-29 through the KPS Alertmanager. Live checks on 2026-08-31 found all three controller-manager, scheduler, and etcd targets healthy, the Alertmanager configuration loaded, and no failed Pushover notifications. Convergence must preserve that production path until vmalert and VMAlertmanager pass the same end-to-end test.

Convergence is **not** "delete KPS." A dependency check (2026-07-03, refreshed 2026-08-31 — verified across repo config +
live metric provenance + live k8s inventory, high confidence) found the **VM stack has six teardown dependencies
on the KPS release.** The VM chart explicitly disables its own KSM + node-exporter
(`kube-state-metrics.enabled: false`, `prometheus-node-exporter.enabled: false`) and its `ks.yaml`
carries `dependsOn: [kube-prometheus-stack]` with the comment "we reuse KPS's KSM, node-exporter,
Alertmanager, prometheus-operator CRDs." A naive delete cascades well past metrics.

**The six couplings and how each is decoupled — do these first, then delete KPS:**

1. **kube-state-metrics.** Every `kube_*` series comes from `kube-prometheus-stack-kube-state-metrics`
   (confirmed: `up{job="kube-state-metrics"}` pod/service carry the KPS name; no VM-owned KSM exists).
   → flip `kube-state-metrics.enabled: true` in the VM chart.
2. **node-exporter.** Every `node_*` series comes from `kube-prometheus-stack-prometheus-node-exporter`
   (KPS-owned DaemonSet, 3 nodes). → flip `prometheus-node-exporter.enabled: true` in the VM chart.
   The chart preserves `job=kube-state-metrics` / `job=node-exporter`, so dashboards/queries keep
   matching; expect a brief scrape gap as source pods rename to `victoria-metrics-k8s-stack-*`.
3. **prometheus-operator CRDs (`monitoring.coreos.com`) — the big blast radius.** The live
   `servicemonitors.monitoring.coreos.com` CRD is labelled `helm.toolkit.fluxcd.io/name: kube-prometheus-stack`
   (KPS `crds.enabled: true` owns it; bootstrap `00-crds.yaml` also sources CRDs from KPS). Third-party
   charts (Cilium, Longhorn, cert-manager, Traefik) emit **ServiceMonitor** objects that vm-operator
   converts to VMServiceScrape. KPS packages these in Helm's special `crds/` directory, so Helm retains them on uninstall; the original cascade-delete concern was overstated. The real gaps are independent lifecycle and upgrades. → install the matching `prometheus-operator-crds` 31.0.1 chart as a standalone Flux HelmRelease, adopt the live v0.93.1 CRDs, protect them with `helm.sh/resource-policy: keep`, disable KPS's CRD install, repoint bootstrap, and make CRD consumers depend on the standalone release.
4. **Converted-object garbage collection.** victoria-metrics-operator had converter owner references disabled. Removing a Prometheus source could therefore leave an unowned VM copy evaluating forever; this happened with `KubeProxyDown`. → enable converter ownership before teardown and verify representative VMRule, VMServiceScrape, and VMPodScrape objects point at their Prometheus sources. Remove any pre-existing orphans explicitly.
5. **Alertmanager (the delivery target).** vmalert currently sends into `kube-prometheus-stack-alertmanager:9093`; only the Prometheus-evaluated copy is routed to Pushover, which intentionally prevents duplicates. → enable VMAlertmanager with the existing SOPS-managed receiver credentials, stamp vmalert with an explicit evaluator label, switch the receiver matchers and UI route, and pass both synthetic delivery tests before retiring KPS Alertmanager.
6. **Flux `dependsOn`.** Remove the temporary `dependsOn: [kube-prometheus-stack]` from
   `victoria-metrics-k8s-stack/ks.yaml`, or the VM Kustomization stops reconciling once KPS is gone.

Also repoint Grafana's **default datasource** `prometheus-kps` → `victoriametrics` and verify a sample
of the 51 dashboards render (VM's datasource type is `prometheus`, so PromQL resolves).

Roll out Phase 1 in reviewable slices:

1. Retire unreferenced OpenObserve; install and adopt the standalone CRDs; enable converter owner references. The retained `data-openobserve-0` PVC is the rollback path until convergence closes.
2. Verify CRD ownership and converted owner references live, then enable VM-native KSM, node-exporter, control-plane scrapes, and VMAlertmanager without removing KPS.
3. Move Grafana's default datasource and Pushover authority to VM. Run the alerting runbook's synthetic tests and sample the existing dashboards.
4. Hold the survivor path green for a few days.
5. Delete KPS and its stale converted children, then stop Alloy's Loki write and delete Loki. Re-run the target, rule, dashboard, log-query, and delivery checks before declaring Phase 1 complete.

The first slice removes `openobserve/`, adds the standalone `prometheus-operator-crds` release, repoints bootstrap and dependent Flux Kustomizations, and enables converter ownership. Later slices change `victoria-metrics-k8s-stack`, transfer the custom rules and Alertmanager secret out of the KPS directory, remove Loki's Alloy sink, and finally delete `kube-prometheus-stack/` and `loki/`.

### Phase 2 — dashboards-as-code (the gap components)

`grafana-operator` `GrafanaDashboard` CRDs sourced by grafana.com ID, **`revision` pinned**,
`resyncPeriod: 24h`, `spec.datasources` mapping `DS_PROMETHEUS` → the VM datasource. Then the gaps:

- **cert-manager** — cert-manager-mixin (dashboard 11001; ships cert-expiry alerts, exactly what we want).
- **Alloy** — its own mixin at `operations/alloy-mixin/rendered/` (9 dashboards + 3 alert YAMLs). The
  bake-off's "Alloy has no dashboard" note was wrong.
- **Flux** — flux2-monitoring-example (dashboards good; alerts are example-grade, needs KSM CustomResourceState).
- **Traefik** — official 17347 (thin alerts; **no** Gateway-API-specific panels).
- **Longhorn** — 16888 + its example rules (the biggest real alert gap — curate the rules into git).

### Phase 3 — estate coverage (low-toil, one pane)

| Segment | Choice | Notes |
|---|---|---|
| UniFi | **unpoller** (MIT, v3.3.1) → VM | Six ready dashboards (11310–15). Override chart image tag to v3.3.1; create a UniFiOS read-only local admin to dodge MFA. |
| Synology NAS | `snmp_exporter` + Synology modules → VM | Dashboards 14284 / 13516; one-time `snmp.yml` DisplayString pass; enable SNMPv3 in DSM. |
| Edge Pis | `services.prometheus.exporters.node` (NixOS) → vmagent | Dashboard 1860; one line per host; keeps the SD-averse Zero minimal; inherits Phase 1 delivery. |
| Uptime | **Gatus** (Apache-2.0) | Monitors + alerts as YAML-in-git; status page + `/metrics` folds into Grafana. Skip Uptime Kuma (config trapped in UI-edited SQLite). |

### Phase 4 — eBPF signals (exploit what we already run)

Cilium already runs the privileged eBPF datapath, so this is not a new trust boundary — just the
`pod-security…privileged` namespace posture kube-system already has.

- **Turn on Hubble** (`hubble.relay` + `hubble.ui` + `hubble.metrics`) for a service map, flow/DNS/
  policy-drop visibility, and (with L7 visibility via the Envoy path) HTTP golden-signal metrics into
  VM+Grafana (dashboard 16613). See the open decision on how far up this rung to go.
- **Optional: Beyla / OpenTelemetry OBI** for app-level RED (latency histograms, auto traces) without
  instrumenting the third-party apps — lightweight DaemonSet, exports into the existing stack, no new UI.

eBPF caveats to keep in mind: it sees the wire not the app (golden signals, not business logic); TLS
decode is runtime-dependent; **eBPF ≠ ML** (it produces signals, the anomaly layer interprets them);
cluster-only (keep the Pi Zero on node_exporter).

### Phase 5 — anomaly + AI root-cause

- Resolve the **ML open decision** (Coroot Community trial first).
- **HolmesGPT** (MIT, CNCF) pointed at our own Anthropic key (SOPS secret, prompt caching on),
  triggered on a fired alert → an explained diagnosis reading k8s + VM + logs. This is the on-brand
  RCA path and complements the Grafana/Kubernetes/Flux MCPs already wired to Claude for on-demand digging.
- Optionally `grafana-llm-app` (Apache-2.0, own Claude key) for "explain this panel" inside Grafana.
- If Coroot is adopted, wire its **free Community MCP** to Claude as well.

## The Netdata question (settled, with the residual named)

- **Netdata Cloud: rejected.** The free tier's local multi-node pane is capped (5 nodes), the modern
  UI is proprietary (NCUL1, needs `allowUnfree`), "sensitive functions" (processes/logs) are gated
  behind a Cloud sign-in, and Cloud implies metadata egress — all against the self-hosted/GitOps stance.
- **The Homelab $90/yr plan** genuinely clears the cap and the VLAN problem and delivers a true
  whole-estate single pane — it is the "buy your way out" option (Option C above), named honestly, not
  chosen by default.
- **The residual truth:** the *one* free, self-hosted, blanket per-metric ML tool in the field is
  Netdata's own GPLv3 **agent** (no Cloud). If Coroot's heuristic anomaly proves not "ML enough,"
  that agent — despite the vendor wariness — is the only free way to match the want. This is the open
  ML decision, not a closed one.

## Effort accounting

- Phase 1: several staged rollouts across a few days (ownership transfer, replacement components, delivery migration, soak, then deletion).
- Phase 2: ~½ day (datasource remapping is the fiddly part).
- Phase 3: ~½ day total (unpoller's UniFiOS local-admin is the one annoying step).
- Phase 4: Hubble ~a HelmRelease change + validation; Beyla ~an hour if wanted.
- Phase 5: Coroot trial ~an afternoon; HolmesGPT ~1–2h.

Day-2: fewer backends to upgrade (the point); ongoing items are anomaly-sensitivity tuning (whichever
ML path), HolmesGPT token cost per investigation, and unpoller auth across UniFiOS upgrades.

## Out of scope / deferred

- Tracing beyond what eBPF (Hubble/Beyla/Coroot) yields for free — no app instrumentation planned.
- Long-term object-storage retention (MinIO on the Synology) — post-convergence, if a retention need appears.
- Replacing Grafana with Perses (dashboards-as-code) — relocates authoring rather than removing it; no pre-built dashboards for the gap components. Not now.
