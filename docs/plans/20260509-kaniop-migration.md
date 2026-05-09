# Kanidm pivot: hand-rolled StatefulSet → kaniop operator

**Status:** in flight (2026-05-09).
**Supersedes:** Phase 0 step 5 of `docs/plans/20260508-arr-suite-setup.md` (the Kanidm
substack only — the rest of the master plan is unchanged).

## Context

The hand-rolled Kanidm StatefulSet (1 replica, Kanidm 1.10.1) was recovered to a clean
state but never fully bootstrapped. PR 4's seed CronJob never successfully ran due to
an upstream CoreDNS flake (issue #36, since resolved); the only credential in the DB
is the `idm_admin` recovered via the manual `kanidmd recover-account` flow.

Reviewing [pando85/kaniop](https://github.com/pando85/kaniop) revealed it's a strictly
better fit than continuing with the hand-rolled approach. It provides declarative
`Kanidm`, `KanidmGroup`, `KanidmPersonAccount`, `KanidmOAuth2Client`, and
`KanidmServiceAccount` CRDs; manages replication topology end-to-end (no manual cert
exchange); generates per-client OAuth2 secrets into per-namespace Kubernetes Secrets;
and lays out the right path for `traefik-oidc-auth` integration via per-app declarative
clients in each consumer's namespace.

This pivot lands now while the hand-rolled DB is still empty — wipe-and-redo cost is
zero. It replaces the planned PR 5 (manual replication bootstrap) and reshapes PR 6
(which gains a cleaner Secret-discovery path).

## Decisions

| | Decision | Why |
|---|---|---|
| **Operator** | kaniop v0.6.1, OCI chart `oci://ghcr.io/pando85/helm-charts/kaniop` | Active maintenance (releases every 2-3 weeks, last commit today), AGPLv3 (matches Kanidm), Gateway API native, only 4 open issues. |
| **Kanidm image** | `kanidm/server:1.9.2` | kaniop builds against `kanidm_client = "1.9.0"`. Matches pando85's own homelab pairing. Avoids the cosmetic "incompatible version" condition in the Kanidm CR status. Downgrade is free for us — DB is empty. |
| **Replicas at migration** | 1 | Smaller blast radius. Scale to 3 in PR E once stable. Forward-compatible with the original HA target. |
| **Namespace** | `identity` (single namespace, kaniop and kanidm as sibling apps inside) | Surveyed 8 community repos running Kanidm: `security/` (3) and `identity/` (1) dominate; workload-named `kanidm/` is a minority pattern. `identity/` is the most semantically precise — survives a hypothetical product swap (Kanidm → Authentik would still describe the contents). Operator and workload as siblings (`identity/kaniop/` + `identity/kanidm/`) rather than bundled into a single app dir, to keep their Flux Kustomizations independent. |
| **Reverse-proxy IP attribution** | `KANIDM_TRUST_X_FORWARD_FOR=true` env var + CiliumNetworkPolicy restricting `:8443` ingress to Traefik pods | NetworkPolicy is the actual trust boundary — kaniop's V2-config CIDR scoping wouldn't buy anything extra, since pod IPs come from a single CIDR for everyone in-cluster. PROXY v2 is the architecturally-correct answer per Kanidm docs but isn't worth blocking on (LDAPS is in-cluster only, so no real attribution gap). Decision-trail: chat 2026-05-09. |
| **OAuth2 client management** | Per-app `KanidmOAuth2Client` CR in each consumer's namespace | Replaces the centralized seed CronJob. kaniop generates `<client-name>-kanidm-oauth2-credentials` Secret per client in the same namespace; consumer reads it directly via SOPS-free reference. Cross-namespace discovery enabled via `oauth2ClientNamespaceSelector: {}` on the Kanidm CR. |
| **Identity seeding** | Declarative `KanidmGroup arr-admins` + `KanidmPersonAccount kelchm` in `identity` namespace | Replaces seed CronJob. Initial password set via the credential-reset URL surfaced in `kubectl describe kanidmpersonaccount kelchm` — kaniop emits it for `credentialsTokenTtl` seconds (default 3600). |
| **Application-consistent backups** | `KANIDM_ONLINE_BACKUP_SCHEDULE='0 5 * * *'` (01:00 ET) + `KANIDM_ONLINE_BACKUP_VERSIONS=7` env vars on Kanidm CR | Daily SQLite-online-backup-API dump to `/data/backups/`. Two-hour gap before Longhorn's 03:00 ET PVC backup picks up both the live DB and the consistent dump. |
| **Volume-level backups** | Inherited from existing default `RecurringJob` (daily 03:00 ET retain 7, weekly Sunday 04:00 ET retain 4) | No new Longhorn config; new PVCs auto-join the `default` group. BackupTarget at `nfs://10.32.25.5:/volume1/backups-k8s-prod/longhorn` already in place. |
| **HTTPRoute** | Keep the hand-rolled `httproute.yaml` rather than use kaniop's `spec.gateway` field | Preserves existing parentRef/sectionName + BackendTLSPolicy contract. kaniop's gateway field would re-create equivalent objects with operator-owned naming. No upside, just churn. |

## Affected files

**Delete entirely** — wrong-namespace placements from PR A and pre-pivot:
- `kubernetes/apps/kaniop/` — PR A's standalone-namespace placement, superseded by `identity/kaniop/`.
- `kubernetes/apps/auth/` — pre-pivot hand-rolled placement, superseded by `identity/kanidm/`.
- `docs/runbooks/kanidm-bootstrap.md` — runbook for the hand-rolled bootstrap flow, superseded by `kanidm-kaniop-cutover.md`.

**Add** under `kubernetes/apps/identity/`:
- `namespace.yaml`, `kustomization.yaml` — new `identity` namespace at the standard top-level shape.
- `kaniop/{ks.yaml, app/{kustomization.yaml, ocirepository.yaml, helmrelease.yaml}}` — operator HelmRelease.
- `kanidm/{ks.yaml, app/{kustomization.yaml, certificate.yaml, kanidm.yaml, group-arr-admins.yaml, httproute.yaml, backendtlspolicy.yaml}}` — Kanidm CR + identity resources + cert + ingress.
- `kanidm/app/person-kelchm.yaml` — `KanidmPersonAccount` (PR C, not B).
- `policies/{kustomization.yaml, kanidm.yaml}` — CiliumNetworkPolicy (PR C, not B).
- `docs/runbooks/kanidm-kaniop-cutover.md` — one-time wipe-and-redo (PR B).
- `docs/runbooks/kanidm-restore.md` — restore drill procedure (PR C).

**Update**:
- `docs/plans/20260508-arr-suite-setup.md` — pointer at Phase 0 step 5 to this pivot doc (already in place from PR A).

## PR sequence

| # | Title | Risk | Scope |
|---|---|---|---|
| ~~A~~ | ~~Install kaniop operator in standalone namespace~~ | ~~low~~ | Merged. Placement wrong; superseded by PR B (which dissolves the standalone `kaniop` namespace and relocates the operator into `identity/kaniop/` alongside kanidm). |
| **B** | Cutover hand-rolled Kanidm → kaniop-managed; relocate kaniop into `identity/` | high | Destructive (PVC wipe). Combined: deletes both `kubernetes/apps/auth/` and `kubernetes/apps/kaniop/`, builds `kubernetes/apps/identity/` from scratch with kaniop and kanidm as siblings. Single replica through cutover. KanidmGroup arr-admins lands here so the operator has something to reconcile besides the cluster CR itself. Cutover runbook drives the sequence. |
| **C** | Harden + backups + kelchm | medium | NetworkPolicy lockdown in `identity` namespace. `KANIDM_TRUST_X_FORWARD_FOR=true`, `KANIDM_ONLINE_BACKUP_*` env vars on the Kanidm CR. KanidmPersonAccount kelchm. Restore runbook. |
| (ops) | Restore drill | n/a | Follow `docs/runbooks/kanidm-restore.md` against a copy of production data. Sign-off step before D+. |
| **D+** | Per-app KanidmOAuth2Client during arr-suite deployment | n/a | Per-app PRs in `media/`. kaniop emits client_secret into a per-namespace Secret. |
| **E** | Scale Kanidm CR from 1 → 3 replicas | low | One-line bump on `replicaGroups[0].replicas`. Operator handles peer cert exchange. Resolve open question on online_backup replica scoping before this lands. |
| **F** | traefik-oidc-auth plugin + kanidm-oidc Middleware | medium | Original PR 6 scope. Now reads `arr-suite` client_secret from kaniop-emitted Secret instead of a hand-managed SOPS file. |

Tasks tracked in the home-lab task list as #17–#22 + #7 + #8.

## Cutover runbook outline

Lives in `docs/runbooks/kanidm-kaniop-cutover.md`, written as part of PR B. Walks the
operator-relocation + hand-rolled-Kanidm-wipe + reconcile cycle. Key steps:

1. Suspend Flux on the old `kaniop` and `auth-kanidm` Kustomizations.
2. Scale the hand-rolled StatefulSet to 0 and delete its PVC (irreversible — the
   data wipe).
3. Merge PR B.
4. Resume Flux. cluster-apps reconciles → deletes the old `kaniop` namespace and the
   old `auth` namespace via prune → creates the new `identity` namespace, installs
   kaniop, then provisions the Kanidm CR.
5. Verify operator pod in `identity` namespace, Kanidm CR ready.
6. Retrieve idm_admin password from kaniop-managed `kanidm-admin-passwords` Secret.
7. Sanity-check web UI; verify `arr-admins` group reconciled.

## Restore runbook outline

Lives in `docs/runbooks/kanidm-restore.md`, written as part of PR C. Covers three
failure modes ordered by likelihood:

- **DB-only corruption** (most common — bad migration, accidental delete via API,
  human error in the web UI). Use the latest `/data/backups/<timestamp>.bak` already
  in the PVC: scale Kanidm CR to 0, exec into a debug pod with the PVC mounted, run
  `kanidmd database restore /data/backups/<file>.bak`, scale back to 1.
- **PVC loss** (Longhorn replica corruption, accidental PVC delete). Restore the most
  recent Longhorn backup to a new PVC. Two paths: (a) run with the live DB —
  crash-consistent, Kanidm replays the journal; or (b) restore from the bundled `.bak`
  for guaranteed-consistent state. (a) is faster, (b) is safer.
- **NAS loss** (catastrophic). In-PVC `.bak` files retained 7 days only; cluster-wide
  off-site replication gap, not Kanidm-specific. Out of scope for this runbook.

The restore drill (post-PR-C operational sign-off) exercises the DB-only path against
a copy of production data — proves the `.bak` files are usable end-to-end.

## Open questions

1. **Backup scoping at scale-to-3**. When PR E lands, `online_backup` will run on
   every replica unless Kanidm scopes it via `role`. Confirm whether Kanidm 1.9.x
   honors per-replica scoping (likely via `KANIDM_ROLE`), or whether we accept three
   identical daily dumps. Resolve before PR E. Not blocking now.
2. **Cross-namespace OAuth2 client discovery scope**. Kanidm CR's
   `oauth2ClientNamespaceSelector: {}` (empty selector = all namespaces) is the most
   ergonomic; alternatively, scope to `media` only. Lean: `{}` — simpler, matches
   pando85's own homelab. Revisit if we ever need namespace-level OAuth2 isolation.
3. **`KanidmPersonAccount` initial password TTL**. kaniop's
   `credentialsTokenTtl` defaults to 3600 (1h). Long enough for hands-on bootstrap;
   short enough that a leaked URL self-expires. Keep default unless we hit it.
