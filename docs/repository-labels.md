# Repository labels

Issues and pull requests here use a small fixed label taxonomy to make work discoverable and scoped. Labels are organizational only; they do not determine rollout risk or drive deployment. [`.github/labels.yaml`](../.github/labels.yaml) is the source of truth for the catalog, and [`.github/labeler.yaml`](../.github/labeler.yaml) maps touched paths to labels.

## Catalog

### Platforms

A platform label names a managed environment or operating layer directly involved in the work, including platform-specific integrations, investigations, and runbooks. Label the layer being operated on, not every hosting ancestor or dependency.

| Label | Covers |
| --- | --- |
| `platform/kubernetes` | Cluster resources, workloads, Flux, bootstrap |
| `platform/talos` | Machine configuration, OS, node hardware |
| `platform/proxmox` | PVE hosts, guests, and their integrations |
| `platform/sparks` | Sparks hosts, direct fabric, inference |
| `platform/synology` | DSM, NAS configuration, Compose workloads |
| `platform/unifi` | UniFi controller, gateways, switching, wireless |
| `platform/devices` | Standalone appliance configuration and operations not covered by another platform (currently GLKVM) |

Today Kubernetes and Talos manage the same three physical machines at different layers; pick the layer the work operates on. `platform/devices` is for administering an appliance itself, not for an app that merely talks to one (for example, printing).

### Areas

An area label names a directly involved domain, including operations on its components. It describes what the work touches, not a promise of its primary purpose.

| Label | Covers |
| --- | --- |
| `area/network` | Connectivity, routing, DNS, ingress, firewall, remote access |
| `area/observability` | Metrics, logs, dashboards, alerts, probes |
| `area/storage` | Persistence, filesystems, volumes, shares, backups, restores, integrity |
| `area/ai` | Models, inference, agents, MCP |
| `area/identity` | Shared sign-in, account policy, SSO integrations, identity service operation |
| `area/docs` | Documentation files: `docs/**`, `assets/**`, the root `README.md`, any `**/README.md` |
| `area/tooling` | Repository automation, CI, dependencies, validation, shared operator tooling, agent instructions and skills |

Boundary notes:

- A routine per-workload token or secret does not make work `area/identity`.
- Not every script or tool is `area/tooling`; platform-specific tasks stay with their platform.
- Not every Markdown file is `area/docs`: `SOUL.md` is runtime prompt configuration (`area/ai`), agent instructions and skills are `area/tooling`, and comments inside configuration files never add `area/docs`. Mixed changes that do touch documentation files carry `area/docs`.

### Priority, kind, and status

- Every issue gets exactly one `priority/*` label; the Renovate Dashboard is exempt.
- An issue carries at most one `kind/*` label, and only for a bug, an investigation, or a new deployment.
- `hold` marks a blocker or a revisit trigger; the issue or PR body names which.
- Renovate owns `type/*` and `renovate/*`; they are managed by its configuration and unchanged by this taxonomy.

## Applying labels

- Issues get at least one `platform/*` or `area/*` label describing the work — one of each is not required, and the Renovate Dashboard is the only exception.
- Pull requests receive every label whose paths were touched, so a broad sweep legitimately accumulates labels. Add manual labels when paths understate the scope.

Examples; automatic labels come from path rules, the rest are manual scope:

| Change | Labels |
| --- | --- |
| PVE exporter deployed into the cluster | `platform/kubernetes`, `platform/proxmox`, `area/observability` |
| Sparks monitoring | `platform/sparks`, `area/observability` |
| UniFi README update | `platform/unifi`, `area/network`, `area/docs` |
| Docs-only evaluation of a Synology service | `area/docs` (automatic) + `platform/synology` (manual), plus `area/ai` if a model cache is explicit |
| Inline Kanidm comment in a HelmRelease | `platform/kubernetes`, `area/identity` — not `area/docs` |
| Dedicated runtime `SOUL.md` | `platform/proxmox`, `area/ai` — not `area/docs` |
| Repository agent instructions and skills | `area/tooling` |

