# Terminated pod garbage collection

## Policy and measured state

The cluster uses **500** at `cluster.controllerManager.extraArgs.terminated-pod-gc-threshold`, through [terminated-pod-gc.yaml](../../talos/patches/controller/terminated-pod-gc.yaml) under `controlPlane.patches` in [talconfig.yaml](../../talos/talconfig.yaml). The patch preserves the existing `bind-address` argument and does not alter kubelet or volume settings.

Pre-rollout baseline measured 2026-10-04 around 22:20–22:23 UTC using read-only Kubernetes and Talos queries: all three controller-manager static pods used `registry.k8s.io/kube-controller-manager:v1.36.4`, with no threshold flag or `--config` argument, and `--controllers=*,tokencleaner`. Thus the effective threshold was the [v1.36.4 default of 12,500](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/controller/podgc/config/v1alpha1/defaults.go). Talos's controllerManagerConfig on node 1 independently showed only `bind-address` under extraArgs. Nodes ran Talos 1.13.10; the repository requested 1.13.11 and Kubernetes 1.36.4.

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

Pre-rollout read-only etcd status on all three members showed a common leader, equal raft/applied indexes, no reported errors, **127/127/130 MB DB size** and approximately **81 MB in use each**. No collection-related etcd size benefit has been demonstrated: the observed terminated count remains below 500. Deletion frees logical records, not necessarily physical DB allocation.

## Verified rollout

Applied on 2026-10-05 UTC (October 4 EDT), in order **k8s-prod-1 → k8s-prod-3 → k8s-prod-2**, using `task talos:apply-node IP=<node-ip> MODE=no-reboot`. The controller-manager containers started with `--terminated-pod-gc-threshold=500` at 00:29:09, 00:30:59 and 00:31:58 UTC respectively. All three are Ready and the freshly rendered configs retain `bind-address` alongside `"500"`. The controller-manager lease moved from node 2 to node 3 when the former leader was recreated. API server and scheduler container identities were unchanged across application; no Running workload pod was unready at the subsequent preflight or monitoring checks.

Each privately reviewed dry-run contained only the 500 argument and the stored installer image pin moving from v1.13.7 to the repository's v1.13.11. Changing the pin does not upgrade the OS. All nodes continue to run **Talos 1.13.10 / Kubernetes 1.36.4**, with image GC 75/70 already applied per [the ephemeral-storage runbook](talos-ephemeral-storage.md). No node was rebooted, drained or upgraded, and no database topology was changed.

Preflight passed before the first node and between applications: all three etcd members healthy with a common leader, all nodes Ready and uncordoned, 24/24 Longhorn volumes healthy, and all three instance-managers Ready with `longhorn-system/storage-network` on `lhnet1`. Talos health also passed between applications. The verified etcd snapshot is outside the repository at `/Users/kelchm/.local/state/home-lab/rollouts/pod-gc-20261004/etcd.snapshot`; Talos reported 127,324,192 bytes, revision 111638668 and 4,146 keys. Private dry-run logs and monitoring evidence reside alongside it.

A 15-minute monitor from 00:34:22 to 00:49:22 UTC recorded 21 samples with all three controller-managers Ready, no further container restarts, and node 3 holding the leader lease throughout. All nodes remained Ready and uncordoned, all 24 Longhorn volumes healthy, all three instance-managers Ready on `lhnet1`, and all etcd members healthy. No Running pod was unready at the monitoring checks. Terminated history stayed at **6 Succeeded / 0 Failed**, below 500, so configuration and health are verified but collection above the threshold was not exercised. The final preflight and Talos health check passed. The startup dynamic-volume-plugin directory permission warning matches September 21 logs; initial node-cache lookup warnings did not recur. A single CronJob update conflict was retried and the Grafana functional probe completed successfully at 00:40:04 UTC.

## Applying or verifying a future change

The threshold needs no node reboot: Talos [allows immediate `.cluster` updates](https://docs.siderolabs.com/talos/v1.13/configure-your-talos-cluster/system-configuration/editing-machine-configuration), and its [static-pod controller](https://github.com/siderolabs/talos/blob/v1.13.10/internal/app/machined/pkg/controllers/k8s/control_plane_static_pod.go) merges extra arguments into the command. The verified application recreated only the controller-manager containers. A leader restart can cause an election.

Use a current `main` checkout with usable credentials. Follow the [Talos rollout procedure](../../.agents/skills/talos-rollout/SKILL.md), take and verify an etcd snapshot, and operate on one node at a time. `task talos:apply-node IP=<node-ip> MODE=no-reboot` regenerates configuration, prints the dry-run diff and prompts before applying. Inspect every diff for unrelated pending changes; `no-reboot` fails instead of rebooting. Verify the rendered and live argument agree, healthy leadership and normal reconciliation, all nodes Ready, etcd healthy, and Longhorn volumes and `lhnet1` attachments healthy before proceeding. A separately approved OS upgrade uses `task talos:upgrade-node IP=<node-ip>`; the installer pin alone does not perform that upgrade.

Read-only comparisons before and after:

```bash
mise exec -- kubectl -n kube-system get pods -l component=kube-controller-manager -o json \
  | jq '.items[] | {name:.metadata.name,containers:[.spec.containers[] | {image,command,args}],status:.status.containerStatuses}'
mise exec -- kubectl get pods -A -o json \
  | jq '[.items[] | select(.status.phase == "Failed" or .status.phase == "Succeeded")] | {count:length,pods:map({namespace:.metadata.namespace,name:.metadata.name,phase:.status.phase,created:.metadata.creationTimestamp})}'
mise exec -- kubectl get resourcequota -A
mise exec -- talosctl -n 10.32.30.11,10.32.30.12,10.32.30.13 etcd status
```

Observe cluster health, controller-manager logs, leadership, terminated counts, quota usage and etcd for at least 15 minutes after application. A below-threshold observation verifies rollout health but does not demonstrate collection. To verify collection, preserve diagnostics and observe a natural crossing above 500, then convergence to at most 500 over several 20-second scans. If a deliberate test is needed, separately approve a disposable namespace with a 520-object quota and a lifetime ceiling of 501 standalone test pods, at most five active at once; stop creation when the threshold is crossed and remove the namespace afterward. Leave existing namespace quotas unchanged, omit finalizers and competing TTL/Job cleanup, and stop on workload readiness loss or controller errors. Check deleted/surviving UIDs, GC logs or metrics, ordinary rollout history and actual recovery headroom. Do not infer that quota headroom is reserved or that pod deletion immediately shrinks etcd's physical DB.

## Rollback

If controller-manager health or collection behavior is unacceptable, stop the roll. For an already changed node, an approved targeted patch setting the threshold to `"12500"` restores the prior effective policy with only a static-pod restart. Do not use a JSON6902 patch against these multi-document machine configs. Revert the dedicated source patch and talconfig reference, regenerate fresh config, and follow guarded per-node application. A Git revert alone does not change live Talos configuration. Keep rollout/rollback approval separate from merge. Deleted pod objects and their local API history cannot be recovered by increasing the threshold; retain diagnostics beforehand.
