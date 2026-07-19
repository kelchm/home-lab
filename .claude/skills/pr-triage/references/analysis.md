# Phase 1 — deep per-PR analysis (reference)

This is the reusable machinery for the analysis fan-out. The goal: for every PR
that isn't a trivial, obviously-inert bump, produce an evidence-backed verdict on
whether it is safe to deploy to the live cluster *right now*.

## How to run it

Use the **Workflow** tool to fan out — one agent per judgment-call PR, plus one
agent that batch-verifies all the trivial bumps. They're independent, so a single
`parallel()` barrier (you need every verdict before you can tier) is right.

Sizing:
- **One dedicated agent** per: feature PR, minor/major bump, anything touching
  identity/cert/networking/storage/CRDs, anything in an area under active rework,
  and either half of a semantic pair.
- **One batch agent** for the rest (dev-tool/mise bumps, dev-only image patches).

Give each agent `gh`, `WebFetch`, `WebSearch`, `Read`, `Bash`. Force structured
output with the schema below so results come back uniform.

## Context the agents DON'T have — inject it

An agent reading only the diff will miss why a technically-safe bump is the wrong
action. Before/while fanning out, gather and pass down:

1. **In-flight plan docs.** Read `docs/plans/*` and anything uncommitted
   (`git status`, `git diff`). A component that a plan schedules for **removal**
   or is mid-migration should usually be **held**, not bumped — upgrading a corpse
   is churn, and bumping a component mid-decouple can fight the migration.
2. **Repo memory / prior incidents.** Skim `MEMORY.md` for hazards on the exact
   components in play (e.g. an operator whose "minor" bump is really a breaking CRD
   migration; a component whose upgrade needs a HelmRelease `spec.timeout` bump; a
   node-image bump that must stay one-at-a-time and manual).
3. **The cluster reality**, when it changes the call — is the thing even running,
   how many replicas, does anything depend on it. Agents can check read-only via
   `kubectl`/the Flux MCP.

**Chart version is not app version.** When judging "how big is this upgrade
really," check what the workload is *actually* running
(`kubectl get pod ... -o jsonpath='{.spec.containers[0].image}'`, or the chart's
`appVersion`) rather than inferring from the chart version. They drift, and a
scary-looking N-minor *chart* jump can be a small app delta (or vice versa). Say
which one you verified — an upgrade rated on the wrong axis produces a confidently
wrong risk call.

## Per-PR analysis prompt (template)

Fill in `<...>` and pass verbatim. Keep the standing rules identical across agents
so verdicts are comparable.

```
Repo: <owner/repo> — a Flux GitOps cluster. CRITICAL: merging a PR to main → Flux
reconciles → it DEPLOYS to the LIVE cluster. No staging. CI (flux-local) is green
on all these PRs and only proves they render, not that they're safe to deploy.

Home-lab facts to weigh: <paste the relevant plan-doc + memory facts here — e.g.
"observability is mid-rework: openobserve is slated for deletion"; "Talos node
upgrades are manual and strictly one-at-a-time; a talenv bump is inert until a
hands-on talosctl upgrade"; "kaniop 0.11 renames a CRD and runs a migration">.

Analyze PR #<n>: <component>, version <A -> B>.
1. `gh pr diff <n>` and `gh pr view <n>` — see the exact change.
2. Find the upstream release notes / CHANGELOG for the EXACT version range and read
   what actually changed. Do NOT guess. Use WebSearch + WebFetch (GitHub releases,
   Artifact Hub, chart CHANGELOG).
3. Focus on: breaking changes, CRD schema changes, Helm values renames/removals,
   removed/renamed flags, default-behavior changes, and anything needing a manual
   migration step. For each documented breaking change, state whether it actually
   applies to THIS repo's config (grep the repo to prove it).
4. Decide: merge-now (safe, no action) / merge-coordinated (safe but needs
   pairing, ordering, a spec change, or a follow-up manual step) / hold (real risk
   or collides with in-flight work).
Return the structured verdict.
```

## Feature-PR prompt (net-new deploys, not bumps)

```
Analyze feature PR #<n>: <desc>. NET-NEW deployment, not a version bump.
1. `gh pr diff <n>` / `gh pr view <n>` — read the WHOLE diff.
2. Merge-readiness for a live deploy: new namespace/CRD/operator blast radius?
   Secrets genuinely SOPS-encrypted (never plaintext)? Placeholder creds that make
   it crashloop on merge? NetworkPolicy/RBAC scoped sanely? A manual prerequisite
   (a DB role, an OAuth client, tailnet/DNS setup) that must exist first?
3. "Owner's own PR + green CI" is necessary, NOT sufficient — a 200-500 line
   new-operator deploy deserves real scrutiny. Note anything that makes it not a
   clean merge, and any pre-merge or post-merge manual step.
Set updateType="feature". Return the structured verdict.
```

## Verdict schema (JSON Schema for the Workflow `schema` option)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["pr","component","updateType","recommendation","risk","clusterImpact","breakingChanges","reasoning"],
  "properties": {
    "pr":              {"type": "number"},
    "component":       {"type": "string"},
    "updateType":      {"type": "string", "enum": ["patch","minor","major","feature","mixed"]},
    "recommendation":  {"type": "string", "enum": ["merge-now","merge-coordinated","hold"]},
    "risk":            {"type": "string", "enum": ["low","medium","high"]},
    "clusterImpact":   {"type": "string", "description": "what changes on the LIVE cluster on merge, or 'config-only, inert'"},
    "breakingChanges": {"type": "string", "description": "breaking changes for THIS exact version range, and whether each applies here; or 'none found in range'"},
    "reasoning":       {"type": "string", "description": "2-4 sentences: why this recommendation"},
    "coordinationNotes":{"type": "string", "description": "pairing/ordering/prereqs, e.g. 'merge with #189', 'raise spec.timeout first', or ''"}
  }
}
```

## Batch-trivial prompt (one agent for all the low-risk bumps)

```
Batch-verify these low-risk bumps are genuinely safe to merge-now. For EACH, run
`gh pr diff <n>` to confirm it's a clean single-line bump to exactly the stated
target and nothing sneaky, and a quick changelog sanity check only where warranted:
<list: #n tool/image current->target, one per line, noting which are inert
.mise.toml/dev-tool bumps vs which actually roll a live pod>
Flag any that AREN'T actually trivial, plus any shared-file (.mise.toml /
bootstrap) sequential-merge coupling. Return ONE verdict with pr=0,
component="trivial batch (N PRs)", updateType="mixed"; put a one-line per-PR
confirmation in reasoning and anything needing attention in coordinationNotes.
```

## Distinguishing inert vs live-rolling

A merge is **inert** (changes nothing running) when it only touches: `.mise.toml`
(local dev tooling), `talos/talenv.yaml` (desired node version — applied later by a
manual `talosctl upgrade`), or a bootstrap-only CRD seed marked not-used-by-sync.
It **rolls a live pod** when it changes an `OCIRepository`/`HelmRelease` chart
version or an image tag inside HelmRelease values. Inert merges are the safest
tier; live rolls — even low-risk patches — deserve a line in the tiering about
what restarts.
