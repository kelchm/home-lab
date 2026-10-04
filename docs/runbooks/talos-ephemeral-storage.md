# Talos ephemeral storage

Use this runbook when `/var` is filling or `KubeNodeEphemeralStorageHigh` fires. `/var` is the XFS EPHEMERAL partition, capped at 100 GiB. `/var/mnt/longhorn` is a separate user volume; its replica data does not consume EPHEMERAL space.

## Policy and verified state

The repository sets image GC high/low to **75/70** in [machine-kubelet.yaml](../../talos/patches/global/machine-kubelet.yaml). The warning in [platform-alerts.yaml](../../kubernetes/apps/observability/victoria-metrics-k8s-stack/app/platform-alerts.yaml) fires when writable `/var` has less than 20% available for 15 minutes. It is restricted to `cluster=k8s-prod`, `job=node-exporter`, XFS and `/var`; it accepts both hostname and legacy IP instance labels. Warning notifications reach Pushover through [the existing alert routing](alerting.md).

**Last verified live state: 2026-10-04, before application of this policy.** All nodes ran Talos 1.13.10, Kubernetes 1.36.4 and containerd 2.2.7. Effective kubelet configuration was GC high/low **85/80**, minimum image age 2 minutes, age-based GC disabled, and hard eviction thresholds `imagefs.available: 15%`, `nodefs.available: 10%`, both inode-free thresholds 5%, and memory available 100 MiB. Rollout and acceptance are tracked in [#744](https://github.com/kelchm/home-lab/issues/744); do not infer live settings from the repository. Update this paragraph after acceptance.

Both imagefs and nodefs use the same `/var` filesystem. Starting at 75% gives GC room to work before the 80% warning and 85% hard-eviction boundary. Kubelet rounds its GC usage percentage upward to an integer, so these GC trigger percentages are approximate.

Kubelet checks image GC every five minutes and excludes in-use and pinned images, with a minimum image age. It counts containerd's reported **compressed** image size toward the reclaim target; actual space includes unpacked snapshots and shared layers. The 70% low threshold is a reclaim calculation, not a promised physical usage floor. Do not predict reclaimed space from the aggregate content/snapshot ratio. A kubelet restart resets its image history, so the first pass need not remove the oldest cached versions first. Keep `discard_unpacked_layers = false`: [Spegel requires retained content](https://spegel.dev/docs/getting-started/#compatibility).

At `imagefs.available < 15%`, kubelet attempts resource reclamation and may set DiskPressure and evict pods. Hard-pressure eviction does not respect PodDisruptionBudgets and uses zero graceful termination. Controllers may replace pods elsewhere if capacity allows. If the filesystem fills, image pulls, logs and writable pod layers can fail; etcd also resides on `/var`, so exhaustion can affect the control plane. See [node pressure eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/), [image GC implementation](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/kubelet/images/image_gc_manager.go) and [containerd image size accounting](https://github.com/containerd/containerd/blob/v2.2.7/internal/cri/store/image/image.go).

## Investigation baseline

Measured around 19:48–19:59 UTC on 2026-10-04. Values below use GiB (2^30 bytes); all nodes have 99.94 GiB filesystem capacity.

| Measurement | k8s-prod-1 | k8s-prod-2 | k8s-prod-3 |
|---|---:|---:|---:|
| `/var` used / available | 82.20 / 17.73 GiB | 65.18 / 34.75 GiB | 62.12 / 37.82 GiB |
| Used percentage | 82.25% | 65.22% | 62.16% |
| Cached unique image manifest digests | 267 | 194 | 189 |
| Running containers' unique image IDs | 43 | 48 | 51 |
| Current pods' total ephemeral storage | 277 MiB | 232 MiB | 873 MiB |

Physical allocated blocks, measured with `du -x -B1` through the existing Multus pod's `/hostroot` mount:

| Directory | k8s-prod-1 | k8s-prod-2 |
|---|---:|---:|
| `/var/lib/containerd` | 77.74 GiB | 60.73 GiB |
| ↳ overlayfs snapshots | 58.10 GiB | 45.76 GiB |
| ↳ content store | 19.62 GiB | 14.95 GiB |
| `/var/log` | 1.20 GiB | 1.32 GiB |
| `/var/lib/etcd` | 0.48 GiB | 0.48 GiB |
| `/var/lib/kubelet` | 0.13 GiB | 0.004 GiB |
| All reachable allocated blocks on `/var` | 79.75 GiB | 62.74 GiB |

Containerd accounts for 17.00 GiB of the 17.02 GiB node-to-node difference. The roughly 2.45 GiB gap between filesystem usage and reachable allocated blocks is similar on both nodes; metadata and deleted-open files were not individually attributed. Node 1's largest disk emptyDirs were two CNPG scratch volumes of about 63 MiB each, so active pods' temporary files and logs did not explain the difference. Historical image versions were present, but cached-minus-running image counts are not an exact eligibility test: init/completed containers, image volumes, pins and shared layers matter. The exact active-image physical floor and reclaimable bytes remain unmeasured; verify recovery before proceeding beyond the first node.

In the preceding 30 days, node 1 went from about 80.9% to 82.3% used (+1.4 GiB net), with sampled minimum/maximum 69.7%/84.3%. It dropped to about 71% on September 19 and accumulated images again. Nodes 2 and 3 rose from about 41.1%/38.9% to 65.2%/62.2% (+24.1/+23.2 GiB). Node 1 was already higher at the start of the window; this was not a continuous rapid-growth incident. The cause of the September drop is unknown. No DiskPressure was recorded during the window, and sampled logs/events showed no GC failures; this does not prove GC never ran.

## Read-only diagnosis

The prior predictive `NodeFilesystemSpaceFillingUp` warning requires less than 15% free *and* predicted exhaustion within 24 hours, held for one hour. `NodeFilesystemAlmostOutOfSpace` warns below 5% free for 30 minutes. `KubeNodePressure` and `KubeNodeEviction` are info-only and do not notify externally. There is no guaranteed useful first warning from those rules before hard eviction; verify their live definitions through `/api/prometheus/victoriametrics/api/v1/rules` rather than assuming chart defaults.

Run CLI commands through `mise exec --` using usable worktree credentials. Use the shell's `timeout`; `kubectl --request-timeout` misbehaves here. Node addresses are `.11`, `.12`, `.13` in `10.32.30.0/24`.

Read effective kubelet settings and pod attribution:

```bash
timeout 30 mise exec -- kubectl get --raw /api/v1/nodes/k8s-prod-1/proxy/configz | jq '.kubeletconfig | {imageGCHighThresholdPercent,imageGCLowThresholdPercent,imageMinimumGCAge,imageMaximumGCAge,evictionHard,evictionSoft,containerLogMaxSize,containerLogMaxFiles}'
timeout 30 mise exec -- kubectl get --raw /api/v1/nodes/k8s-prod-1/proxy/stats/summary | jq '{node: (.node | {fs,runtime}), pods: [.pods[] | {podRef,ephemeralStorage:."ephemeral-storage",containers,volume}]}'
timeout 30 mise exec -- kubectl get pods -A -o wide --field-selector spec.nodeName=k8s-prod-1
timeout 30 mise exec -- kubectl get events -A --field-selector involvedObject.kind=Node,involvedObject.name=k8s-prod-1 --sort-by=.lastTimestamp
mise exec -- talosctl -n 10.32.30.11 mounts
mise exec -- talosctl -n 10.32.30.11 image list
mise exec -- talosctl -n 10.32.30.11 usage /var/lib/containerd --depth 2
```

Talos `usage` reports logical sizes, which can differ substantially from allocated blocks. Request one path at a time. Avoid an unrestricted traversal of `/var/mnt` or kubelet-mounted PVCs. For physical usage, find the **existing** Multus pod on the selected node and check its host mount before running:

```bash
timeout 30 mise exec -- kubectl -n kube-system get pods -o wide | rg kube-multus
timeout 180 mise exec -- kubectl -n kube-system exec <existing-multus-pod> -c kube-multus -- du -x -B1 --max-depth=2 /hostroot/var
timeout 180 mise exec -- kubectl -n kube-system exec <existing-multus-pod> -c kube-multus -- du -x -B1 --max-depth=1 /hostroot/var/lib/containerd
```

Do not create a privileged diagnostic pod or delete files as part of this procedure. `-x` excludes Longhorn and mounted PVC filesystems. Kubelet's `runtime.imageFs.usedBytes` reports snapshot usage in this containerd version and omits the compressed content store, while its available/capacity values describe the shared filesystem. Node status's image list is truncated; count cached unique digests from Talos's complete image list instead.

Query the `victoriametrics` Grafana datasource:

```promql
100 * (1 - node_filesystem_avail_bytes{cluster="k8s-prod",job="node-exporter",mountpoint="/var",fstype="xfs"}
  / node_filesystem_size_bytes{cluster="k8s-prod",job="node-exporter",mountpoint="/var",fstype="xfs"})
```

For a 30-day range, omit the `platform` filter. Before 2026-10-04 ~17:16 UTC the instances were `10.32.30.11:9100`, `.12:9100`, `.13:9100` with no platform label; afterward they are `k8s-prod-1`, `-2`, `-3` with `platform=k8s`. Join each old/new pair when interpreting the range. Use raw available/size bytes for capacity and `max_over_time(kube_node_status_condition{cluster="k8s-prod",condition="DiskPressure",status="true"}[30d])` for recorded pressure. `kubelet_volume_stats_*` measures mounted volumes, including PVCs, and is not by itself an attribution of host ephemeral bytes.

## Approved application and verification

**This section changes node state.** Require explicit approval for the live Talos application and follow the repository's [talos-rollout skill](../../.agents/skills/talos-rollout/SKILL.md), including preflight, etcd snapshot and health gates. Operate on one node at a time, starting with k8s-prod-1. Merge is a separate decision: Flux deploys the warning but never applies these kubelet settings. With the measured usage, expect the warning to notify after its 15-minute hold and repeat every 12 hours until GC recovery.

Use a targeted two-field patch instead of applying an entire generated machine config. At investigation time, the repository targeted Talos 1.13.11 while nodes ran 1.13.10; applying a full config would mix unrelated changes into this operation. Verify the diff and effective configuration, rather than relying on a successful apply exit status. Talos supports immediate updates under `machine.kubelet`; `--mode=no-reboot` prevents a node reboot, but **Talos restarts kubelet automatically** when these settings change ([service controller](https://github.com/siderolabs/talos/blob/v1.13.10/internal/app/machined/pkg/controllers/k8s/kubelet_service.go)). The live-application approval must cover that restart. Existing workload containers normally continue through containerd; verify readiness and workload health afterward. If effective settings do not change, stop and investigate; an additional manual service restart or reboot requires a separately reviewed and approved action.

Run the following commands in **bash**, keeping the same shell through verification and rollback. After preflight and approval, create private temporary files outside the repository. Save the selected node's effective old settings for rollback, and derive the new patch from the checked-out repository:

```bash
set -euo pipefail
umask 077
var_gc_dir=$(mktemp -d)
node_name=k8s-prod-1
node_ip=10.32.30.11
health_peer_ip=10.32.30.12 # Choose a healthy control-plane node other than node_ip.

timeout 30 mise exec -- kubectl get --raw "/api/v1/nodes/${node_name}/proxy/configz" \
  | jq '{machine:{kubelet:{extraConfig:(.kubeletconfig | {imageGCHighThresholdPercent,imageGCLowThresholdPercent})}}}' \
  > "$var_gc_dir/rollback.json"
mise exec -- yq '{"machine":{"kubelet":{"extraConfig":(.machine.kubelet.extraConfig | {"imageGCHighThresholdPercent":.imageGCHighThresholdPercent,"imageGCLowThresholdPercent":.imageGCLowThresholdPercent})}}}' \
  talos/patches/global/machine-kubelet.yaml > "$var_gc_dir/gc.yaml"
cat "$var_gc_dir/gc.yaml" "$var_gc_dir/rollback.json"
# Inspect the local preview: only the two GC fields may differ.
# Machine-config previews can contain credentials; do not publish raw output.
mise exec -- talosctl -n "$node_ip" patch machineconfig --mode=no-reboot --dry-run --patch "@$var_gc_dir/gc.yaml"
```

Reject missing/non-numeric rollback values or a preview containing other changes. The pinned talosctl is 1.14.2 against 1.13.10 servers; client-side re-encoding can expose version skew. Any diff beyond the two GC fields means stop and investigate. Record the node and private rollback path before applying; if interrupted, reuse that original patch rather than capturing the changed settings as a new baseline. Only after approving that exact diff, apply in the same bash shell:

```bash
mise exec -- talosctl -n "$node_ip" patch machineconfig --mode=no-reboot --patch "@$var_gc_dir/gc.yaml"
```

Verify `/configz` now reports 75/70 and retains the prior eviction/age settings. Observe actual `/var` available bytes and GC events for at least 15 minutes (several cycles), including the two-minute minimum image age. Compare against the pre-apply baseline; do not treat the nominal freed-byte count as measured free space. Recheck image-pull health and running workloads. The first pass may remove substantial unused cache, increasing later pulls; other nodes retain their caches and Spegel remains configured.

Investigate `FreeDiskSpaceFailed` even if usage drops: compressed-size accounting can exhaust the eligible list before its nominal target is met. A failure message alone does not establish the active physical footprint. Stop on repeated failures, DiskPressure, failed pulls or usage remaining at/above the high threshold; do not continue to the next node or manually delete images. Attribute in-use images, image pins, logs and emptyDirs before choosing another policy or a capacity change.

Re-run the read-only preflight and Talos health checks under `mise exec --`:

```bash
timeout 180 mise exec -- .agents/skills/talos-rollout/scripts/preflight.sh "$node_name"
timeout 180 mise exec -- talosctl health --nodes "$health_peer_ip"
```

Choose a healthy control-plane peer for the health check. Require etcd quorum, every node Ready and uncordoned, all in-use Longhorn volumes healthy, and every instance-manager Running and Ready with its storage-network attachment on `lhnet1` before repeating preflight for the next node. The post-upgrade `verify-node.sh` requires the OS version in `talenv.yaml` and would reject these 1.13.10 nodes against the 1.13.11 target; an OS upgrade is outside this GC application. Nodes 2 and 3 initially sit below 75%; immediate collection there is not an acceptance requirement. Confirm their effective settings nonetheless.

If rollback is approved, apply only the saved two-field rollback patch with `--mode=no-reboot`, then verify `/configz` and cluster health again. The saved effective values restore 85/80 explicitly in live config; the repository retains 75/70 until reverted. Raising thresholds cannot restore removed image cache; images return on future pulls. Keep the saved patch until acceptance.

After all nodes pass, verify the live warning rule, its routing and resolution as described in [alerting](alerting.md), update the verified-state paragraph, and record effective settings, before/after availability, GC events and health evidence in #744. Close the issue only after live acceptance, not merely after merging the PR.
