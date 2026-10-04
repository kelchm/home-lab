# Terminated pod garbage collection

## Policy and measured state

**Prepared policy, not applied.** Set `cluster.controllerManager.extraArgs.terminated-pod-gc-threshold` to **25** through [terminated-pod-gc.yaml](../../talos/patches/controller/terminated-pod-gc.yaml), referenced under `controlPlane.patches` in [talconfig.yaml](../../talos/talconfig.yaml). This merges with the existing `bind-address` extra argument in `controller/cluster.yaml`; no kubelet or volume patch changes. Keep the PR draft and unmerged until the task guards in [#663](https://github.com/kelchm/home-lab/issues/663) and [#293](https://github.com/kelchm/home-lab/issues/293) land. Coordinate application with the pending Talos v1.13.11 node cycle and retain the image-GC configuration from [#745](https://github.com/kelchm/home-lab/pull/745). The [ephemeral-storage runbook](talos-ephemeral-storage.md) now records that image GC 75/70 was separately applied without reboot; it need not be applied again solely for this change.

Measured 2026-10-04 around 22:20–22:23 UTC using read-only Kubernetes and Talos queries: all three controller-manager static pods use `registry.k8s.io/kube-controller-manager:v1.36.4`, with no threshold flag or `--config` argument, and `--controllers=*,tokencleaner`. Thus the effective threshold is the [v1.36.4 default of 12,500](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/controller/podgc/config/v1alpha1/defaults.go). Talos's controllerManagerConfig on node 1 independently shows only `bind-address` under extraArgs. Nodes run Talos 1.13.10; the repository requests 1.13.11 and Kubernetes 1.36.4.

The live pod list contained **145 pods: 139 Running, 6 Succeeded, 0 Failed**. Completed history was ai 1 (functional probe), longhorn-system 2 (daily/weekly backup), and media 3 (recyclarr); the oldest was October 2. A retained VictoriaMetrics query `max_over_time((sum(kube_pod_status_phase{phase=~"Failed|Succeeded"}))[30d:5m])` returned **11**. This is a sampled 30-day maximum, not a guarantee about short bursts or missing scrape intervals. A threshold of 25 is over twice that sampled maximum and over four times the current history.

[PR #303](https://github.com/kelchm/home-lab/pull/303) introduced the [50-object quota component](../../kubernetes/components/pod-quota/resourcequota.yaml), with [kube-system 100](../../kubernetes/apps/kube-system/kustomization.yaml) and [longhorn-system 75](../../kubernetes/apps/longhorn-system/kustomization.yaml) overrides. Live `pod-count` quotas agree:

| Namespace | count/pods used / hard | Nonterminated pods | Headroom if all 25 retained pods were here |
|---|---:|---:|---:|
| ai | 11 / 50 | 10 | 15 |
| cert-manager | 3 / 50 | 3 | 22 |
| cnpg-system | 1 / 50 | 1 | 24 |
| default | 1 / 50 | 1 | 24 |
| flux-system | 5 / 50 | 5 | 20 |
| homepage | 1 / 50 | 1 | 24 |
| identity | 5 / 50 | 5 | 20 |
| iot | 3 / 50 | 3 | 22 |
| kube-system | 36 / 100 | 36 | 39 |
| lemon-manuals | 1 / 50 | 1 | 24 |
| longhorn-system | 32 / 75 | 30 | 20 |
| media | 14 / 50 | 11 | 14 |
| network | 11 / 50 | 11 | 14 |
| observability | 19 / 50 | 19 | 6 |
| printing | 2 / 50 | 2 | 23 |

`count/pods` counts all pod objects, including terminated ones. Flux's separate scoped `pods: 1k` quota is not the object-count guard. System namespaces cilium-secrets, kube-node-lease and kube-public have no live `count/pods` quota. GC is cluster-wide; those namespaces still contribute to its threshold.

25 leaves room for a complete 36-pod kube-system replacement set even with all retained history there (36 + 36 + 25 = 97). Longhorn can fit a complete current 30-pod replacement set when retaining its normal two completed backup pods (30 + 30 + 2 = 62), but not if all 25 global history slots accumulate there (85 > 75). Check actual quota headroom before recovery; prune selected historical objects after preserving evidence if necessary. Observability is the tightest ordinary namespace, with six slots in the conservative 25-history case. This policy preserves useful normal history without pretending every namespace can retain 25 pods during a full replacement.

50 or 100 would let media, the incident namespace, reach its quota before GC begins at today's baseline (36 free object slots versus 45 additional terminations needed to exceed 50 globally). With 25, a runaway namespace at current occupancy can cross the GC trigger within its quota. This is not guaranteed for future near-full namespaces, nor is it a strict instantaneous cap. The [v1.36.4 controller](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/controller/podgc/gc_controller.go) checks every 20 seconds and collects excess terminated pods, prioritizing Evicted pods, then creation time. Pending/Running/Unknown pods are outside this threshold. Finalizers, errors and bursts can delay convergence. Churn in one namespace can erase another's history; there is no per-namespace or time retention guarantee. ReplicaSet rollout revisions are separate objects and are not collected by this threshold. Preserve important diagnostics in logs before relying on pod history.

## Incident evidence and limitations

The May 12 [remediation PR #55](https://github.com/kelchm/home-lab/pull/55), preserved in commit `e76e790b088e7d45c45fe1d96fc0c65873763c66`, independently records qBittorrent in a `SysctlForbidden` admission loop and suspension to stop a **1000+-pod** loop. The original [#54](https://github.com/kelchm/home-lab/pull/54) manifest requested `net.ipv4.conf.all.src_valid_mark`; #55 added that unsafe sysctl to the kubelet allowlist, later removed after refactoring. The exact peak, node distribution and roughly 20-minute duration reported in #56 cannot be remeasured: live metrics retention is 30d, logs are configured for 30d, and a May 12 failed-pod metrics query returned no data. Do not interpret absent historical series as zero failures.

Read-only etcd status on all three members showed a common leader, equal raft/applied indexes, no reported errors, **127/127/130 MB DB size** and approximately **81 MB in use each**. No causal etcd size benefit can be measured before rollout; deletion frees logical records, not necessarily physical DB allocation.

## Rollout and verification

**Future procedure only; no node application, staging, upgrade or reboot was performed during preparation.** The threshold alone needs no node reboot: Talos [allows immediate `.cluster` updates](https://docs.siderolabs.com/talos/v1.13/configure-your-talos-cluster/system-configuration/editing-machine-configuration), and its [v1.13.10 static-pod controller](https://github.com/siderolabs/talos/blob/v1.13.10/internal/app/machined/pkg/controllers/k8s/control_plane_static_pod.go) merges controller-manager extraArgs into the pod command and updates the static pod. An immediate application recreates the controller-manager container; changing a leader can cause an election. The combined approved Talos upgrade still uses its planned power cycle. Upgrading the installer alone does not apply new machine-config arguments.

1. After #663/#293 land, review the combined diff and obtain approval for the guarded roll. Follow the [Talos rollout skill](../../.agents/skills/talos-rollout/SKILL.md): preflight, snapshot, one node at a time, etcd member health/quorum, all nodes Ready, all Longhorn volumes healthy and all instance-managers Ready with `lhnet1` before proceeding.
2. Generate fresh config through the new guarded workflow. For local preparation use `mise exec -- talhelper genconfig --offline-mode --out-dir <private-temporary-directory>` from `talos/`; never overwrite credential symlinks in a worktree. Generated configs contain secrets. Validate each rendered control-plane config has the string `"25"` at `cluster.controllerManager.extraArgs.terminated-pod-gc-threshold`, retains `bind-address`, and matches desired component versions. Inspect the per-node dry-run diff privately; include only approved pending changes. Coordinate staged config versus immediate config with the guarded workflow: staging delays this flag until the power cycle; immediate application restarts the static pod before it.
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

For the acceptance test, after separate approval for workload writes, first save names/UIDs and logs of existing terminated pods and record deployment/ReplicaSet readiness and revision history. Use one disposable namespace with `count/pods: 35`; create at most **30 standalone pods**, in batches of five, with `restartPolicy: Never`, a cached approved small image, tiny CPU/memory requests, no volumes or privileges, and commands that exit successfully or fail once. Do not create a ReplicaSet admission loop or change the live threshold for testing. Abort on production readiness loss, quota trouble or growing controller errors. The maximum intentional API footprint is 30 pods plus namespace/quota; launch fewer if current terminal history already approaches 25. Keep test objects free of finalizers and omit TTL/Job controllers so deletion is attributable to PodGC.

Confirm some test pods reach Succeeded and Failed, global terminated count exceeds 25, then converges to at most 25 within two minutes after the final completions (several 20-second scans). Record surviving/deleted UIDs and GC logs/metrics to distinguish collection from another actor. Existing older completed pods may be collected: this is an intended retention consequence, requiring the diagnostics backup above. Confirm newest useful history remains, ordinary workload readiness and ReplicaSet revision history are unaffected, and quota headroom recovers. After convergence, run an approved ordinary stateless rollout and confirm readiness/revision history; verify recovery headroom without deliberately disrupting storage. Remove the disposable namespace. Compare etcd health, errors, DB size/in-use bytes and request latency before/after, allowing temporary write load and no immediate physical shrink. Observe for at least 15 minutes. Record evidence in #56 and leave it open until these checks pass.

## Rollback

If controller-manager health or collection behavior is unacceptable, stop the roll. For an already changed node, an approved targeted patch setting the threshold to `"12500"` restores the prior effective policy with only a static-pod restart. Do not use a JSON6902 patch against these multi-document machine configs. Revert the dedicated source patch and talconfig reference, regenerate fresh config, and follow guarded per-node application. A Git revert alone does not change live Talos configuration. Keep rollout/rollback approval separate from merge. Deleted pod objects and their local API history cannot be recovered by increasing the threshold; retain diagnostics beforehand.
