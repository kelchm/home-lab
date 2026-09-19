# Log collector replatform evaluation: Alloy → vlagent or Vector

**Status:** Proposed — 2026-09-19; a decision and evaluation design for replacing the Grafana Alloy pod-log collector, motivated by a fresh-start review of the collector landscape and the accepted durability gap in the current pipeline. No deployment happens on merge; every execution phase has its own gate.

## Context and origin

The Alloy DaemonSet was chosen during the Loki bake-off era and deliberately survived the convergence to VictoriaLogs because the collector was not the bottleneck and migration would discard validated correctness work (PR #483's path-correlation soak, PR #484/#487's normalization and hardening, the CI contract in `scripts/ci/validate-alloy-log-paths.sh`). A fresh-start review in September 2026 concluded that this momentum argument, while still true for *migrating*, no longer holds for *starting fresh*: the collector landscape has moved and the incumbent has one real accepted weakness.

The weaknesses and changes that motivate this plan:

- **Durability gap.** Alloy's `loki.write` queue is memory-only with bounded retries (`max_backoff_retries=15`); a prolonged VictoriaLogs outage silently drops entries beyond the retry window. The runbook documents this as accepted posture, mitigated only by alerts. On-disk buffering is the single capability a replacement would meaningfully add.
- **Wrong-protocol shim.** Alloy speaks the Loki push API to a non-Loki backend; the entire field contract (`message_fields_prefix=msg.`, `_msg_field=...`, `_stream_fields=...` URL params in `kubernetes/apps/observability/alloy/app/helmrelease.yaml:290`) exists to compensate. A collector speaking VL-native ingestion removes the shim layer.
- **The first-party collector now exists.** VictoriaMetrics shipped `vlagent` with a `-kubernetesCollector` mode, automatic pod discovery and metadata enrichment, LogsQL-based pre-read exclude filtering, on-disk buffering with bounded disk usage, and hostPath checkpoints — plus the `victoria-logs-collector` Helm chart and a 2026 collector benchmark claiming the lowest resource usage among candidates. When the bake-off ran, this did not exist.
- **VL gained native syslog and journald ingestion** (`/victorialogs/data-ingestion/syslog/`, `/journald/`), which changes the estate-log endgame in the [observability rework](../plans/20260703-observability-rework.md) §Collection posture: whichever collector is chosen should be able to serve later as the estate receiver without adding a second agent fleet.

A correction to the premise that triggered this plan: **Vector has no native VictoriaLogs sink.** The official integration uses the Elasticsearch sink with `_msg_field`/`_stream_fields` query parameters or the generic HTTP `jsonline` sink. "Vector + native OTel where it fits" therefore decomposes into "Vector via ES/jsonline shim" or "Vector's OTLP sink → VL's OTLP endpoint" — the shim problem does not disappear for Vector either; only vlagent (native `/insert/native`) and a raw OTel collector (native OTLP) avoid it.

## Candidates

| | vlagent | Vector | Alloy (status quo) |
| --- | --- | --- | --- |
| Ingestion protocol | Native `/insert/native`; also accepts all VL HTTP protocols on :9429 | Elasticsearch sink w/ query params, or HTTP `jsonline`, or OTLP sink | Loki-compat push with field-mapping URL params |
| Durability | On-disk buffer, per-URL bounded (`-remoteWrite.maxDiskUsagePerURL`), replays after outage | Disk buffers natively | Memory-only, bounded retries |
| Kubernetes collection | `-kubernetesCollector`: auto discovery, metadata enrichment, pre-read LogsQL exclude filter | `kubernetes_logs` source: CRI + partial merge | discovery.relabel + exact path rules (hardened in #483) |
| CRI handling | Native (explicit invalid-line handling); partial-line reassembly unverified | Native, including partial merge | `stage.cri` + documented partial behavior |
| Transforms | None — flag-configured only | VRL (full language) | loki.process stages + templating |
| Severity normalization (#487) | **Cannot replicate** — no extraction/alias-folding capability | Replicable in VRL | Implemented, CI-guarded |
| `service_name` derivation | **Cannot replicate** the 3-way fallback; would use `kubernetes.pod_labels.*` fields or default stream fields | Replicable in VRL | Implemented, CI-guarded |
| Metadata freshness | Pod/node labels cached at startup; changes need restart | Live via k8s API watch | Live via discovery.kubernetes |
| Estate receiver (network) | Accepts all VL HTTP protocols on :9429 out of the box; syslog/journal protocols via separately configured listeners | `syslog` source is a network receiver | `loki.source.syslog` component exists, but reception is not its design center |
| Estate receiver (local journal) | Journal ingestion via configured listeners | `journald` source is local-only (runs `journalctl`; cannot receive forwarded journal traffic) | Same `journalctl` limitation |
| Resource profile | Lowest (vendor benchmark) | Moderate | Moderate (requests 128Mi/limits 768Mi) |
| Maturity | Young (2026) | Mature | Mature |
| First-party alignment | Yes — same vendor, same release train, same chart repo | No | No |

## Recommendation

Run a bounded **head-to-head evaluation of vlagent against Vector**, then decide. The status quo stays a live option: if both fail the acceptance gates below, the incumbent's durability gap remains alert-mitigated and no migration happens. This plan deliberately does not pre-pick a winner — vlagent's first-party alignment and durability are compelling, but it cannot express the two schema behaviors (#487 normalization, `service_name` fallback) that were the most expensive lessons of the current pipeline, and how much those matter is an empirical question the evaluation answers.

## What any successor must replicate (contracts)

Non-negotiable contracts (fail any one and the candidate is out):

1. Exact `/var/log/pods` path correlation incl. Talos static mirror-pod config-hash paths (#483 lesson: mis-correlation is silent and corrupts history).
2. CRI decode, ANSI strip after reassembly, no global multiline rule.
3. Collision-safe application payload (no app key may mask cluster/namespace/workload fields).
4. Stream identity stability: `cluster`, namespace, service identity, pod, container, node — within the measured envelope (alert: >1,500 new streams/hour).
5. Field parity: the ordinary queryable fields the runbook depends on survive — `app_instance`, `filename`, `stream`, and the five-field `_msg` fallback chain (`message`, `msg`, `log`, `event`, `record.message` post-prefix). Timestamp precedence must match incumbent behavior (CRI runtime timestamp) or the divergence is explicitly accepted with re-validation of comparison and time-picker queries — vlagent by default prefers application-embedded `time`/`timestamp`/`ts` over the runtime timestamp, which would silently move entries.
6. Hardening parity, not just pod-level: bounded resources, read-only rootfs, no capabilities, `allowPrivilegeEscalation: false`, RuntimeDefault seccomp, owner-read for root-owned 0640 pod logs, node-scoped collection, and — because a read-only root filesystem does not constrain hostPath mounts — host log mounts read-only and exactly `/var/log/pods` (or narrower); any additional host path requires a reviewed exception in the CI contract.
7. CI contract: `validate-alloy-log-paths.sh` replaced with equivalent exact-contract scripts for the chosen config. The incumbent script governs Alloy's own writer/forwarding graph and does not block deploying candidate collectors, so it is **not lifted** during evaluation; the successor contract adds separate collector-count and destination-count checks (one collector that fans out to multiple backends satisfies neither).
8. Alert continuity: Alloy-specific pipeline alerts (`platform-alerts.yaml`) re-targeted to the new collector's metrics without losing node-silence and drop detection — including backend-side alerts that select protocol-specific series: `VictoriaLogsIngestionSilent` matches only `vl_rows_ingested_total{type=~"loki.*"}`, so a native/Elasticsearch/OTLP switch must update its selector or the critical alert fires permanently despite healthy ingestion.

Waivable-with-approval contracts (a candidate may trade these away only with explicit sign-off and the replacement delivered in the same change):

- **Severity normalization (#487).** If dropped, severity display moves to VL/plugin layers (`detected_level`, logLevelRules) and every `level:` LogsQL filter in the runbook and future LogsQL alerting is reworked before E3.
- **`service_name` fallback derivation.** If dropped, the replacement browsing identity (e.g., `kubernetes.pod_labels.*` fields) is validated against the stream envelope, the Grafana datasource provisioning, and the 11 label-less Longhorn system pods before E3.

## Evaluation design

**Phase E1 — isolated harness (no prod impact).** Deploy a scratch VictoriaLogs single-node (small retention, e.g. 2d) on the PVE test capacity or a disposable namespace, and run vlagent and Vector side by side against it from one k8s-prod node (or a PVE VM mounting a snapshot-equivalent log set). Each candidate must satisfy every non-negotiable contract; the two waivable contracts are decided explicitly per candidate with their replacement delivered. Verify: partial-line/CRI fidelity vs current ingested rows; `service_name` equivalence per live label audit (11 Longhorn system pods are the fallback cases); traefik access-log `_msg` readability (Vector can synthesize it in VRL; record whether vlagent's default mapping handles it); restart-of-collector checkpoint behavior; label-change propagation (the vlagent restart caveat); a rendered resource/verb RBAC comparison between candidates and the incumbent chart's defaults (the alloy chart ships broad default RBAC — pods, nodes, and more — so vlagent's access is not assumed wider; least-privilege is an E1 requirement either way).

**Phase E2 — durability drill.** Two drills per candidate, against numbered fixture entries emitted before the fault so replay is countable: (a) backend outage — stop the scratch VictoriaLogs, generate load ≥ the incumbent retry window, restore, and require 100% replay of buffered entries with bounded disk growth, with the collector itself restarted mid-outage to prove the buffer is persistent rather than memory-resident; (b) buffer saturation — sustain load past the buffer limit and require documented, alerted loss behavior rather than silent truncation (vlagent drops oldest data once `-remoteWrite.maxDiskUsagePerURL` is reached; the pipeline alerts must detect that mode). Outage duration and volume budget are fixed in the harness before the run. Alloy fails drill (a) under today's configuration (memory-only `loki.write` queue). The deployed Alloy v1.19.2 does carry an experimental `loki.write` WAL that would add disk-backed delivery; this plan explicitly rejects enabling it — experimental status, unknown retention/retry semantics, and a configuration change that would still leave the Loki-shim protocol in place — rather than testing it, and that rejection is revisited only if both candidates fail E2.

**Phase E3 — cutover (separate PR, gates required).** Dual-run the chosen collector alongside Alloy into the prod VL, isolated from production queries: shadow entries go to a separate VL tenant (AccountID/ProjectID headers) or carry an enforced exclusion that production queries filter; a plain discriminator field is not isolation because it still double-counts in existing aggregates such as the runbook's `stats by (service_name)`. Fix a capacity/abort gate before admitting shadow traffic (current store usage is ~1.1 GiB of 30 Gi, so seven days of dual-write is not a capacity risk in practice, but the gate is what makes that checkable). Measure entry-count deltas and stream-creation envelope for ≥7 days with a successor-only ingestion/failure test passing, then cut over: retarget all Alloy-specific and protocol-specific backend alerts (`VictoriaLogsIngestionSilent` among them), replace the CI script, update runbook + #556-style inventory, and remove Alloy. Stream identity will change (new streams; historical comparability ages out within the 30-day retention — accepted).

**Decision record.** Whichever way the evaluation lands — switch or stay — the outcome and measured evidence are recorded as a status revision of this plan, and the collector choice is revisited only if a driver in this document materially changes (vendor direction, schema needs, estate-receiver phase).

## Risks and open questions

- vlagent is young; the chart (`victoria-logs-collector`) is new to the victoriametrics chart repo. Pinning, Renovate tracking, and security posture need review in E1 — specifically a rendered resource/verb RBAC comparison against the incumbent chart's defaults and the hostPath mount surface (the vendor DaemonSet example mounts all of `/var/log`, which the hardening contract narrows to read-only `/var/log/pods` unless a reviewed exception says otherwise).
- vlagent cannot express the #487 normalization; if the evaluation picks it, severity display moves entirely to VL/plugin layers (`detected_level`, logLevelRules) and `level:` LogsQL filters in the runbook and future LogsQL alerting need rework. Record this explicitly if it becomes the chosen trade.
- vlagent's default stream fields (`kubernetes.container_name`, `kubernetes.pod_name`, `kubernetes.pod_namespace`) differ from the current identity (`service_name`, `pod`, ...); any streamFields override must be validated against the envelope alert and the Grafana datasource provisioning in the same change.
- The evaluation runs on one node's real log flow; behavior on the other two (log volume skew, disk variance) is extrapolated — acceptable for a 3-node homogeneous cluster.
- Renovate does not manage the Grafana datasource plugin pin today (`grafana/app/helmrelease.yaml:89`); a collector switch does not fix that, but E3's runbook pass should note manual-pin drift for all collector-adjacent pins.

## Related

- #485 — log schema normalization (Phase 1 shipped; source inventory in flight via draft PR)
- #470 / [observability rework](20260703-observability-rework.md) — convergence baseline this builds on; its syslog-ingress and LogsQL-alerting designs assume "the collector" is whatever this plan decides
- [Logging runbook](../runbooks/logging.md) — documents the incumbent pipeline and its accepted durability gap
