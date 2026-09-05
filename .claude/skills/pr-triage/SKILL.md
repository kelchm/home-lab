---
name: pr-triage
description: >-
  Triage the open pull requests in this Flux GitOps home-lab repo and safely merge
  the ones that are safe to merge. Use this whenever the user wants to go through,
  review, clean up, or merge open/outstanding PRs — including Renovate dependency
  bumps — e.g. "triage the PRs", "let's go through the open PRs", "merge what we
  safely can", "clean up the Renovate PRs", "what can we merge", or when a batch of
  Renovate PRs has piled up. Orchestrates the full pipeline: read-only scouting,
  deep per-PR changelog/breaking-change analysis, risk tiering, explicit
  per-target approval, conflict-aware merging, and post-merge rollout verification.
  Prefer this over ad-hoc `gh pr merge`, because every merge to main auto-deploys
  via Flux and this skill is what keeps the safety gates and rollout checks from
  being skipped.
---

# Triage & safely merge open PRs

## The one thing that governs everything

This is a **Flux GitOps repo**: merging a PR to `main` → Flux reconciles → it
**deploys to the LIVE cluster**. There is no staging. So:

- **Green CI is necessary, not sufficient.** `flux-local` proves a change *renders*,
  not that it's *safe to deploy*. The real question for every PR is: *what happens
  on the running cluster the moment this lands?*
- **Never batch-merge a set of PRs you selected without the user's explicit,
  per-target approval.** A general "merge what's safe" delegates the *judgment* to
  you; it is not approval of specific production targets. The auto-mode classifier
  enforces this (a bulk `gh pr merge` of agent-picked PRs gets denied), and that
  guardrail is correct — work *with* it: analyze, propose, get a yes, then merge.

Your job is to be the safety filter, not a merge button. Move deliberately.

## Pipeline

Scout → Analyze → Tier → Approve → Merge → Verify. Track it with a task list; a
real run touches 20+ PRs and spans several tool round-trips.

### Phase 0 — Scout (read-only, do this first)

```bash
scripts/pr-triage-scout.sh
```

Read the whole output before forming any opinion. It gives you:
- the **open-PR table** (mergeability, CI rollup, size, update type);
- **coupling clusters** — files touched by >1 PR. Merging one makes the others
  stale, so they merge one-at-a-time and expect a rebase; adjacent-line edits
  (classically `.mise.toml`) can even hard-conflict;
- **semantic pairs** — different PRs bumping the *same* component (e.g. a chart
  split across a bootstrap-CRD file and its HelmRelease). These usually must land
  **together** or you deploy a version skew;
- **merge mechanics** — branch protection, allowed merge methods, Renovate scope.

### Phase 1 — Deep analysis (Workflow fan-out)

Trivial dev-tool/patch bumps you can eyeball. Everything else gets a real,
changelog-backed verdict. **Read `references/analysis.md`** — it has the agent
prompt templates, the structured verdict schema, and (critically) the checklist of
context the agents can't see and you must inject: in-flight plan docs
(`docs/plans/*` + uncommitted), repo `MEMORY.md` hazards, and cluster reality.

The non-obvious failures live here, so don't shortcut it:
- A **semver-minor can be secretly breaking** — an operator bump that renames a
  CRD and runs a migration on live data is "0.10→0.11" on the tin.
- **In-flight work overrides "keep current"** — bumping a component a plan is about
  to delete, or one mid-migration, is churn at best and interference at worst.
- Make agents fetch the **real upstream changelog for the exact version range** and
  prove whether each breaking change actually applies to *this* repo's config.
  Never accept a guessed "probably fine".

### Phase 2 — Tier & present

Collapse the verdicts into tiers and present a scannable summary (a table per tier
with component, version, what-rolls, and the one-line reason). Use the framework
below. Call out coupling, semantic pairs, and any prerequisites explicitly.

### Phase 3 — Get explicit approval

Use **AskUserQuestion** to confirm the merge set — typically one question per tier,
multiSelect for the "safe but rolls infra" tier. Recommend, but let the user pick.
Merge **only** what they name. Anything held gets a clear gate and next action.

### Phase 4 — Merge (conflict-aware)

Match the repo's method (squash + delete-branch here). Merge **one at a time** so a
failure is isolated, and land semantic pairs as a set:

```bash
gh pr merge <n> --squash --delete-branch
```

Handle the coupling you found in Phase 0:
- **Same-file cluster** (e.g. six `.mise.toml` bumps): after the first lands the
  rest are behind `main`. Non-adjacent lines usually still merge; **adjacent lines
  can conflict.** On failure, try `gh pr update-branch <n>` and re-merge.
