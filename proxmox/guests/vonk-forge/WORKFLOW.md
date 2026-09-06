# Vonk Forge source workflow

Cleanup completed September 6, 2026. Four active worktrees were relocated, 22 historical checkouts were removed, and seven superseded fork PRs were closed with replacement links. Every existing local branch was retained; a verified all-refs bundle and exact dirty-worktree backup preserve recovery. The source evaluation remains separate from the paused physical deployment. See [source reassessment](SOURCE-REBASE.md) for dated findings and exact CI evidence, and [deployment notes](README.md) for the deployed generation and rollback.

## Repository and branches

The canonical checkout is `~/Development/kelchm/vonk-forge`, with `origin` pointing to `kelchm/vonk-forge` and `upstream` to `CarstVaartjes/vonk-forge`. Keep its `main` as a fast-forward mirror of upstream, without evaluation patches. Updating this mirror does not rebase existing candidates or qualify a deployment.

| Branch family | Purpose |
| --- | --- |
| `main` | Clean upstream mirror in the canonical checkout. |
| `patch/<topic>` | One bounded correction under development; record its exact upstream base and dependencies. |
| `upstream/<topic>` | Reviewed, signed submission candidate with evidence for its exact head. |
| `integration/evaluation` | Aggregate evaluation candidate only; never use its combined diff as an individual upstream contribution. |
| Existing `review/*`, `rebase/*`, `fix/*`, `experiment/*` branches | Retained historical evidence or explicitly held work; names alone do not establish current applicability or approval. |

The canonical checkout and fork `main` now match fetched upstream `f1fc79a71bcc1c86cda9db936803fd74b7f2eb54`, advanced by fast-forward only. Local `main` pulls from upstream with fast-forward-only policy and pushes to the fork. This includes upstream PR 610's APT publication changes; those changes have not been evaluated here and do not establish that a trusted native upgrade is available.

The published `integration/evaluation` branch points to the exact tested `9462ef6548eea46d7df3765a79ca11314e63a02e`. `rebase/integration-4430` remains as its historical reference. The aggregate is 19 commits ahead and 11 behind the fetched upstream snapshot; it was deliberately not rebased during cleanup. Do not silently advance the aggregate to newer upstream: that produces a different candidate and needs its own validation. The two submitted branches are based on upstream `35ba13ea81adada84ec9f98b2c63db1032e11b98`; their individual evidence is recorded in the source reassessment.

## Review queue

[Upstream PR 608](https://github.com/CarstVaartjes/vonk-forge/pull/608) is ready for architecture-policy review. [Upstream PR 609](https://github.com/CarstVaartjes/vonk-forge/pull/609) remains a dependent draft for retained temporary state. Both target upstream `main`; 609 includes the architecture commit because the upstream repository cannot use a fork-only branch as its base. After 608 lands, update 609 against current upstream, confirm that only its own correction remains, and validate the new exact signed head before marking it ready.

The cleanup closed superseded own-fork PRs **1–5, 11 and 13**, preserving all source branches. Own-fork PRs **6, 7, 8, 9, 10, 12 and 14** remain open drafts on their frozen review bases as the remaining review queue. Those frozen-base artifacts preserve earlier review context; they are not current-upstream submissions and their individual heads do not inherit the aggregate candidate's full CI result. Closing a redundant PR does not delete its patch or change either upstream PR.

All new commits must be signed and verified; use `git commit -S` and never fall back to an unsigned commit. Carry one new upstream candidate at a time. Reassess it against current upstream, reproduce the relevant defect, preserve current contracts, and resolve its dependencies before submission. Use focused checks during iteration and the required Linux workflow on the actual signed submission head. Record that head and workflow result; earlier or aggregate passes cannot substitute for them. Give independent review extra attention for concurrency, authority, lifecycle and cross-component changes. Keep effort proportional to the remaining uncertainty rather than repeatedly rerunning unchanged checks or developing held ideas without a concrete need.

A reviewed PR, passing checks, merge, published package, installed package, and successful physical workload are distinct states. Merge and deployment remain separate decisions. Do not resume physical evaluation, change the serving workload, or deploy an integration branch merely because source cleanup or CI succeeded.

## Worktrees and retained evidence

Use the canonical checkout for `main` and keep the four active worktrees under `~/.t3/worktrees/vonk-forge/`:

| Directory | Role |
| --- | --- |
| `upstream-compiled-image-policy` | Architecture submission. |
| `upstream-retained-inspection-tmp` | Dependent retained-state submission. |
| `integration-evaluation` | Exact tested aggregate. |
| `experiment-egress-lifecycle` | Held unfinished egress work; preserve its dirty files and index. |

The cleanup removed 22 clean historical checkouts and their expected environment/build caches after preserving all refs, a verified Git bundle, and a backup of dirty files and index state. The unfinished egress tree's working bytes, file modes, staged blobs, raw index and staged/unstaged patches matched the backup exactly after relocation. Removing a checkout is not deleting a branch or discarding its evidence. The detached published-platform fixture remains at its stable location under the home-lab private evaluation directory, alongside the compatible recipe fixture. Keep those fixtures distinct from the current source checkout.

These four trees were moved through Git and are agent-managed; placing them under T3's conventional directory does not register them to a T3 thread. Prefer T3-created worktrees for new T3 threads, or use `git worktree add` with a named owner for bounded delegated work. Avoid sibling worktrees alongside ordinary checkouts. Recreate relocated virtual environments before use because their scripts may retain old absolute paths. Remove a finished checkout after recording its branch/head and preserving any uncommitted state; branch deletion is a separate cleanup decision.

Use the existing Vonk project in T3 with the canonical checkout as its project directory. Start a new thread through the user interface for resumed work. The private fresh-agent handoff belongs at `vonk-forge/.private/vonk-work/fresh-agent-prompt.md`; earlier receipts remain under the home-lab `.private/vonk-evaluation/` directory. These private paths are handoff locations, not material to commit. Keep generated credentials, deployment receipts, backups, bundles and sensitive logs outside tracked documentation.

Before resuming a topic, read the private handoff and confirm the branch, exact head, worktree state, outstanding review and evidence boundary. Preserve the dated reports: correct stale statements about current organization with links or dated updates, rather than rewriting historical measurements as if they describe today's upstream or deployment.
