# Repository Validation

The `Repository Validation` workflow uses an **affected-on-PR,
complete-on-main** model. Path filtering is a pull-request optimization; it is
not part of the correctness model for the deployed `main` branch.

## Event semantics

| Event | Validation scope | Concurrency behavior |
|---|---|---|
| Pull request | Only surfaces affected by the complete PR diff. A workflow or toolchain change deliberately selects every related surface. | A newer run for the same PR cancels the older run because it covers the complete, updated PR diff. |
| Push to `main` | Every validation surface runs against the complete repository state. Flux diffs remain PR-only because there is no review comment to produce after merge. | A newer `main` run cancels the older run because the newer commit contains the resulting repository state and validates it in full. |

This is deliberately different from validating only the files changed by each
individual push. GitHub keeps at most one pending run in a concurrency group by
default; a newer pending run can replace an older one. If main-push validation
were delta-based, the replaced run's surface could go unvalidated. Full-state
validation makes replacement safe: the newest run revalidates all inherited
changes without cross-run dependencies or ordering requirements.

The workflow encodes this by running `changed-files` only for pull requests.
On `main`, the absent changed-file outputs default to `true`, selecting every
surface. This fail-open-for-coverage default is intentional and must be
preserved when outputs are added or refactored.

## Relationship to Flux

Flux remains pull-based, but its GitHub receiver triggers a pull and immediate
reconciliation after a push to `main`; periodic polling is the fallback. GitHub
Actions receives the same push independently. Workflow concurrency therefore
does not order, delay, or gate Flux reconciliation.

Most non-trivial changes go through pull requests and are validated before
merge. The full `main` run is defense in depth for merge-state interactions and
the small direct-to-main changes allowed by [AGENTS.md](../AGENTS.md). It is a
post-push detector, not a deployment gate: Flux may reconcile before validation
finishes. A separate validated promotion ref would be required if prevention
ever becomes the goal.

## Workflow invariants

Keep these properties when extending `.github/workflows/flux-local.yaml`:

1. Pull requests may use changed-file filtering; pushes to `main` must select
   every validation surface.
2. Every conditional validation job must finish successfully in both its real
   and no-op paths so the aggregate job has a stable dependency set.
3. The aggregate job must run with `always()` and require every dependency to
   equal `success`. Failure, cancellation, and unexpected skipping must fail the
   aggregate result.
4. PR jobs must never receive the SOPS age private key. Encryption structure is
   checked on PRs; decryption is required only on trusted `main` pushes.
5. Flux diffs are review artifacts and therefore remain PR-only. Flux build,
   Kubernetes schema, and policy validation run for both event types.
6. A new validation surface must be added to the scope outputs, given a PR
   filter that includes this workflow and its toolchain inputs, and added to the
   aggregate job's `needs` list.

This model follows the same broad pattern used by large monorepos: optimize PR
feedback using affected paths, then validate the complete trunk state. For a
public example, PostHog's backend workflow skips its path filter on pushes with
the explicit policy “Run all tests on master push.”

## References

- [GitHub Actions concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency)
- [Flux webhook receivers](https://fluxcd.io/flux/components/notification/receivers/)
- [PostHog affected-on-PR, complete-on-master workflow](https://github.com/PostHog/posthog/blob/master/.github/workflows/ci-backend.yml)
