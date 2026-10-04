# Terminated pod garbage collection

## Policy and measured state

**Prepared policy, not applied.** Set `cluster.controllerManager.extraArgs.terminated-pod-gc-threshold` to **500** through [terminated-pod-gc.yaml](../../talos/patches/controller/terminated-pod-gc.yaml), referenced under `controlPlane.patches` in [talconfig.yaml](../../talos/talconfig.yaml). This merges with the existing `bind-address` extra argument in `controller/cluster.yaml`; no kubelet or volume patch changes. The task guards in [#663](https://github.com/kelchm/home-lab/issues/663) and [#293](https://github.com/kelchm/home-lab/issues/293) landed through [#754](https://github.com/kelchm/home-lab/pull/754); this branch includes them. Merge and live application remain separate owner decisions. Coordinate application with the pending Talos v1.13.11 node cycle and retain the image-GC configuration from [#745](https://github.com/kelchm/home-lab/pull/745). The [ephemeral-storage runbook](talos-ephemeral-storage.md) now records that image GC 75/70 was separately applied without reboot; it need not be applied again solely for this change.

Measured 2026-10-04 around 22:20–22:23 UTC using read-only Kubernetes and Talos queries: all three controller-manager static pods use `registry.k8s.io/kube-controller-manager:v1.36.4`, with no threshold flag or `--config` argument, and `--controllers=*,tokencleaner`. Thus the effective threshold is the [v1.36.4 default of 12,500](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/controller/podgc/config/v1alpha1/defaults.go). Talos's controllerManagerConfig on node 1 independently shows only `bind-address` under extraArgs. Nodes run Talos 1.13.10; the repository requests 1.13.11 and Kubernetes 1.36.4.

The live pod list contained **145 pods: 139 Running, 6 Succeeded, 0 Failed**. Completed history was ai 1 (functional probe), longhorn-system 2 (daily/weekly backup), and media 3 (recyclarr); the oldest was October 2. A retained VictoriaMetrics query `max_over_time((sum(kube_pod_status_phase{phase=~"Failed|Succeeded"}))[30d:5m])` returned **11**. This is a sampled 30-day maximum, not a guarantee about short bursts or missing scrape intervals. These normal-operation measurements do not establish the history needed during an incident or recovery.

[PR #303](https://github.com/kelchm/home-lab/pull/303) introduced the [50-object quota component](../../kubernetes/components/pod-quota/resourcequota.yaml), with [kube-system 100](../../kubernetes/apps/kube-system/kustomization.yaml) and [longhorn-system 75](../../kubernetes/apps/longhorn-system/kustomization.yaml) overrides. Live `pod-count` quotas agree:

| Namespace | count/pods used / hard | Nonterminated pods | Current free object slots |
|---|---:|---:|---:|
| ai | 11 / 50 | 10 | 39 |
| cert-manager | 3 / 50 | 3 | 47 |
| cnpg-system | 1 / 50 | 1 | 49 |
| default | 1 / 50 | 1 | 49 |
| flux-system | 5 / 50 | 5 | 45 |
| homepage | 1 / 50 | 1 | 49 |
| identity | 5 / 50 | 5 | 45 |
| iot | 3 / 50 | 3 | 47 |
| kube-system | 36 / 100 | 36 | 64 |
| lemon-manuals | 1 / 50 | 1 | 49 |
| longhorn-system | 32 / 75 | 30 | 43 |
| media | 14 / 50 | 11 | 36 |
| network | 11 / 50 | 11 | 39 |
| observability | 19 / 50 | 19 | 31 |
| printing | 2 / 50 | 2 | 48 |

`count/pods` counts all pod objects, including terminated ones. Flux's separate scoped `pods: 1k` quota is not the object-count guard. System namespaces cilium-secrets, kube-node-lease and kube-public have no live `count/pods` quota. GC is cluster-wide; those namespaces still contribute to its threshold.

Choose **500 cluster-wide**, a 96% reduction from 12,500 that preserves substantially more incident and recovery history than the normal baseline. This is a conservative retention policy, not a measured performance optimum: no observed API/etcd problem at 500 objects has been established, and normal-operation counts do not establish the incident-retention requirement. Namespace quotas remain the primary containment mechanism. A single ordinary namespace can fill its 50-object quota before global GC starts; stop the faulty controller, preserve diagnostics, and delete selected failed objects when recovery needs headroom. Do not lower global retention just to make GC free an individual namespace's quota.

At the measured baseline, kube-system can fit a complete 36-pod replacement set (36 + 36 = 72 < 100), and Longhorn can fit a complete 30-pod replacement set with its two normal completed backup pods (30 + 30 + 2 = 62 < 75). These margins depend on actual retained objects and concurrent surges. The global 500 threshold neither reserves namespace headroom nor guarantees any namespace can retain 500 pods. Check actual quota usage before recovery and prune selected history only after preserving evidence. The [v1.36.4 controller](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/controller/podgc/gc_controller.go) checks every 20 seconds and collects excess terminated pods, prioritizing Evicted pods, then creation time. Pending/Running/Unknown pods are outside this threshold. Finalizers, errors and bursts can delay convergence. Churn in one namespace can erase another's history; there is no per-namespace or time retention guarantee. ReplicaSet rollout revisions are separate objects and are not collected by this threshold. Preserve important diagnostics in logs before relying on pod history.

## Incident evidence and limitations

The May 12 [remediation PR #55](https://github.com/kelchm/home-lab/pull/55), preserved in commit `e76e790b088e7d45c45fe1d96fc0c65873763c66`, independently records qBittorrent in a `SysctlForbidden` admission loop and suspension to stop a **1000+-pod** loop. The original [#54](https://github.com/kelchm/home-lab/pull/54) manifest requested `net.ipv4.conf.all.src_valid_mark`; #55 added that unsafe sysctl to the kubelet allowlist, later removed after refactoring. The exact peak, node distribution and roughly 20-minute duration reported in #56 cannot be remeasured: live metrics retention is 30d, logs are configured for 30d, and a May 12 failed-pod metrics query returned no data. Do not interpret absent historical series as zero failures.

Read-only etcd status on all three members showed a common leader, equal raft/applied indexes, no reported errors, **127/127/130 MB DB size** and approximately **81 MB in use each**. No causal etcd size benefit can be measured before rollout; deletion frees logical records, not necessarily physical DB allocation.

## Rollout and verification

**Future procedure only; no node application, staging, upgrade or reboot was performed during preparation.** The threshold alone needs no node reboot: Talos [allows immediate `.cluster` updates](https://docs.siderolabs.com/talos/v1.13/configure-your-talos-cluster/system-configuration/editing-machine-configuration), and its [v1.13.10 static-pod controller](https://github.com/siderolabs/talos/blob/v1.13.10/internal/app/machined/pkg/controllers/k8s/control_plane_static_pod.go) merges controller-manager extraArgs into the pod command and updates the static pod. An immediate application recreates the controller-manager container; changing a leader can cause an election. The combined approved Talos upgrade still uses its planned power cycle. Upgrading the installer alone does not apply new machine-config arguments.

1. Use a checkout containing #754, review the combined diff and obtain approval for the guarded roll. Follow the [Talos rollout skill](../../.agents/skills/talos-rollout/SKILL.md): preflight, snapshot, one node at a time, etcd member health/quorum, all nodes Ready, all Longhorn volumes healthy and all instance-managers Ready with `lhnet1` before proceeding.
2. Generate fresh config through the new guarded workflow. For local preparation use `mise exec -- talhelper genconfig --offline-mode --out-dir <private-temporary-directory>` from `talos/`; never overwrite credential symlinks in a worktree. Generated configs contain secrets. Validate each rendered control-plane config has the string `"500"` at `cluster.controllerManager.extraArgs.terminated-pod-gc-threshold`, retains `bind-address`, and matches desired component versions. Inspect the per-node dry-run diff privately; include only approved pending changes. Coordinate staged config versus immediate config with the guarded workflow: staging delays this flag until the power cycle; immediate application restarts the static pod before it.
3. Apply the approved config and v1.13.11 upgrade using the guarded tasks, including `task talos:upgrade-node IP=<node-ip>`; never use an unguarded fleet command. After each node, inspect its controller-manager command, image, Ready status and restart count. After all three, verify exactly one healthy holder of `kubectl -n kube-system get lease kube-controller-manager -o yaml`, normal reconciliation, and no repeated election/restart errors.

Read-only comparisons before and after:

```bash
mise exec -- kubectl -n kube-system get pods -l component=kube-controller-manager -o json \
  | jq '.items[] | {name:.metadata.name,containers:[.spec.containers[] | {image,command,args}],status:.status.containerStatuses}'
mise exec -- kubectl get pods -A -o json \
  | jq '[.items[] | select(.status.phase == "Failed" or .status.phase == "Succeeded")] | {count:length,pods:map({namespace:.metadata.namespace,name:.metadata.name,phase:.status.phase,created:.metadata.creationTimestamp})}'
mise exec -- kubectl get resourcequota -A
mise exec -- talosctl -n 10.32.30.11,10.32.30.12,10.32.30.13 etcd status
```

For the acceptance test, obtain separate approval for workload writes and the larger temporary object budget needed to exercise 500. First save names/UIDs and logs of existing terminated pods and record deployment/ReplicaSet readiness and revision history. Use one disposable namespace with `count/pods: 520`; leave all managed namespace quotas unchanged. Create standalone pods in batches of at most five active pods, with `restartPolicy: Never`, a cached approved small image, tiny CPU/memory requests, no volumes or privileges, and commands that exit successfully or fail once. Stop creating as soon as the global terminated count exceeds 500, with a hard ceiling of **501 test pods**. At the original six-pod baseline, roughly 495 completions cross the threshold; the ceiling accounts for independent normal history cleanup. Abort if the threshold is not crossed at the ceiling, production readiness degrades, quota trouble appears, or controller errors grow. Do not create a ReplicaSet admission loop, lower the live threshold for testing, or bypass existing quotas. Keep test objects free of finalizers and omit TTL/Job controllers so deletion is attributable to PodGC. The intentional footprint is bounded at 501 pod objects plus one namespace and quota; this test is not authorized by approval of the Talos rollout alone.

Confirm test pods reach both Succeeded and Failed, global terminated count exceeds 500, then converges to at most 500 within two minutes after final completions (several 20-second scans). Record surviving/deleted UIDs and GC logs/metrics to distinguish collection from another actor. Existing older completed pods may be collected: this is an intended retention consequence, requiring the diagnostics backup above. Confirm newest useful history remains and ordinary workload readiness and ReplicaSet revision history are unaffected. Run an approved ordinary stateless rollout and confirm readiness/revision history; verify actual managed-namespace recovery headroom without deliberately disrupting storage. Remove the disposable namespace. Compare etcd health, errors, DB size/in-use bytes and request latency before/after, allowing temporary write load and no immediate physical shrink. Observe for at least 15 minutes. If the temporary object budget is not approved, defer collection acceptance until a naturally occurring threshold crossing; do not claim a small below-threshold smoke test demonstrates GC. Record evidence in #56 and leave it open until these checks pass.

## Rollback

If controller-manager health or collection behavior is unacceptable, stop the roll. For an already changed node, an approved targeted patch setting the threshold to `"12500"` restores the prior effective policy with only a static-pod restart. Do not use a JSON6902 patch against these multi-document machine configs. Revert the dedicated source patch and talconfig reference, regenerate fresh config, and follow guarded per-node application. A Git revert alone does not change live Talos configuration. Keep rollout/rollback approval separate from merge. Deleted pod objects and their local API history cannot be recovered by increasing the threshold; retain diagnostics beforehand.
