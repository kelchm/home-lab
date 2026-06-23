# Sequenced dependency upgrades — k8s-prod (Longhorn → Talos → k8s → Gateway API/Traefik → kaniop)

## Context

A batch of major-version Renovate PRs for `k8s-prod` cannot be merged independently:
their ordering is load-bearing (vendor support matrices) and several of them roll the
data plane or the control plane, so they must be gated and validated one window at a
time. This runbook is the execution plan for working through all of them in one
sequenced pass.

Baseline verified live on 2026-06-22 (read-only via the cluster MCPs):

- 3 control-plane nodes `k8s-prod-1/2/3` (10.32.30.11–13), all `Ready`, **Talos v1.12.6 / k8s v1.35.4**.
- Flux **v2.8.8** (operator v0.52.0) healthy: **52 Kustomizations, 45 HelmReleases, 0 failing**.
- Gateway API CRDs present but **installed out-of-band, bundle-version v1.4.0, `standard` channel** — not managed in-repo.
- `kube-prometheus-stack` already at **86.3.2** (verify-only; do not re-apply CRDs).
- flux-operator group (#125) already merged (`0c54e1a`).

### The PRs in scope

| Window | PR | Change | Risk |
|---|---|---|---|
| 1 | [#111] | Longhorn chart `1.11.2 → 1.12.0` | Rolls all 3 nodes' Longhorn data plane |
| 2 | [#26] | Talos OS `v1.12.6 → v1.13.5` | Reboots each node; one-at-a-time |
| 3 | [#27] + [#81] | k8s `v1.35.4 → v1.36.2` (kubelet + cni-untaint image) | Control-plane + kubelet roll |
| 4 | **new** + [#138] | Gateway API `v1.5.1` standard install (in-repo), then Traefik `39.0.9 → 41.0.0` | Re-do of the reverted #143 migration |
| 5 | [#104] | kaniop `0.7.6 → 0.9.1` | CRD operator + validating webhook |

Out of scope / independent (merge any time, not gated): [#147] utility MCPs, [#144] docs, [#99] docs.

[#111]: https://github.com/kelchm/home-lab/pull/111
[#26]: https://github.com/kelchm/home-lab/pull/26
[#27]: https://github.com/kelchm/home-lab/pull/27
[#81]: https://github.com/kelchm/home-lab/pull/81
[#138]: https://github.com/kelchm/home-lab/pull/138
[#104]: https://github.com/kelchm/home-lab/pull/104
[#147]: https://github.com/kelchm/home-lab/pull/147
[#144]: https://github.com/kelchm/home-lab/pull/144
[#99]: https://github.com/kelchm/home-lab/pull/99

## Why this order (the non-obvious constraints)

1. **Longhorn 1.12 MUST precede k8s 1.36.** Longhorn 1.11.2's vendor tested-k8s matrix tops out at 1.35; 1.12.0 is the first release that lists 1.36. Crossing to k8s 1.36 on Longhorn 1.11.2 is untested.
2. **Talos 1.13.5, not 1.13.4.** Talos 1.13.2/.3 had a kube-scheduler crashloop (rendered 1.36 scheduler-config fields into a 1.35 scheduler), fixed in 1.13.4. Renovate now offers **1.13.5** (released 2026-06-22, bundles kube-scheduler/kubelet 1.36.2, carries the fix forward, benign changelog). 1.13.x supports k8s 1.31–1.36, so the interim **Talos-1.13.5 / k8s-1.35.4** state is matrix-valid.
3. **Talos OS upgrade precedes the k8s upgrade.** Talos must be on a release that supports 1.36 before `upgrade-k8s` rolls the control plane to 1.36.2.
4. **`talenv.yaml` is the source of truth and inert to Flux.** Merging #26/#27 changes nothing on the cluster until the manual `task talos:*` flow runs. #26 (line 2, `talosVersion`) and #27 (line 4, `kubernetesVersion`) touch different lines → they auto-merge with no conflict.
5. **Traefik v41 needs Gateway API ≥1.5.0 standard CRDs.** Traefik proxy v3.7 (chart 41) watches `tlsroutes.gateway.networking.k8s.io` **unconditionally** (TLSRoute graduated to the Standard channel in GW-API 1.5.0). The cluster is on v1.4.0 standard, which lacks the `v1 tlsroutes` CRD — this is exactly why #143 was reverted by #146 (`failed to watch TLSRoute`). So GW-API must reach v1.5.1 standard **before/with** the traefik re-apply. This run also brings the CRDs **in-repo** (they are currently unmanaged).
6. **kaniop last.** Additive only (0.8.0 added Gateway API parentRef fields + BackendTLSPolicy support; 0.9.0 added bounded OAuth2 secret key aliases). No breaking CRD/migration; direct 0.7.6→0.9.1 is supported. Independent of the core chain, so it goes last.

## Execution model

Driven gated: per-window and per-node approval, destructive ops executed only after go/no-go.
GitOps changes (`gh pr merge`, authoring the Gateway API app) and `flux`/`task`/`talosctl`
commands are run from this repo; health is validated at every gate using the read-only
cluster MCPs (`kubernetes-mcp`, `flux-operator-mcp`, `grafana`).

Toolchain confirmed present: `task`/`talhelper`, `talosctl`, `flux v2.8.8`, `kubectl v1.36.2`,
`gh`, `sops`. Talos tasks: `talos:generate-config` (`talhelper genconfig`),
`talos:upgrade-node IP=<ip>` (`talhelper gencommand upgrade`), `talos:upgrade-k8s`
(`talhelper gencommand upgrade-k8s --to`). All read versions from `talos/talenv.yaml`.

**Global recovery rule** (per prior incidents): if a node comes back stuck after reboot,
escalate **force-delete pods → restart kubelet → restart cilium → reboot**, in that order.
Never roll a second node until the first is fully recovered.

---

## Pre-flight gate (non-destructive)

1. **Baseline health** — confirm 0 failing before any mutation:
   - `flux-operator-mcp get_flux_instance` → 0 failing KS/HR.
   - All 3 nodes `Ready`; Longhorn volumes all `Robust`/`Healthy`, no `Degraded`/`Faulted`, no in-flight rebuilds.
2. **Deprecated-API scan for 1.36** — query Prometheus for usage that 1.36 removes:
   `apiserver_requested_deprecated_apis` (grafana MCP `query_prometheus`). Any series whose
   `removed_release="1.36"` must be remediated before Window 3.
3. **etcd snapshot** (safety net): `talosctl -n 10.32.30.11 etcd snapshot etcd-pre-upgrade-20260622.db`.
4. **Working tree clean**, on `main`, `git pull` up to date.

**Gate:** green baseline + snapshot saved → proceed to Window 1.

---

## Window 1 — Longhorn 1.11.2 → 1.12.0 (#111)

The Longhorn KS (`Kustomization/longhorn` in `flux-system`, targetNamespace `longhorn-system`)
drives one Helm upgrade that rolls `longhorn-manager` + `instance-manager` on all 3 nodes.
Gate it with a suspend so the roll is a deliberate, watched action.

1. Pre: all Longhorn volumes `Robust`, no rebuilds.
2. `flux suspend kustomization longhorn` (durable pause; survives reconcile — `flux suspend hr`/`scale` do not).
3. `gh pr merge 111 --squash` and `flux reconcile source git flux-system`.
4. `flux resume kustomization longhorn` → watch the Helm upgrade.
5. Validate:
   - `longhorn-manager` DaemonSet pods all on **1.12.0**, `Ready` on all 3 nodes.
   - `longhorn-csi-plugin` `Ready` on every node (keystone for all PVC mounts).
   - All volumes `Robust`; engine images upgraded to the 1.12.0 default (check Longhorn
     `EngineImage` state / volume `currentImage`; trigger engine upgrade if any volume lags).
   - `flux get hr -n longhorn-system longhorn` → `Ready`.

**Rollback:** `flux suspend ks longhorn`, revert #111, `flux resume ks longhorn`. Longhorn
supports chart downgrade within a minor only with caution — prefer fixing forward; the etcd
snapshot + Longhorn backups are the deep fallback.

**Gate:** Longhorn fully healthy on 1.12.0 → proceed to Window 2.

---

## Window 2 — Talos OS 1.12.6 → 1.13.5 (#26), one node at a time

1. `gh pr merge 26 --squash`, `git pull`, then `task talos:generate-config` (regenerate rendered configs from new `talenv.yaml`).
2. **For each node, strictly one at a time** (11 → 12 → 13):
   - Pre: node `Ready`, all Longhorn volumes `Robust` (losing a node mid-degrade risks data availability).
   - `task talos:upgrade-node IP=10.32.30.<n>` (reboots into Talos 1.13.5).
   - Wait for: node `Ready` + `Talos (v1.13.5)`; Longhorn `instance-manager` + `csi-plugin` back `Ready` on that node; the node's volume replicas re-attached and back to `Robust`.
   - Confirm **kube-scheduler static pod is not crashlooping** (the #13350 regression class — should be clean on 1.13.5 with k8s still 1.35.4).
   - Recovery escalation if stuck (force-delete → kubelet → cilium → reboot).
   - **Gate per node:** fully recovered before the next node.

**Rollback:** Talos upgrade is per-node and revertible by upgrading that node back to the
1.12.6 image; the cluster tolerates mixed Talos patch during the window. Stop the sequence
on the first node that won't recover and escalate.

**Gate:** all 3 nodes on Talos 1.13.5, cluster healthy → proceed to Window 3.

---

## Window 3 — Kubernetes 1.35.4 → 1.36.2 (#27 + #81)

1. Re-run the deprecated-API scan (Pre-flight step 2) — must be clean for `removed_release="1.36"`.
2. `gh pr merge 27 --squash` (kubelet/k8s version) and `gh pr merge 81 --squash` (cni-ready-untaint image `alpine/k8s:1.36.2`). `git pull`.
3. `task talos:upgrade-k8s` (talhelper `upgrade-k8s --to v1.36.2`; rolls control-plane static pods + kubelet gracefully).
4. Validate:
   - All 3 nodes report `v1.36.2`; `flux-report` serverVersion `v1.36.2`.
   - All control-plane static pods (apiserver, controller-manager, scheduler, etcd) healthy.
   - `cni-ready-untaint` DaemonSet rolled to the 1.36.2 image and reconciling (taint logic intact).
   - Full Flux sweep: 0 failing KS/HR after a `flux reconcile`.

**Rollback:** k8s downgrade is not supported in-place — the etcd snapshot is the recovery path.
This is the highest-commitment window; do not start it until Windows 1–2 are clean and the
deprecated-API scan is empty.

**Gate:** cluster fully on 1.36.2, 0 failing → proceed to Window 4.

---

## Window 4 — Gateway API v1.5.1 standard (new, in-repo) + Traefik 41 (#138)

### 4a. Bring Gateway API CRDs in-repo at v1.5.1 (new artifact)

Mirror the `whereabouts` CRD-vendoring pattern (pinned remote URL in a `kustomization.yaml`).
New app `kubernetes/apps/network/gateway-api/`:

- `app/kustomization.yaml`:
  ```yaml
  apiVersion: kustomize.config.k8s.io/v1beta1
  kind: Kustomization
  resources:
    # renovate: datasource=github-releases depName=kubernetes-sigs/gateway-api
    - https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.1/standard-install.yaml
  ```
- `ks.yaml`: `Kustomization/gateway-api` (flux-system), `path: ./kubernetes/apps/network/gateway-api/app`, `prune: true`, `wait: true`, health on the CRDs being Established. Do **not** set the experimental channel (adds TCPRoute → a new missing-CRD watch error).
- Wire into `kubernetes/apps/network/kustomization.yaml` `resources:` and add `dependsOn: [{name: gateway-api}]` to `kubernetes/apps/network/traefik/ks.yaml`.

Adoption note: the live CRDs were applied out-of-band (client-side). Flux SSA normally adopts
cleanly (it migrates the `last-applied-configuration` annotation). **Contingency** if Flux logs
a field-manager conflict on the 7 GW-API CRDs: strip the annotation
(`kubectl annotate crd <name> kubectl.kubernetes.io/last-applied-configuration-`) and let Flux re-apply.

Validate: GW-API CRDs at **bundle-version v1.5.1**, `tlsroutes.gateway.networking.k8s.io`
present (v1), existing `Gateway`/`HTTPRoute` objects still served and unchanged.

### 4b. Traefik 39 → 41 (#138)

1. `gh pr merge 138 --squash`, `flux reconcile source git flux-system`. (traefik KS now depends on gateway-api, so ordering is enforced.)
2. Validate (the exact symptoms from the #146 revert must be absent):
   - 3 traefik instances rolled to proxy **v3.7.x**, all `Ready`.
   - **No `failed to watch TLSRoute`** in any traefik pod log (grafana/loki MCP `query_loki_logs`).
   - OIDC yaegi plugin loaded; Service types correct (admin/services `LoadBalancer` + lbipam, public `ClusterIP`).
   - HTTPRoutes resolve end-to-end; kanidm sticky-session affinity intact (TraefikService `sticky.cookie.kanidm_lb`).

**Rollback:** revert #138 (back to 39.0.9, known-good); the GW-API 1.5.1 install (4a) is
backward-compatible and can stay regardless.

**Gate:** traefik healthy on 41, no CRD-watch errors → proceed to Window 5.

---

## Window 5 — kaniop 0.7.6 → 0.9.1 (#104)

1. Pre: kanidm + kaniop healthy; capture current OAuth2 client secret state.
2. `gh pr merge 104 --squash`, `flux reconcile source git flux-system`.
3. Validate:
   - kaniop operator `Ready` on **0.9.1**; validating webhook healthy (cert-manager backed).
   - kaniop CRDs applied (additive); `Kanidm` CR still reconciled; ServiceMonitor scraping.
   - **Watch the multi-replica OAuth2 `basic_secret` race**: if a client's secret drifts after
     the operator roll, recover via the `kaniop.rs/force-secret-rotation` annotation (runbook in `docs/runbooks/`).

**Rollback:** revert #104 (OCIRepository tag back to 0.7.6). Additive CRDs are forward-compatible.

**Gate:** identity stack healthy → proceed to final validation.

---

## Window 6 — Final validation + cleanup

1. Full sweep: 0 failing KS/HR; nodes Talos 1.13.5 / k8s 1.36.2; Longhorn 1.12.0 all `Robust`; traefik 41; kaniop 0.9.1.
2. Grafana: dashboards populated, no firing alerts attributable to the upgrade.
3. Optionally merge independent PRs: #147, #144, #99.
4. Memory updates: mark `project_major_upgrade_plan` done; resolve `project_traefik_v41_tlsroute_fix` (note GW-API now in-repo at 1.5.1); record that Gateway API CRDs are now Flux-managed.

## Rollback philosophy

Each window is independently revertible at the GitOps layer (revert the PR, reconcile) up to
Window 3. The k8s 1.36 upgrade (Window 3) is the point of no easy return — the etcd snapshot
from pre-flight is the recovery anchor for everything from there on. Longhorn backups + the
snapshot cover the storage/control-plane worst case.
