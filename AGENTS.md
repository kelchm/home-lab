# Working in this repo

Solo-maintained homelab. Flux watches `main`; a GitHub webhook makes pushes live in seconds. Talos config (`talos/` tree) rolls manually via `talosctl` after merge — Flux doesn't touch it.

## Branch + PR vs direct-to-main

Branch + PR when the change is cross-cutting (multiple subsystems), can't be undone with `git revert` (Talos rollouts, secrets, anything mutating state outside Flux's reach), or needs a non-obvious manual step after merge.

Direct to `main` for small single-app pure-config changes — the recent "Shift Longhorn backup cron…", "Stamp cluster=k8s-prod…" commits are the shape.

Rule of thumb: would a bad version auto-deploying be embarrassing or just inconvenient? Embarrassing → branch.

## Planning, issues, and documentation

Plan and track unfinished work in GitHub issues; do not create new plan files in the repository. A small change completed in one PR can use the PR alone. Check existing issues and PRs before creating another tracker.

- Use the [Work item](.github/ISSUE_TEMPLATE/1-work-item.md) template for changes, defects, and operational tasks; use [Investigation](.github/ISSUE_TEMPLATE/2-investigation.md) for a substantial question or evaluation. Apply the same structure when creating issues through a CLI or API. Adapt the prompts to the work; a defect does not need a known solution to be filed.
- Give each issue a bounded outcome. An investigation ends with a supported conclusion, including keeping the current approach; create linked implementation work if the outcome calls for it. Routine choices can stay inside a work item. Parent issues link independently finishable work without duplicating its detailed checklists.
- Keep issue bodies easy to scan: brief context, actionable work, and a clear completion condition. Link detailed research and procedures instead of copying whole proposals; keep the decision criteria and constraints needed to act safely in the issue. Avoid duplicating the work checklist under completion criteria or pre-writing a follow-on rollout in an investigation.
- Keep the current next step or blocker and completion criteria accurate in the issue body. Deferred work names a revisit trigger. Close with the outcome and verification evidence; merging a PR does not complete an unperformed manual rollout or acceptance check.
- Update current-state docs and runbooks with implementation. Write procedures before risky operations, clearly distinguishing intended behavior from verified state. Keep durable decisions and useful evaluation results in the relevant documentation, progress in issues, and session handoffs in conversations.
- Retire existing plans incrementally: move remaining work to issues, preserve current behavior and operations in maintained docs, and retain useful rationale or evidence before archiving or removing a plan. Completed plans stay historical; later improvements get new issues.

## Markdown style

Do not hard-wrap prose in Markdown. Keep each paragraph on one source line and rely on soft wrapping. Wrap only when Markdown syntax or readability requires it. Do not reflow existing prose merely to satisfy a column limit.

## Talos operations

Before any Talos upgrade or node reboot, load and follow the repository's `talos-rollout` skill. Run the guarded `task talos:upgrade-node IP=<node-ip>` task for upgrades; it deliberately uses `--reboot-mode=powercycle` because these nodes have hung during the default kexec reboot path.

Operate on one node at a time. Do not continue until etcd has quorum, every Kubernetes node is Ready, every Longhorn volume is healthy, and every Longhorn instance-manager is Running and Ready with its `longhorn-system/storage-network` attachment on `lhnet1`.
