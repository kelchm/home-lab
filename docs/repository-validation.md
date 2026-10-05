# Repository Validation

The `Repository Validation` workflow uses an **affected-on-PR,
complete-on-main** model. Path filtering is a pull-request optimization; it is
not part of the correctness model for the deployed `main` branch.

## Event semantics

| Event | Validation scope | Concurrency behavior |
|---|---|---|
| Pull request | Repository configuration checks always run; other surfaces run when affected by the complete PR diff. A workflow or toolchain change deliberately selects every related surface. | A newer run for the same PR cancels the older run because it covers the complete, updated PR diff. |
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

## Repository configuration checks

The Repository Configuration Validation job runs on every pull request and push to `main`, independently of changed-file filtering. It uses actionlint and ShellCheck pinned in `.mise.toml` to check all GitHub workflows, then invokes `renovate-config-validator --no-global .renovaterc.json5` from a digest-pinned Renovate image to validate repository configuration. Either failure fails the job and the aggregate `Validation Success` check.

## Label checks

Label Catalog Validation runs the pinned label-sync action in read-only dry-run mode when label configuration or its workflows change on a PR, and on every push to `main`. It parses the catalog and prints planned label changes in the job log. Errors reported by the action fail `Validation Success`; planned deletions or merges are visible but do not themselves fail the dry run. The separate Label Sync workflow applies catalog changes after merge.

Labels are coarse filters. Existing labels and mappings stay in place; the added AI, identity, observability, storage, and UniFi areas follow broad directory paths. Additional scope labels are optional when useful. Do not add individual-file exceptions to classify everything a change touches.

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

Keep these properties when extending `.github/workflows/repository-validation.yaml`:

1. Pull requests may use changed-file filtering; pushes to `main` must select
   every validation surface.
2. Every conditional validation job must finish successfully in both its real
   and no-op paths so the aggregate job has a stable dependency set.
3. The aggregate job must run with `always()` and require every dependency to
   equal `success`. Failure, cancellation, and unexpected skipping must fail the
   aggregate result.
4. PR jobs must never receive the SOPS age private key. Encryption structure is
   checked on PRs; YAML decryption is required only on trusted `main` pushes.
5. Flux diffs are review artifacts and therefore remain PR-only. Flux build,
   Kubernetes schema, and policy validation run for both event types.
6. Every new validation surface must be added to the aggregate job's `needs` list. If it uses PR filtering, add scope outputs and a filter that includes this workflow and its toolchain inputs. The repository configuration job always runs and needs no scope output or filter.

This model follows the same broad pattern used by large monorepos: optimize PR
feedback using affected paths, then validate the complete trunk state. For a
public example, PostHog's backend workflow skips its path filter on pushes with
the explicit policy “Run all tests on master push.”

## SOPS files

The SOPS job covers every tracked `*.sops.yaml`, `*.sops.yml`, and `*.sops.env` file. PR path filtering includes all three formats, the validator and its regression tests, `.sops.yaml`, and toolchain/workflow changes. `scripts/ci/test-validate-sops.sh` checks the current tree and tests plaintext, partially encrypted, and wrong-recipient dotenv files using freshly generated throwaway age recipients; no private keys are supplied to SOPS.

All formats must pass `sops filestatus`, which checks SOPS metadata presence rather than authenticating ciphertext. Dotenv files additionally require every non-metadata assignment to have the SOPS AES256-GCM string ciphertext structure, an encrypted MAC, age encrypted-key envelopes, and exactly the recipients in the first matching `.sops.yaml` creation rule. Duplicate assignments and unknown metadata fields are rejected. Dotenv rules currently support age-only, full-value encryption; a rule introducing other options fails explicitly until the validator supports them. Comments and SOPS metadata are exempt from value encryption, and dotenv has no nested YAML `data`/`stringData` selection.

Keyless checks cannot authenticate ciphertext, verify the MAC, or prove that an age envelope can actually be opened by its declared recipient. YAML files retain the existing metadata-only PR check and required decryption on trusted `main` pushes. Dotenv files receive structure and recipient checks on both events and are never decrypted by this validator: the Synology file uses a separate NAS recipient, whose private key is not provided to GitHub Actions. `--require-decrypt` requires decryption of YAML files only. Run the keyless checks locally with `mise exec -- scripts/ci/test-validate-sops.sh`.

## Talos machine configs

The Talos job runs `scripts/ci/test-validate-talos.sh`, which validates the current tree and exercises rejection cases with the tools pinned in `.mise.toml`. `scripts/ci/validate-talos.sh` checks the talhelper input, generates fresh fixture secrets with `talhelper gensecret`, and renders `talconfig.yaml` plus its patches with `talhelper genconfig --offline-mode`. Each rendered node config is validated with `talosctl validate --mode metal`. The installer image tag must match `talosVersion` in `talos/talenv.yaml`; the kubelet and control-plane component image tags must match `kubernetesVersion`.

Both PR and main runs use fixture secrets. Explicit `--env-file` and `--secret-file` arguments bypass talhelper's default SOPS inputs; no SOPS key is supplied. Fixture secrets, generated machine configs, and the client config live in a private temporary directory that is removed on exit. The job does not upload rendered configs or contact nodes or the image factory. Run the same validation locally with `mise exec -- scripts/ci/test-validate-talos.sh`.

This checks renderability, machine-config validity, and version consistency. It does not assert that `.nodes[].networkInterfaces` is absent: talhelper's modern network documents coexist with legacy `machine.network.interfaces` patch entries, so the patches do not atomically replace those talconfig entries. The remaining primary-NIC duplication cleanup is tracked in [#662](https://github.com/kelchm/home-lab/issues/662).

## References

- [GitHub Actions concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency)
- [Flux webhook receivers](https://fluxcd.io/flux/components/notification/receivers/)
- [PostHog affected-on-PR, complete-on-master workflow](https://github.com/PostHog/posthog/blob/master/.github/workflows/ci-backend.yml)
