# Dependency updates

Renovate proposes updates according to [`.renovaterc.json5`](../../.renovaterc.json5), and the [dependency-triage skill](../../.agents/skills/dependency-triage/SKILL.md) covers landing them. This page covers holding a dependency back on purpose: which mechanism to use, how to write an exact-content lock, what a lock records, and how to read a failed lookup.

## Choose the narrowest mechanism

Hold back only what has a stated reason, and only as far as the reason reaches. Each mechanism is a rule in [`.renovaterc.json5`](../../.renovaterc.json5) scoped to named packages or, for a Dockerfile content lock, the form of the reference itself.

| Need | Mechanism | Example |
| --- | --- | --- |
| Some versions are known to be incompatible | `allowedVersions`, or `enabled: false` with `matchUpdateTypes` | Valkey below 8 for VSS; Kubernetes minors waiting for Talos |
| Updates are fine but need coordination or a gate | `dependencyDashboardApproval: true`, or a recorded hold on the PR | external-dns |
| Exact content was qualified and something depends on those bytes | An exact-content lock | NVIDIA PyTorch under the DeepSeek SparkRun mod |

Use a constraint when some updates remain acceptable, approval when updates need review, and a content lock when no automatic change is acceptable. Don't exclude a path on a guess about what is frozen there, disable a whole manager or datasource, or hold a whole package when one update type is the problem.

## Write references

- **Default:** `name:tag@sha256:digest`. The digest makes the pull reproducible and the tag tells Renovate what to follow. Renovate can update the digest while retaining the tag, or propose a newer tag with its matching digest. An exact version tag alone, such as `26.07-py3`, is not a content lock, because the registry can repoint it.
- **Dockerfile content lock:** `FROM name@sha256:digest` with no tag. A rule in `.renovaterc.json5` disables the Dockerfile manager's Docker dependencies that have a digest and no tag, so Renovate neither proposes updates for them nor reports a failed lookup. Without the rule it looks for `latest`, which images such as NVIDIA PyTorch don't publish. Tagged references in the same file stay managed. The rule reads the form, not the intent, so write a digest-only `FROM` only as a recorded lock.
- **Other managers:** the digest-only convention covers only the Dockerfile manager's Docker dependencies. Lock anything else, such as a Kubernetes image, a workflow image or an annotated custom-manager value, with an `enabled: false` rule scoped to the package, manager and files that need the lock. Record the reason in its comment. A package-wide rule is appropriate only when every use has the same restriction, as with Visionect.
- **Existing locks:** a tagged reference that is already intentionally locked keeps its form and gets a narrowly scoped rule. Don't rewrite frozen or checksummed files to adopt the digest-only form.
- **Not extracted:** the current managers don't extract the image behind a `FROM ${ARG}` without a default or the inputs in SparkRun's `upstream.lock.json`. Those inputs are invisible to Renovate rather than locked by a rule, and their record lives with them.

## CI workflow images

Eligible updates to the Renovate image in Repository Validation use PR auto-merge with tests required. This exception to branch auto-merge lets the workflow's internal-API regression harness check compatibility before a new Renovate image lands.

