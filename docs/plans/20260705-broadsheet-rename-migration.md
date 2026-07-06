# paperboy → broadsheet: rename + PVC-preserving migration

/ status: ready — manifests authored on `feat/broadsheet-rename`, live steps pending

## Goal

The upstream project `paperboy` was renamed to `broadsheet` (module, binary,
image, `BROADSHEET_*` env prefix, `X-Broadsheet-*` headers, `broadsheet.db`).
Carry that rename fully through the deploy — HelmRelease, Service, HTTPRoute,
hostname, **and the PVC** — without losing the archived front-page PDFs on the
existing Longhorn volume.

Scope decisions: full rename including the PVC; hostname switches to
`broadsheet.home.kelch.io` only (the wall display must be repointed).

## What actually changes in the app

The rename is thorough but the **on-disk archive format is unchanged**, so the
PDFs need no transformation:

| Aspect        | paperboy (live)                        | broadsheet                              |
| ------------- | -------------------------------------- | --------------------------------------- |
| Image         | `ghcr.io/kelchm/paperboy`              | `ghcr.io/kelchm/broadsheet`             |
| Env prefix    | `PAPERBOY_*`                           | `BROADSHEET_*` (old names warn til 1.0) |
| State store   | `state.json`                           | `broadsheet.db` (SQLite)                |
| PDF archive   | `/data/archive/<id>/<YYYYMMDD>.pdf`    | **identical**                           |
| Render cache  | `/data/cache/<id>/<date>.png`          | `/data/cache/<id>/<date>.w1600.png`     |
| Probes        | `/health`, `/healthz`                  | **identical semantics**                 |

Built-in one-time migrations broadsheet runs on first boot (`broadsheet.New`):
- `paperboy.db` → `broadsheet.db` (WAL sidecars too). No-op here — this deploy
  predates SQLite and has only `state.json`.
- `state.json` → imported into `broadsheet.db`, then renamed `state.json.imported`.
  Best-effort; its contents (health, provider ETags) are re-derivable.

## Why the PDFs are safe (verified on the live PVC)

`/data` on the running pod (namespace `iot`, node `k8s-prod-3`):

```
/data/archive/{ca-lat,ca-sfc,can-ts,ma-bg,ny-nyt}/2026070{2,3,4,5}.pdf   # 20 PDFs
/data/cache/<id>/<date>.png                                              # disposable
/data/state.json                                                         # imported → SQLite
```

broadsheet's `archive.Store` roots at `$DATA_DIR/archive` and reads
`<id>/<YYYYMMDD>.pdf` verbatim — zero migration for the PDFs. Files are owned
`1000:1000`, matching the retained securityContext (`runAsUser`/`fsGroup: 1000`),
so ownership is untouched. Freedom Forum only serves ~2 days upstream, so the
older editions in this archive are genuinely unrecoverable if lost — hence the
care below.

## The hazard

The `paperboy` PVC is Helm-owned and the PV reclaim policy is **`Delete`**. A
naive rename would let Flux prune the `paperboy` HelmRelease → Helm uninstall
deletes the PVC → Longhorn destroys the volume and every PDF. The migration flips
reclaim to `Retain` first, then re-binds the same PV to a new `broadsheet` PVC.

**Invariant:** the PV is `Retain` before any PVC is deleted, and bound to
`iot/broadsheet` before broadsheet's pod schedules.

Key identifiers:
- PV: `pvc-1d47a183-b382-4e2e-a0d7-96ac4fbb2b08` (storageClass `longhorn`, RWO, 20Gi)
- Old PVC: `iot/paperboy` · New PVC: `iot/broadsheet`
- Flux Kustomization / HR / Deployment: all `paperboy` in `iot`

## Prerequisites (met)

