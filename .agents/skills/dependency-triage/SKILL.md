---
name: dependency-triage
description: Triage, merge and verify Renovate dependency-update PRs in this home-lab repo, where a merge to main deploys through Flux within seconds. Use when asked to triage, work through, land or merge the Renovate or dependency PRs, or to list outstanding dependency updates.
---

# Dependency triage

Renovate opens PRs every weekend. Only GitHub Actions updates automerge; every other merge is yours, with no branch protection behind you. Anything under `kubernetes/` is live seconds after merging, and green CI only proves the change renders.

## Size up the batch

- Get the queue from `gh pr list --author app/renovate --limit 100 --json number,title,labels,mergeable,statusCheckRollup` (the default limit of 30 has been smaller than a batch). `type/*` labels give the update type, `!` marks a major, and `platform/*` and `area/*` describe its scope; the paths below determine how it deploys. The Renovate Dashboard issue lists updates awaiting approval.
- Account for every open PR. One left out of the summary is how a sensitive update slips into a routine merge.
- Before merging, record the health of Flux, pods and alerts, so new breakage can be told from old.
- Merge a component split across PRs as a set, for example a CRD chart and its OCI image, or a `bootstrap/` seed and its HelmRelease.
- PRs that edit the same file, usually `.mise.toml`, conflict after the first merges. Renovate rebases them within minutes.
- Read failing jobs, and rerun registry or network flakes. A newly required values key means upstream changed behaviour, so read the notes before adding it.
- CI results age as `main` moves. `gh pr update-branch <n> --rebase` refreshes them.
- Present the batch in tiers (inert, routine, needs care, hold) with a proposed order.

## Where a merge lands

| Path | On merge |
| --- | --- |
| `kubernetes/` | Flux applies it within seconds |
| `synology/<project>`, if listed in `synology/.doco-cd.yml` | doco-cd deploys it within about three minutes |
| `synology/platform/doco-cd` | Nothing; applied by hand |
| `talos/` | Nothing until nodes are rolled with the `talos-rollout` skill |
| `bootstrap/` | Nothing; only a cluster rebuild reads it |
| `proxmox/guests/`, `sparks/` | Nothing until deployed by hand, as each README describes |
| `.mise.toml` | Local tools and CI validators only |

Report merges that only staged a manual rollout.

## Judge risk

Rate a PR by what changes on the running system, not by its semver label.

- **Small bumps can be large.** An operator patch can change or restart the server it manages. A minor can carry a CRD migration. A digest on a `latest`-tracking image can be a whole release. Check the image that actually runs, since chart versions are not app versions.
- **Majors can be empty.** A major may only touch a subchart this repo disables. Render both versions with the repo's values before deciding.
- **HelmRelease upgrades retry.** `kubernetes/flux/cluster/ks.yaml` makes every HelmRelease replace chart CRDs on upgrade. A failed upgrade rolls back and retries twice, rerunning hooks, so a forward-only migration can run again. After a CRD rename, a Kustomization that fails citing the old plural needs a kustomize-controller restart.
- **Chart defaults can change rollouts.** For example, DaemonSet `maxUnavailable` above 1 breaks one-node-at-a-time.
- **External writers can delete real state.** Controllers such as external-dns, which writes Cloudflare records, act on their first sync. Read the upgrade notes, stage with a dry-run and inspect the planned actions.
- **Identity is never routine.** Keep kanidm, kaniop and the Traefik OIDC plugin out of routine batches, since a broken login path locks out everything behind it.
- **Some updates merge alone.** app-template re-renders every app built on it. The flux-operator group also bumps the manifests artifact the FluxInstance installs from, which upgrades the Flux controllers. The `flux-instance` Kustomization doesn't wait, so a Ready HelmRelease doesn't prove the controllers rolled.
- **Renovate limits are deliberate.** Work within those in `.renovaterc.json5`.
- Hold or close updates to components that are being retired or no longer run, after checking that nothing still depends on them. A security fix jumps the queue once its preconditions are confirmed here.

## Read the real changelog