- **When server-side rebase can't resolve it** (and it often can't for a Renovate
  branch, and `git push` over SSH is unavailable non-interactively), rebase the
  branch's file **via the GitHub Contents API** — no local checkout, doesn't touch
  the user's working tree:

  ```bash
  # Set the conflicted file on the PR branch to main's content + this PR's change,
  # which erases the conflict; then merge.
  gh api "repos/$REPO/contents/<path>?ref=main" --jq .content | base64 -d > /tmp/f
  #  …apply this PR's one-line edit to /tmp/f (e.g. sed the version bump)…
  SHA=$(gh api "repos/$REPO/contents/<path>?ref=<branch>" --jq .sha)
  gh api -X PUT "repos/$REPO/contents/<path>" -f message="<renovate-style msg>" \
    -f branch="<branch>" -f sha="$SHA" \
    -f content="$(base64 -i /tmp/f | tr -d '\n')"
  gh pr merge <n> --squash --delete-branch
  ```

Report merged / failed as you go; never retry a denied merge verbatim.

### Phase 5 — Verify the rollout

Merging is only half the job — confirm it actually landed healthy on the cluster:

```bash
scripts/pr-triage-verify.sh          # from the repo root
```

It checks, in the order that matters: Flux caught up to the latest commit →
Kustomizations Ready → every HelmRelease Ready (and prints deployed versions so you
can **cross-check merged targets upgraded AND held PRs did not**) → no unhealthy
pods → certs Ready → recent warnings. Interpreting it:
- **Completed Jobs** (phase Succeeded, 0/1 ready) are not failures — the script
  already excludes them.
- **Warnings during a roll** that reference *old/terminating* pod IPs (readiness
  probes on pods being replaced) are expected churn. Judge by current pod state.
- A **transient `UpgradeFailed` that Flux retries to success** is fine — check the
  HR's current status, not the one-off event.
- The **monitoring MCP tools may themselves be in the rollout** (the Flux/k8s MCP
  servers run in-cluster). If they flake, that's why — `kubectl` (what the script
  uses) is the reliable path.

## What "safe" means here — the tiering framework

| Tier | Merge stance | What it looks like |
|---|---|---|
| **Inert** | merge-now | Changes nothing running: `.mise.toml` dev tools, `talos/talenv.yaml` desired version, bootstrap-only CRD seeds. Safest tier. |
| **Low-risk live roll** | merge-now | Patch bump that rolls a non-critical / self-healing pod (dev MCPs, a tunnel, a gateway). Note *what restarts*. |
| **Safe but rolls infra** | merge-coordinated | Verified-inert breaking changes, but reconciles critical/in-flight infra (cert-manager, monitoring stack). Land in a window; get an explicit yes. |
| **Feature / prerequisite** | merge-coordinated | Net-new deploy that's isolated but needs a manual step first/after (a DB role, an OAuth client, DNS). |
| **Do-not-batch / hold** | hold | Breaking migration on critical data, or collides with in-flight work. Needs a supervised window or should be closed. |

## Hard-won gotchas

- **Talos installer bumps are inert on merge** — they set the *desired* node
  version. The actual node upgrade is a separate, manual, **strictly one-node-at-
  a-time** op. Never treat merging one as "the upgrade is done."
- **An operator's "minor" can mandate a data migration.** If the changelog shows a
  CRD rename/migration, check whether the HelmRelease needs a **`spec.timeout`
  bump** before merge (a long migrate Job vs Flux's short default = a scary Failed
  upgrade). Merge it deliberately, watch the hook Jobs.
- **Held PRs are not "no" forever** — record the gate (the prerequisite, the
  window, the decision) so the next pass knows exactly what unblocks each one.
- **macOS has no `timeout`.** Running `kubectl` locally (out-of-cluster) doesn't
  need it anyway; don't paste the in-cluster `--request-timeout` habit here.
- **Non-interactive git is HTTPS-only.** `origin` is SSH and hits the 1Password
  agent, which is unreachable headless — so `git fetch/push` fails and your local
  `origin/main` may be stale. Use `gh` (API + credential helper) for git network
  ops, and trust the server-side state over the local ref.

## Related

- `scripts/pr-triage-scout.sh`, `scripts/pr-triage-verify.sh` — the two read-only
  helpers this skill drives.
- `references/analysis.md` — Phase 1 agent prompts + verdict schema.
- `deploy-broadsheet` skill — the single-app deploy path (different job: no triage,
  direct-to-main is correct there).
