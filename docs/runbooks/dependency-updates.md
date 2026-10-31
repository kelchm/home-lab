# Dependency updates

Renovate proposes updates according to [`.renovaterc.json5`](../../.renovaterc.json5), and the [dependency-triage skill](../../.agents/skills/dependency-triage/SKILL.md) covers landing them. This page covers holding a dependency back on purpose: which mechanism to use, how to write an exact-content lock, what a lock records, and how to read a failed lookup. It also covers the [Renovate problems](#renovate-problems) alert.

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
- **OCI Helm charts:** `spec.ref.tag` plus `spec.ref.digest` on the `OCIRepository`. See [OCI chart digests](#oci-chart-digests).
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

## CNI and device-plugin releases

The three remote-manifest apps use upstream runtime release commits and tagged image digests. The `cni-source` comment records the tag that resolves to the following URL's commit; it is an input to the narrow Renovate manager, not an exact-content lock. The source and image releases must match, with Multus retaining its `-thick` variant. Use `newTag: tag@sha256:digest`: Kustomize ignores `newTag` when a separate `digest` field is present, which would lose the release label in the rendered image.

Verified against upstream tags, registry indexes and read-only cluster metadata on 2026-10-30 UTC:

| App | Runtime release → source commit | Image index digest |
| --- | --- | --- |
| [generic-device-plugin](../../kubernetes/apps/kube-system/generic-device-plugin/app/kustomization.yaml) | [`0.2.0` → `a3d6f47`](https://github.com/squat/generic-device-plugin/tree/a3d6f47ddde5cbb24cc274f4ffd2f2cdc90f7b92) | `sha256:66c8d5c270eb2b721f1064c549b9b7898152a6d2f0163380a5d37dc7636c20ff` |
| [Multus](../../kubernetes/apps/kube-system/multus/app/kustomization.yaml) | [`v4.3.1` → `d6170681`](https://github.com/k8snetworkplumbingwg/multus-cni/tree/d61706810cc8be60381dcb66e6a2428d5eb2795a) | `sha256:a357a79359b80ddd5f4699117946cacf3b54f5baecf7944535594863e875fc25` |
| [Whereabouts](../../kubernetes/apps/kube-system/whereabouts/app/kustomization.yaml) | [`v0.9.4` → `3a262bd0`](https://github.com/k8snetworkplumbingwg/whereabouts/tree/3a262bd0cd873e13d9b379342f8251b852a89b8f) | `sha256:f279fd7dc1124d7819330fb2e07357990b2b4c5d8733aef1b028573c29f05e14` |

Each index digest matched the running Pods' reported image IDs and contains a linux/amd64 manifest. All three DaemonSets were 3/3 Ready. The proposed references preserve these runtime releases and bytes; their image strings still change, causing Pod replacement. The generic-device-plugin source previously used a **chart** tag (`generic-device-plugin-0.1.2`); use the runtime `0.2.0` tag, not `generic-device-plugin-0.2.0`, which is also a chart release.

Renovate groups release changes per app across the native Kustomize image manager and the custom GitHub-tags manager. The minimum group includes every URL plus the image (two references for Multus/device-plugin, five for Whereabouts). Missing eligible updates withhold new branches; dashboard overrides and already-existing branches still require review and CI. Same-release digest refreshes can be reviewed separately. None of these updates auto-merge. `scripts/ci/test-renovate-cni.mjs` checks the real files for release agreement, full extraction, immutable pins and one source commit per app, then exercises grouped release replacements and independent digest refreshes with the workflow's pinned Renovate version. It rejects mismatched versions even if both upstream streams offer different latest releases. It does not prove a tag's commit provenance or that a future release retains the same artifact paths: check those upstream and review the render diff before landing an update.

### Effective alignment changes

- **generic-device-plugin:** changing to the runtime release manifest only adds its existing `0.2.0` tag upstream. After the local DRI-only patch, the sole render change is the image digest. Device arguments, resource limits and privilege are unchanged.
- **Multus:** the [release manifest](https://github.com/k8snetworkplumbingwg/multus-cni/blob/d61706810cc8be60381dcb66e6a2428d5eb2795a/deployments/multus-daemonset-thick.yml) adds the `nad` CRD short name, adds `get/list` on `resource.k8s.io` resourceclaims, resourceclaims/status and resourceslices to ClusterRole `multus`, and changes DaemonSet `kube-multus-ds` termination grace from 10 to 30 seconds. Both container and installer gain the same image digest. The fail-closed ConfigMap, `/var/run/netns/`, 192Mi/384Mi memory settings, tuning installer and taint reconciler remain unchanged.
- **Whereabouts:** CRDs, RBAC, ConfigMap, DaemonSet arguments/resources/privilege and host CNI paths are unchanged. Remove the obsolete cron mount; v0.9.4 [moved scheduled cleanup to a separate Deployment](https://github.com/k8snetworkplumbingwg/whereabouts/pull/700), which remains deliberately omitted. The v0.9.3 DaemonSet ran that cleanup until the image-only update in [#103](https://github.com/kelchm/home-lab/pull/103) on 2026-06-19; earlier run outcomes are unavailable. [#1134](https://github.com/kelchm/home-lab/issues/1134) tracks whether restoring it is warranted. This alignment preserves the current absence of scheduled cleanup.

### Rollout, acceptance and rollback

After rollout approval, record DaemonSet readiness/image IDs, node `devic.es/dri` capacity, Longhorn health and Whereabouts allocations. Merging lets Flux roll all three DaemonSets and update CRD/RBAC resources. Their installers rewrite the same host CNI binaries. Multus remains fail-closed, so its replacement can briefly delay new Pod sandboxes; existing workload Pods are not deliberately restarted. No node reboot or drain is required.

Verify all three Flux Kustomizations and DaemonSets are updated and Ready, CRDs remain Established with the same stored versions, and no persistent sandbox, authorization or allocation errors appear. Confirm `devic.es/dri` remains one per node, Longhorn volumes/instance-managers stay healthy, and existing secondary-network annotations and allocations are intact. The [Multus recovery checks](multus-conf-absent-recovery.md) cover annotation failures. Record live acceptance in #221.

If acceptance fails, revert through Git and verify Flux restores the previous templates and healthy DaemonSets. This restores the old version skew and tag-only references, so recheck registry identities. A revert does not undo host installer writes; the reviewed images contain the same binaries. Do not delete CRDs or reset IP pools as rollback.

The remaining #221 slice here is workload image digest coverage (inventory first, preserving deliberate locks and database rollout boundaries). This source alignment does not change that policy. [OCI chart digests](#oci-chart-digests) covers chart content.

## OCI chart digests

Chart versions and chart content change through Renovate PRs, and the exact bytes in use stay in place until a PR is merged. Each `OCIRepository` sets `spec.ref.tag` and `spec.ref.digest`. Flux pulls the digest and never reads the tag, so a publisher replacing a version's content cannot change what the cluster runs.

| Upstream change | What Renovate proposes |
| --- | --- |
| New version | One PR replacing `tag` and `digest` together |
| Same version re-pushed | One PR replacing only `digest`, so review sees a content change with no version change |
| New source committed with only a tag | A pin PR adding `digest` |

None of these merge automatically, and Renovate runs on weekends, so a re-push can wait up to a week for its PR. Existing holds still apply: external-dns stays behind dashboard approval, including its digest updates. `scripts/ci/test-renovate-oci-charts.mjs` checks presence, extraction and replacement of both fields, not their correspondence in the registry. Changing only `tag` while retaining an old `digest` passes CI and continues deploying the old chart. Resolve the matching digest for manual version edits, and let Renovate rebuild held tag-only PRs before reviewing and landing them.

helm-controller appends the first 12 digest characters to the chart version, so a release reports `5.3.0+0e13b1456564` in `status.lastAttemptedRevision` and `helm history`, and `helm.sh/chart` labels show it with `_`. The 13 releases from a `HelmRepository` keep plain versions and follow versions only. `bootstrap/helmfile.d` also stays tag-only: it runs at cluster bootstrap and Flux takes over from the pinned sources.

Merging a digest change upgrades the release even when the chart files match, and it restarts any workload that carries the version in its pod template, directly or through a hash of a versioned object. CoreDNS is the second case: `checksum/config` hashes its ConfigMap, whose `helm.sh/chart` label includes the digest. A chart that writes `.Chart.Version` to a label without replacing `+` fails its upgrade with an invalid label. All current OCI charts render valid labels, but the `flux-instance` healthcheck Job has this flaw, so keep `healthcheck.enabled` off. Check any new chart's use of `.Chart.Version` when adopting it.

### First activation

Activated on 2026-10-31 through #1192 (`b136c80b`). All 42 source pins were verified before releases resumed; 41 releases converted successfully in 42 upgrades, including the initial `flux-instance` configuration upgrade. No upgrade failed or required rollback. `identity/kaniop` remains deliberately suspended at 0.19.1: its source is pinned, but its conversion and CRD apply/migrate/verify hooks wait for the separate identity maintenance in #1180/#1160. #1195 (`e888fba2`) then landed the first reviewed digest-only change, CoreDNS's re-push of 1.49.0 with identical chart files: Helm moved to v15, both Pods were replaced and 705 of 705 lookups succeeded through the rollout. #1172 (`68891b9b`) landed the first tag-and-digest pair. #221 tracks Kaniop's remaining conversion.

Establish all source pins before enabling digest tracking: merging both changes without pausing releases leaves a race in which helm-controller can consume a newer, still-unpinned source. The initial `flux-instance` configuration upgrade removes `DisableChartDigestTracking` and restarts helm-controller. Its restart then causes 42 digest-conversion upgrades, including a second upgrade of `flux-instance`: normally 43 successful Helm upgrades in total, with remediation potentially adding attempts. If Kaniop stays held for a separate identity window, budget 42 successful upgrades now and defer its conversion; all 42 source pins must still be verified. Up to ten reconciliations can run concurrently; this is not an ordered batch rollout. The checked renders change no images, values, chart defaults, selectors or volume claim templates; refresh the inventory and pins if any chart has moved before activation.

| Effect on upgrade | Releases |
| --- | --- |
| Labels only, no restart | The other 32, including all `app-template` releases; `flux-instance`'s separate configuration upgrade restarts helm-controller |
| Chart version in the pod template | `cilium` (agents and operator), `cert-manager` (three Deployments), `flux-operator`, `flux-operator-mcp`, `k8s-gateway`, `reloader`, `seerr` |
| Checksum over rendered labels | `coredns`, `cnpg-operator`; database pods stay untouched |
| Upgrade hooks | `kaniop` runs its CRD apply, migrate and verify Jobs; its Deployments stay untouched |

The activation replaced 16 Pods across 12 workloads plus helm-controller, matching the render prediction. Database Pods, Visionect, selectors and PVCs were unchanged. Seerr was unavailable for about 70 seconds; DNS checks passed during the CoreDNS and Cilium rolls, and BGP, storage and database health passed afterward. Cilium's singleton operator permits full unavailability while it is replaced.

When enabling tracking on an unconverted cluster, use native Flux suspensions to establish the pins, then keep the network plane held until the rest has proved the change:

1. Finish in-flight dependency upgrades, refresh the chart and workload inventory, confirm HelmReleases, nodes, Longhorn volumes and CNPG clusters are healthy, and hold Renovate merges for the window. Include Kaniop's hooks in the approved scope after checking identity health and its completed migration marker; otherwise keep Kaniop held.
2. Record existing suspensions, then use `flux suspend hr <name> -n <namespace>` for each of the 42 OCI-sourced releases, including `flux-instance`. Before merging, confirm all suspensions are observed and remain set after Git reconciliation. Their Kustomization health checks pass once the healthy suspended releases have been observed; pausing reconciliation does not stop their running workloads.
3. Merge while all 42 releases remain suspended. Before resuming any release, require every `OCIRepository` to have its committed `spec.ref.digest`, be Ready at its current observed generation, and report that same OCI digest in `status.artifact.revision`. A revision can include a version prefix, such as `1.49.0@sha256:…`; compare its OCI digest component, not the packaged archive's separate `status.artifact.digest`. In particular, the CoreDNS source must return to its pinned deployed archive. Keep helm-controller's existing gate enabled until this check passes.
4. Resume `flux-instance` first and confirm its configuration upgrade restarts helm-controller without the gate, followed by its digest-conversion upgrade. Resume the other approved OCI releases in deliberate groups, leaving CoreDNS and Cilium last and preserving any pre-existing holds. For each resumed release, require `Ready=True`, current `status.observedGeneration`, no `Stalled=True`, and a successful deployed Helm history entry for the expected digest-suffixed chart version, as well as Ready workloads. `lastAttemptedRevision` alone proves only an attempt; existing workloads can remain healthy after failed hooks.
5. Run `flux resume hr coredns -n kube-system`, then check both replicas and cluster DNS. Run `flux resume hr cilium -n kube-system --timeout 10m` and verify agents one node at a time, connectivity, BGP and Longhorn attachments using [AGENTS.md](../../AGENTS.md#talos-operations). The 120-second BGP graceful-restart setting bounds route retention; it does not guarantee connectivity. Apply the same HelmRelease success checks after each resume and release only this window's owned holds, with Kaniop remaining within its approved scope.

### Recovery

A failed upgrade attempts rollback through the release's remediation, currently with two retries. Rollback can fail and cannot undo arbitrary hook effects, CRD changes or forward-only migrations. Check the actual release, hooks and workloads before choosing recovery.

After fixing the cause, `flux reconcile hr <name> -n <namespace> --force` permits a one-off upgrade despite exhausted retries; it does not reset the counters. Add `--reset` to clear them. Suspend/resume is a fallback when a release remains stalled: its generation change also resets failures.

Restoring the gate is not a per-release fix. Releases already converted to digest-suffixed versions can upgrade and restart again; a partial activation need not repeat all 42 upgrades. Use it only if digest tracking itself proves unworkable, and resume any suspended release afterwards.

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

## Renovate problems

The [Renovate Problems workflow](../../.github/workflows/renovate-problems.yaml) reads the [Renovate Dashboard](https://github.com/kelchm/home-lab/issues/2) daily at 13:17 UTC with the workflow's `GITHUB_TOKEN`. A failed run is the alert: GitHub sends its workflow-failure notification to whoever last changed the cron line, provided that account has Actions notifications enabled. A persisting failure notifies on every run.

The result says whether the current dashboard lists problems, not that Renovate is running. A passing run on a stalled Renovate reads the last dashboard it wrote. Missed-run detection needs a trustworthy hosted-run signal and remains open in [#1017](https://github.com/kelchm/home-lab/issues/1017).

| Annotation | Meaning | Response |
| --- | --- | --- |
| `Renovate repository problem` | The dashboard has a Repository Problems section. | Follow its **View logs** link, and use [Read a failed lookup](#read-a-failed-lookup) for lookup failures. A registry or token error can clear on Renovate's next dashboard update, so rerun the workflow before changing anything. |
| `Renovate dashboard unreadable` | The GitHub API read failed, or issue #2 is closed, missing or no longer looks like a dashboard. | No problem result exists for this run. Rerun after an API, rate-limit or authentication failure, and restore the dashboard if it was closed or replaced. |

The check samples once a day, so it can miss a problem that clears first. GitHub can delay or drop scheduled runs, and nothing alerts if this workflow stops running.

Acceptance after merge: confirm the first scheduled run reads issue #2 and passes or reports a real problem. To test delivery, run `gh workflow run renovate-problems.yaml -f drill=true`. The run takes the same problem path on a bundled real dashboard body, so it needs no edit to issue #2, and it notifies the account that dispatched it.

## Review locks

When a lock's trigger fires, or work touches the component that owns it, review the candidate release and affected patches, then run the validation the record names. Update the reference, coupled inputs, checksums and lock record together after that review, or record why the lock still holds and its next revisit trigger. This policy adds no automatic lock-review schedule.

## SparkRun mods

Each mod is a reviewed snapshot: an upstream change gets a new versioned mod and recipe, and the current one stays as the rollback ([Update a pinned mod](../../sparks/inference/sparkrun/README.md#update-a-pinned-mod)). The `mod_files` checksums in `upstream.lock.json` verify the listed files, including `build/Dockerfile` in mods that have one. They don't decide when to update, and are never rewritten to clear a failure. For a mod with a custom image build, the base takes a content lock, either a digest-only `FROM` or a build argument supplied from the lock, since a Renovate edit in place would fail `prepare.py --check`. The SparkRun README records the current base's rationale.

The lock manifest is itself a reviewed input, not covered by its own file hashes. Review its changes alongside the code and recipe. [CI runs the existing integrity and contract checks](../repository-validation.md#sparkrun-locks-and-contracts); semantic validation of all manifest fields remains separate work in [#1052](https://github.com/kelchm/home-lab/issues/1052).

A rebuild is a candidate artifact until its identity and profile qualification establish that it can be adopted. Retain the qualified image for rollback; source and base-image pins alone do not prove that a fresh build is equivalent.
