# Repository labels

Issues and pull requests use a small fixed set of labels as coarse filters. Labels are organizational only; they do not determine rollout risk or drive deployment. [`.github/labels.yaml`](../.github/labels.yaml) is the catalog, and [`.github/labeler.yaml`](../.github/labeler.yaml) maps directories to labels.

## Scope labels

Scope labels follow the repository layout. A platform is a managed environment with its own tree; an area is a domain with its own directories.

| Label | Directories |
| --- | --- |
| `platform/kubernetes` | `kubernetes/`, `bootstrap/` |
| `platform/talos` | `talos/` |
| `platform/proxmox` | `proxmox/` |
| `platform/sparks` | `sparks/` |
| `platform/synology` | `synology/` |
| `platform/unifi` | `network/unifi/` |
| `platform/devices` | `devices/` |
| `area/ai` | `kubernetes/apps/ai/`, `sparks/inference/` |
| `area/identity` | `kubernetes/apps/identity/` |
| `area/network` | `network/`, `kubernetes/apps/network/` |
| `area/observability` | `kubernetes/apps/observability/`, `proxmox/monitoring/`, `sparks/monitoring/` |
| `area/storage` | `kubernetes/apps/longhorn-system/`, any `kubernetes/apps/<namespace>/storage/` |
| `area/docs` | `docs/`, `assets/`, every `README.md` |
| `area/tooling` | `scripts/`, `tools/`, `Taskfile.yaml`, agent instructions, root dotfiles and dot-directories such as `.github/` and `.taskfiles/` |

The label says where the change lives, not everything it is about:

- The PVE exporter and the Grafana dashboards in `kubernetes/apps/observability/` are `platform/kubernetes` and `area/observability`, whatever they monitor.
- Cilium lives in `kubernetes/apps/kube-system/`, so its network policy is `platform/kubernetes` only.
- Anything under `scripts/`, `tools/`, or `.taskfiles/` is `area/tooling`, whichever platform it operates on.

Issues have no paths; use the same table and pick the labels for where the work lands. Every issue except the Renovate Dashboard gets at least one `platform/*` or `area/*` label.

Extra scope labels are optional. Add one by hand when it helps someone find the work; there is no obligation to name every domain a change involves.

Map platforms and domains by directory; keep the common README and repository-configuration rules for docs and tooling. If a directory mixes concerns, reconsider its layout instead of adding component or resource exceptions.

## Priority, kind, and status

- Every issue gets exactly one `priority/*` label; the Renovate Dashboard is exempt.
- An issue carries at most one `kind/*` label, and only for a bug, an investigation, or a new deployment.
- `hold` marks a blocker or a revisit trigger; the issue or PR body names which.
- Renovate owns `type/*` and `renovate/*`; they are managed by its configuration.

## Grafana relationship

Grafana folders (see [`architecture.md`](architecture.md#dashboards)) organize dashboards by subject: the service, otherwise the platform, otherwise Overview. Labels organize repository work by directory, so the two overlap without mapping one-to-one, and a dashboard change needs no label for its subject. Labels imply no metric-label change: the existing `platform=pve` and `platform=spark` series keep those values.

## Automation

- PRs targeting `main` are labeled on open, reopen, and push with every label whose directories they touch; deleted files count.
- The labeler reads its rules from `main`, so a PR that edits the rules is not labeled by its own changes.
- Existing open PRs are not re-labeled when rules change. Replay one with `gh workflow run labeler.yaml -f pr-number=123`.
- Labeling is add-only (`sync-labels: false`): manual labels survive, a label whose paths left the PR stays until removed by hand, and removing a label that still matches brings it back on the next push.

## Changing the catalog

- Define labels only in `.github/labels.yaml`; label-sync deletes any live label absent from both the catalog names and their aliases, along with its associations.
- Rename through `aliases` and keep old aliases. An alias is a safe rename only while its target does not exist; if the target already exists the alias becomes a merge, and merges have lost PR associations in this repository (cause unverified, so do not assume every merge loses them).
- `scripts/ci/validate-labels.sh [ROOT_DIR] [LIVE_LABELS_JSON]` enforces exact unique names and aliases of at most 50 characters, descriptions of at most 100 characters, labeler keys that all exist in the catalog, and zero planned deletions or merges against the live-labels JSON. Regression tests live in `scripts/ci/test-validate-labels.sh`.
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