- [x] `ghcr.io/kelchm/broadsheet:latest` published (also `sha-4f418a2`) and
      anonymously pullable (public — the cluster pulls kelchm/* with no secret).
- [x] Branch `feat/broadsheet-rename`: `kubernetes/apps/iot/paperboy/` →
      `broadsheet/`, image + env renamed, hostname `broadsheet.home.kelch.io`,
      `persistence.data.existingClaim: broadsheet`, plus a static `pvc.yaml`
      pinned to the PV via `volumeName`. `flux-local test` green (105 passed);
      chart render confirmed: no chart PVC, Deployment mounts `claimName: broadsheet`.

## Runbook

A short maintenance window: the display goes dark from paperboy teardown until
broadsheet is Ready. Two manual `kubectl` steps bracket a normal GitOps merge.

### 1 — Safety net (reversible, do first)

```sh
kubectl patch pv pvc-1d47a183-b382-4e2e-a0d7-96ac4fbb2b08 \
  -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
kubectl get pv pvc-1d47a183-b382-4e2e-a0d7-96ac4fbb2b08 \
  -o jsonpath='{.spec.persistentVolumeReclaimPolicy}{"\n"}'   # -> Retain
```

Optional second belt if Longhorn backups are configured: snapshot the volume now.

### 2 — Merge the rename

Merge `feat/broadsheet-rename`. Flux then, in one reconcile:
- Prunes the `paperboy` child Kustomization → GCs the HR → `helm uninstall
  paperboy` removes the Deployment/Service/SA/HTTPRoute and **deletes the
  `paperboy` PVC**. Reclaim=`Retain` keeps the PV → it goes `Released`, data intact.
- Applies `broadsheet`: creates the `broadsheet` PVC (`volumeName` pinned) — which
  parks in **`Pending`**, because the PV is still `Released` with a stale
  `claimRef` to the deleted `paperboy` PVC. The pod waits.

Watch it reach that state:

```sh
kubectl get pv pvc-1d47a183-b382-4e2e-a0d7-96ac4fbb2b08   # -> Released
kubectl get pvc -n iot broadsheet                         # -> Pending
```

### 3 — Release the PV to the new claim

```sh
# Clear the stale binding; the broadsheet PVC (volumeName-pinned) then binds.
kubectl patch pv pvc-1d47a183-b382-4e2e-a0d7-96ac4fbb2b08 \
  --type json -p '[{"op":"remove","path":"/spec/claimRef"}]'
```

Race-proof variant (reserves the PV for exactly `iot/broadsheet` instead of
leaving it briefly open to any pending 20Gi longhorn PVC):

```sh
kubectl patch pv pvc-1d47a183-b382-4e2e-a0d7-96ac4fbb2b08 --type json \
  -p '[{"op":"replace","path":"/spec/claimRef","value":{"apiVersion":"v1","kind":"PersistentVolumeClaim","namespace":"iot","name":"broadsheet"}}]'
```

### 4 — Verify

```sh
kubectl get pvc -n iot broadsheet                          # Bound to pvc-1d47...
kubectl get pods -n iot -l app.kubernetes.io/name=broadsheet   # Running, 1/1 Ready
kubectl logs -n iot deploy/broadsheet | grep -iE 'imported legacy|listening'
# archive intact (distroless: inspect via ephemeral container):
POD=$(kubectl get pod -n iot -l app.kubernetes.io/name=broadsheet -o name)
kubectl debug -n iot ${POD#pod/} --image=busybox:1.36 --target=app -c v \
  --attach=false -- sh -c 'find /proc/1/root/data/archive -name "*.pdf" | wc -l'
sleep 6 && kubectl logs -n iot ${POD#pod/} -c v          # -> 20
curl -sI https://broadsheet.home.kelch.io/healthz         # -> 200
```

## Post-cutover

- **Display URL:** repoint the wall display to `broadsheet.home.kelch.io`. The
  advance-on-GET `/` and `/current.png` still work but are deprecated (removed
  before 1.0); prefer the clock-driven `/rotation` (HTML) or `/rotation.png` (raw).
- **Reclaim policy:** restore the PV to `Delete` so its lifecycle matches the
  storageClass and no orphan `Released` PV lingers if the PVC is ever removed —
  Longhorn snapshots/backups are the DR mechanism, not a `Retain`'d PV. Keep
  `Retain` only if this volume has no Longhorn backup configured.
  `kubectl patch pv pvc-1d47a183-... -p '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}'`
- **Cache orphans (optional):** broadsheet re-renders to `<date>.w1600.png`; old
  `<date>.png` files (~tens of MB) linger. One-shot via ephemeral container:
  `find /proc/1/root/data/cache -name '*.png' ! -name '*.w*.png' -delete`.

## Rollback

The data is never at risk while reclaim=`Retain`. If broadsheet fails to start:
revert the merge (Flux restores paperboy), delete the `broadsheet` PVC, re-reserve
the PV `claimRef` for `iot/paperboy` (or recreate a `paperboy` PVC with the
`volumeName` pinned), and let paperboy's HR bind to it.