The workflow image custom manager extracts tagged, digest-pinned images written on their own line in multiline `docker run` commands under `.github/workflows/`. Folded YAML (`>-`) and literal shell continuations (`|` with `\`) are supported. Keep new inline images in that form; the native GitHub Actions manager owns `uses: docker://` references. The repository configuration job checks extraction and replacement with the actual Renovate version pinned in the workflow.

The Flate render action and diff command are one `Flate CI image` group across both managers. Both references must start at the same tag and digest, wait three days, and pass CI and review; this group overrides GitHub Actions auto-merge. A minimum group size of two withholds a new branch if either reference has no eligible update. The regression test also rejects manual drift and exercises digest, patch, minor and major replacements in one branch.

The inline references were identified from primary registry metadata on 2026-10-28 UTC, preserving all existing digests:

| Image | Verified origin | Update stream |
| --- | --- | --- |
| `ghcr.io/home-operations/flate` | `0.6.5` resolves directly to `sha256:e0e2d2de97539b3141977e8bb3bbb50ea5c5afe89477950e5fa972c9862478da`; its image config reports version `0.6.5` and source revision `631b76b69c4e58c6f4d1cb01e23616fa61aebafa` ([upstream package](https://github.com/home-operations/flate/pkgs/container/flate)). | Versioned releases and digest refreshes, with render and diff reviewed together. |
| `ghcr.io/yannh/kubeconform` | `v0.8.0-amd64` resolves directly to `sha256:5103f6f5e89061728aad4ad5a250627dd0fc9b2a92eb876f3762677a4222f9e0` ([upstream package](https://github.com/yannh/kubeconform/pkgs/container/kubeconform)). | Versioned releases retaining Docker versioning's `amd64` compatibility suffix, and digest refreshes. |
| `quay.io/frrouting/frr` | The `10.3.4` index, `sha256:c8790a83db3cb105c0823f76afe55de5c9f77d968fb1ebd28174a8ddd49b989c`, contains the pinned `linux/amd64` manifest `sha256:5a3a646db8bc911a996e48d67fc6dfed4c49f8d893c76cc6ef52a620a9ec6312` ([upstream tag metadata](https://quay.io/api/v1/repository/frrouting/frr/tag/?specificTag=10.3.4)). | Versioned stable releases and digest refreshes. The current pin remains the amd64 child manifest; a switch to an index would be a separate image update. |

These are managed CI tools, with no exact-content locks. Updating FRR's CI parser does not upgrade or qualify the UniFi gateway's FRR runtime. Verify origins with `crane digest IMAGE:TAG` and `crane manifest IMAGE:TAG`; for a platform pin, check its membership and platform in the release index instead of equating a child digest with the index digest.

## Record a lock

Write the record beside the reference, in the rule's comment, or in the README that owns the component, and link it from the others. It states:

- the input that matters, such as a base image or source commit, with its human-readable origin (a tag, branch or release) and the evidence that the origin resolved to this digest or commit, with the date checked;
- what depends on it, such as patches, a qualified build, checksums or a rollback profile;
- the issue or maintained document that owns the decision;
- the validation that permits a change;
- a concrete revisit trigger, such as the next refresh of the profile or an upstream fix landing.

Renovate does not report updates for dependencies disabled by these locks. State how upstream changes will be noticed while the lock holds, such as checking the upstream releases during each profile refresh. A revisit trigger is a manual responsibility unless a separate check actually exists; it is not an automated freshness or security alert.

## Read a failed lookup

When the Renovate Dashboard reports a failed lookup, such as `Could not determine new digest`, identify the lookup Renovate attempted and check the reference against the registry or source before changing it. Authentication, rate limits and transient registry failures need their own fixes; they do not establish that a dependency should be locked.

1. If the reference names the wrong registry or an absent tag, digest or commit, fix the reference after confirming access to the registry or source works.
2. If the reference resolves, its record requires exact content, and Renovate is trying to follow an undeclared update target, correct the narrowly scoped lock rule. A valid current digest does not prove that a failed lookup for a specified tag is harmless.
3. If a digest-only reference resolves but has no lock record, determine whether its exact content must be held. Record the decision to lock it, or add a verified matching tag to opt into update tracking.

Don't add a tag only to silence the warning. That turns a lock into a tracked dependency, and Renovate then proposes changes to content something depends on.

## Review locks

When a lock's trigger fires, or work touches the component that owns it, review the candidate release and affected patches, then run the validation the record names. Update the reference, coupled inputs, checksums and lock record together after that review, or record why the lock still holds and its next revisit trigger. This policy adds no automatic lock-review schedule.

## SparkRun mods

Each mod is a reviewed snapshot: an upstream change gets a new versioned mod and recipe, and the current one stays as the rollback ([Update a pinned mod](../../sparks/inference/sparkrun/README.md#update-a-pinned-mod)). The `mod_files` checksums in `upstream.lock.json` verify the listed files, including `build/Dockerfile` in mods that have one. They don't decide when to update, and are never rewritten to clear a failure. For a mod with a custom image build, the base takes a content lock, either a digest-only `FROM` or a build argument supplied from the lock, since a Renovate edit in place would fail `prepare.py --check`. The SparkRun README records the current base's rationale.

The lock manifest is itself a reviewed input, not covered by its own file hashes. Review its changes alongside the code and recipe. [CI runs the existing integrity and contract checks](../repository-validation.md#sparkrun-locks-and-contracts); semantic validation of all manifest fields remains separate work in [#1052](https://github.com/kelchm/home-lab/issues/1052).

A rebuild is a candidate artifact until its identity and profile qualification establish that it can be adopted. Retain the qualified image for rollback; source and base-image pins alone do not prove that a fresh build is equivalent.