## Grafana relationship

The Grafana tree (see [`architecture.md`](architecture.md#dashboards)) gives each dashboard one subject home: its service first, otherwise the platform whose machines, OS, or plumbing it shows, otherwise Overview. Label areas are intentionally broader than the Services folders.

- Longhorn's dashboard lives under Platforms/Kubernetes; a change to it can still carry `area/storage`. Cilium maps to `area/network` the same way.
- Dashboard and alert work carries `area/observability` even when the subject is Kubernetes; the Services/Observability folder is only for monitoring-system health.
- Services domains align loosely with areas such as AI and Identity, but there is no forced one-to-one mapping.
- Talos is folded into the Kubernetes dashboard folder, while `platform/kubernetes` and `platform/talos` stay separate labels for the two operating layers.
- `area/docs` and `area/tooling` have no Grafana equivalent, and Overview needs no label; use overlapping scopes on the issue instead.
- Repository files stay with their owning configuration. Labels do not imply a directory reorganization or metric-label migration: the existing `platform=pve` and `platform=spark` series keep those values. The name Sparks remains provisional.

## Automation

- The labeler evaluates rules from base/`main`, so a PR that edits the rules does not exercise its own new rules.
- PRs targeting `main` are labeled on open, reopen, and push; deleted files count as touched paths.
- Existing open PRs are not re-labeled when rules change. Replay one with `gh workflow run labeler.yaml -f pr-number=123`.
- `sync-labels: false` is explicit: manual supplements survive. The flip side is that a stale automatic label whose paths left the PR needs manual cleanup, and removing a still-matched label brings it back on the next push.
- A subject change inside a generic HelmRelease needs a manual supplement; there is no AI classifier.
- Review label rules when adding directories or new resource filename conventions. Most paths already inherit a platform; add the relevant areas or explicit cross-platform targets. The root `LICENSE` deliberately has no automatic scope.

## Changing the catalog

- Define labels only in `.github/labels.yaml`; label-sync deletes any live label absent from both the catalog names and their aliases, along with its associations.
- Rename through `aliases` and keep old aliases. An alias is a safe rename only while its target does not exist; if the target already exists the alias becomes a merge, and merges have lost PR associations in this repository (cause unverified, so do not assume every merge loses them).
- `scripts/ci/validate-labels.sh [ROOT_DIR] [LIVE_LABELS_JSON]` enforces exact unique names and aliases, descriptions of at most 100 characters, labeler keys that all exist in the catalog, and zero planned deletions or merges against the live-labels JSON. Regression tests live in `scripts/ci/test-validate-labels.sh`.
- Repository Validation previews the EndBug label-sync dry-run and the safety gate on PRs; the live LabelSync workflow repeats the gate before writing. A push to `main` that changes the catalog triggers live label sync; complete the migration preparation before merging.

### Migration procedure

Applying a catalog change to live labels happens at merge and is a deliberate sequence:

1. Snapshot every issue and PR label association, including closed items, plus the label IDs.
2. Disable the Labeler workflow and wait for all in-flight runs to finish.
3. Refresh the live catalog: `gh api --paginate repos/kelchm/home-lab/labels --jq '.[]' | jq -s . > /tmp/live-labels.json`.
4. Run the validator and review the dry-run against the fresh snapshot. The initial migration should show 6 renames, 5 creates, and description changes, with zero merges and zero deletions.
5. Merge the catalog, rules, and guidance together, then confirm the sync result.
6. Verify exact issue and PR association sets and preserved label IDs, not counts alone.
7. Re-enable the labeler, replay open PRs, and manually scope open issues; leave closed items alone — no semantic backfill.
8. Recheck that retired label names did not return.

Rollback: a plain `git revert` can erase associations carried by new labels. Roll forward instead with reverse aliases, preserve new labels still in use, and compare against the snapshot.