- Renovate's embedded notes are only a pointer: they have described the wrong project, and digest bumps have none. Read upstream notes for every release in the range, covering both the chart and the app it ships.
- Grep the repo for each breaking change or new default. An option the repo leaves unset takes the new default.
- If the notes are ambiguous, read the source at the tag. For compiled dependencies, read the lockfile, not the manifest's range.
- Render with `flate diff all --path kubernetes/flux/cluster --path-orig <primary-checkout>/kubernetes/flux/cluster -o github`. Leaving out `--path-orig` has produced an empty diff. flate is pinned in `.mise.toml`; the diff is of rendered output, not a git patch.
- CI's Flate PR Diff comments are not a full diff. They strip chart and version labels by default and are omitted when empty, so a missing comment doesn't mean nothing changed. The job summary holds the same diff.

## Merge

- Merge inert pins first, then routine single-app updates as a group. Merge everything else one PR at a time, verifying between merges.
- Use `gh pr merge <n> --squash --delete-branch --match-head-commit <sha>` so you merge the head you reviewed.
- A `talos/` bump changes no node. The user schedules the node rollout, which follows the `talos-rollout` skill. A `talosctl` bump changes only the local and CI CLI.
- Put a Renovate PR's fix on its branch. Under `AGENTS.md`, a small single-app fix exposed by a rollout can go straight to `main`, such as a missing egress rule for a chart hook or a probe expecting dropped behaviour. Larger fixes get their own PR.
- In a worktree, symlink the primary checkout's gitignored `kubeconfig` and `talos/clusterconfig/*` first. `.mise.toml` points `KUBECONFIG` into the worktree, and the failure looks like a dead API server.

## Verify each rollout

- Confirm Flux fetched the merge commit, then run `flux get all -A --status-selector ready=false` and check for unhealthy pods.
  - An upgrading release shows Unknown.
  - A release that exhausts its retries stalls. After fixing the cause, suspend and resume it; a forced reconcile has failed to clear one.
- Confirm the pods run the new version; the HelmRelease recording it isn't enough.
- Check output, not just readiness: DNS records exist and none were deleted, OIDC redirects, an MCP tool call succeeds, replicas converge. Read the whole log window and confirm the selector matched pods; an empty result is not health.
- After every live merge, confirm no warning or critical alert is firing, because those page the user. Query `ALERTS{alertstate="firing",severity=~"warning|critical",evaluator="vmalert"}` in VictoriaMetrics; the Grafana MCP can run it. Alternatively, read `/api/v2/alerts` from `pod/vmalertmanager-victoria-metrics-k8s-stack-0` on port 9093. `docs/runbooks/alerting.md` covers routing and first response. Judge a startup error by whether it recurs.
- Name any check only the user can make, such as an SSO login.

## Hold, close, roll back

- Hold a PR with a comment naming the reason and what would clear it. Link an issue when clearing it takes real work.
- Don't close a Renovate PR to hold it: Renovate then ignores that version.
- An open PR hides lower releases of the same dependency, security patches included. To get one, cap the range in `.renovaterc.json5` or write the patch PR.
- Renovate closes superseded and obsolete PRs itself. Close one by hand only when your PR replaces it (link the replacement) or the user decides.
- Roll back by reverting the commit, because Flux undoes live changes. When the missed prerequisite is known and Git holds the desired state, the user prefers rolling forward with the fix.

## What needs the user

Once asked to work through the updates, you can merge inert and routine updates, merge the rest one at a time with verification, fix CI on Renovate branches, land small fixes and record holds.

Ask first for identity-layer updates, Talos node rollouts, one-way data or schema migrations (take and test a recovery point first), operator upgrades that restart singleton databases, external-state writers, anything in a vendor-locked stack (sidecars included), new defaults that touch the user's data, retire-or-keep decisions and Renovate policy changes.

When asking, say what you checked and what could still go wrong. Skip ceremony, such as speculative guardrails or backups of state Git already holds. Verify incident claims before acting on them, and own your mistakes plainly.

## Done

Every open Renovate PR is merged and verified, held with a recorded reason, or closed with a reason. Flux, pods and alerts are back to baseline, and post-merge `main` CI is green.

The report covers merges with evidence, holds with what would clear them, staged-only merges, checks left for the user, fixes made along the way, and any mistakes.
