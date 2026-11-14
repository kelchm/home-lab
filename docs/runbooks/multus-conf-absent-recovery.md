# Multus Conf-Absent Pod Recovery

Recovery procedure for pods stuck running on Cilium-only after a Multus restart, when the continuous taint reconciler didn't catch the window.

## Background

`multus-cni` thick mode deletes `/etc/cni/net.d/00-multus.conf` on SIGTERM (the [`monitorPluginConfiguration`](https://github.com/k8snetworkplumbingwg/multus-cni/blob/v4.3.0/pkg/server/config/manager.go) goroutine's deferred `os.Remove`). Upstream PR [#1338](https://github.com/k8snetworkplumbingwg/multus-cni/issues/1338) attempted graceful shutdown but was closed stale Dec 2024; only OpenShift carries a downstream fix.

Two mechanisms guard the resulting window:

1. **Fail-closed config publication (primary).** Cilium writes its conflist to `/etc/cni/multus/net.d` (`cni.confPath`) rather than `/etc/cni/net.d`, and Multus reads it from there as its master CNI. Containerd's live CNI directory therefore contains *only* `00-multus.conf`. When Multus is down that directory is empty, containerd reports CNI uninitialized, and no sandbox is created at all — instead of one created on Cilium alone.
2. **`cni-ready-untaint` (supplementary).** Applies `node.multus.io/not-ready:NoSchedule` whenever the conf is absent. This keeps the scheduler from piling pending pods onto a node whose CNI is down. It is a scheduling nicety, **not** a correctness mechanism — see below.

## When this runbook applies

Under the fail-closed arrangement a pod should never reach Running while missing a Multus attachment; it should fail with `FailedCreatePodSandBox` instead. This runbook still applies if you find a Cilium-only pod, which now indicates one of:

- The node predates the fail-closed cutover, or a stale `/etc/cni/net.d/05-cilium.conflist` was left behind by it (see the cutover runbook's cleanup step)
- Something other than Cilium wrote a containerd-recognized config into `/etc/cni/net.d`
- A stale `00-multus.conf` survived a crash — containerd sees a syntactically valid config, but `multus-shim` fails against the absent socket. Still fail-closed, though the node may report `Ready` inaccurately.

Multus's `[error]` lines about a missing cached delegates file on CNI DEL do not indicate this failure when the same container ID already had a `DEL finished` with a non-empty `Netns`; see [Accepted warnings](logging.md#accepted-warnings).

### Why the taint alone was never sufficient

`node.multus.io/not-ready` is a `NoSchedule` taint, which is a *scheduler* constraint. Pods already bound to a node via `.spec.nodeName` are not rescheduled after a reboot — kubelet simply calls `RunPodSandbox` for them again, with the scheduler uninvolved. A `NoSchedule` taint neither evicts them nor prevents kubelet from starting them.

This is exactly what happened on 2026-08-16: all three nodes rebooted, Cilium's conflist was already on disk, Multus wrote `00-multus.conf` roughly a minute later, and every `longhorn-manager` pod was recreated in that window with `eth0` only. Their `network-status` annotations still described the pre-reboot `net1`, so nothing surfaced the failure, and the Longhorn NFS backup target sat unavailable for two days. The taint could not have prevented it regardless of health, which is why the fail-closed mechanism above exists.

## Symptom

A pod with `k8s.v1.cni.cncf.io/networks` annotation requesting a NAD is running, but its `k8s.v1.cni.cncf.io/network-status` annotation does not list that attachment. For Longhorn instance-managers, this manifests as the IM listening on Cilium pod-CIDR (10.42.x.x) instead of the storage VLAN (10.32.25.x), and engine peers stalling rebuilds at 0%.

Detection query:

```sh
kubectl -n longhorn-system get pod -o json | jq -r '
  .items[]
  | select(.metadata.annotations["k8s.v1.cni.cncf.io/networks"] != null)
  | {
      name: .metadata.name,
      node: .spec.nodeName,
      requested: .metadata.annotations["k8s.v1.cni.cncf.io/networks"],
      status: (.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // "MISSING")
    }'
```

Any pod where `status` is `"MISSING"` or doesn't contain the requested NAD name is broken.

### The above query is not sufficient on its own

When a sandbox is recreated without Multus, the `network-status` annotation is not cleared — it retains the values from the *previous* sandbox and keeps advertising an attachment that no longer exists. The query above then reports the pod as fine. This is how the 2026-08-16 breakage stayed invisible for two days.

Multus writes that annotation only during a successful Multus ADD, so a sandbox created behind its back leaves it stale in every field, including the primary interface IP. Comparing that IP against the pod's live `status.podIP` is a reliable staleness check that needs no exec:

```sh
kubectl get pod -A -o json | jq -r '
  .items[]
  | select(.metadata.annotations["k8s.v1.cni.cncf.io/networks"])
  | select(.metadata.annotations["k8s.v1.cni.cncf.io/network-status"])
  | . as $p
  | ($p.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] | fromjson) as $ns
  | ($ns[] | select(.default == true) | .ips[0]) as $annIP
  | select($annIP != $p.status.podIP)
  | "STALE \($p.metadata.namespace)/\($p.metadata.name) annotation=\($annIP) actual=\($p.status.podIP)"'
```

Any output means that pod's sandbox was created without Multus and its annotation is fiction. Confirm before acting — the interface is the ground truth:

```sh
kubectl -n <namespace> exec <pod> -- ip -br addr
```

## Pre-recovery checks — do not skip

Before recycling pods, confirm the node is *currently* healthy. Recycling onto a still-broken node just chains more stuck sandboxes (containerd's sandbox-name reservation outlives the failed CNI ADD).

1. **Multus pod is running on the affected node**
   ```sh
   kubectl -n kube-system get pod -l app=multus -o wide --field-selector spec.nodeName=<node>
   ```

2. **CNI conf is present on the node**
   ```sh
   talosctl -n <node> read /etc/cni/net.d/00-multus.conf | head -5
   ```

3. **Reconciler reflects healthy state — node is NOT tainted**
   ```sh
   kubectl get node <node> -o jsonpath='{.spec.taints}' | jq
   ```
   `node.multus.io/not-ready` should not appear.

4. **No active CNI EAGAIN cascade**
   ```sh
   POD=$(kubectl -n kube-system get pod -l app=multus \
     --field-selector=spec.nodeName=<node> -o name)
   kubectl -n kube-system logs --since=2m "$POD" \
     | grep -iE 'DelegateAdd|temporarily unavailable'
   ```
   Empty output is what you want. If you see `cannot set "" interface name to "eth0": resource temporarily unavailable`, **wait** — recycling now creates more stuck pods. Investigate why CNI ADDs are failing on this node before proceeding.

## Recovery

For a Longhorn instance-manager, stop here and follow [Longhorn instance-managers](#longhorn-instance-managers) instead: deleting one that still carries engines and replicas fails them. For any other pod, delete it gracefully and let its controller replace it:

```sh
kubectl -n <namespace> delete pod <name>
kubectl -n <namespace> get pod -o wide -w   # until the replacement is Running and Ready
```

Then rerun the staleness query above and confirm the replacement's interface with `ip -br addr`.

Any delete gets a replacement with a fresh UID, so a graceful delete does not inherit the old sandbox's name reservation. The `failed to reserve sandbox name "<podname>_<namespace>_<UID>_0"` error belongs to kubelet retrying a sandbox for the *same* pod after a cancelled CNI ADD, which is why the pre-recovery checks require a clean CNI before you delete anything. A graceful delete also lets kubelet stop the containers and run CNI DEL, which releases the sandbox, its name and its IPAM allocations.

### Longhorn instance-managers

An instance-manager runs the engines and replicas of the volumes on its node. Deleting one that still carries them fails every replica it hosts, and the engines with them. Before deleting it:

1. List the volumes it serves and let them move off: stop or move their workloads, or drain the node with a PodDisruptionBudget-respecting `kubectl drain` as in the [cutover procedure](longhorn-storage-network-cutover.md#roll-managers-then-one-node-at-a-time). Do not add `--force` or `--disable-eviction`.
2. Record the old pod's UID, container and sandbox IDs.
3. Delete it gracefully, as above. Longhorn may not recreate it while the node is cordoned; once CNI is healthy, uncordon so the replacement can start.
4. Require the replacement to have a new UID and sandbox, and [prove its storage interface](longhorn-storage-network-cutover.md#prove-each-replacement-has-the-filter): `lhnet1` with an address from `10.32.25.129`–`.142` in the `10.32.25.128/28` subnet, and the storage routes.
5. Run `.agents/skills/talos-rollout/scripts/verify-node.sh <node>`. Every in-use volume must be attached and healthy, with none degraded or faulted, and every instance-manager Running and Ready with `lhnet1`. Watch rebuilds with `kubectl -n longhorn-system get replica -o wide -w`.

### When to force-delete

Only for a pod already diagnosed as stuck: it has stayed `Terminating` well past its grace period, and kubelet cannot finish the teardown, for example because CNI DEL keeps failing. Force deletion removes the API object without waiting for kubelet, so the old containers can keep running beside their replacement; for an instance-manager that means two processes claiming the same engines and replicas. Before forcing, confirm on the node that the pod's containers are gone, and for an instance-manager complete steps 1 and 2 above:

```sh
talosctl -n <node-ip> containers -k | grep <pod-name>   # must print nothing
kubectl -n <namespace> delete pod <name> --force --grace-period=0
```

Then wait for the replacement as for a graceful delete: Running and Ready, a clean staleness query and the attachment present in `ip -br addr`. For an instance-manager, uncordon the node if step 1 drained it, so Longhorn can start the replacement, and continue from step 4.

## After recovery

If you executed this runbook, capture which node it happened on, what triggered the Multus disturbance (DS roll? crash? reboot?), and whether the reconciler taint was applied at the time. A second occurrence in normal operation suggests the reconciler is unreliable — investigate `cni-ready-untaint` DS health rather than treating recurring recycles as routine.

## See also

- Issue [#4](https://github.com/kelchm/home-lab/issues/4) — original analysis of the conf-absent race
- PR [#1](https://github.com/kelchm/home-lab/pull/1) — predecessor fix (one-shot untaint, since superseded by the continuous reconciler)
- [`docs/storage-benchmarks.md`](../storage-benchmarks.md) — why we run Multus + storage VLAN at all
- [`docs/runbooks/longhorn-storage-network-cutover.md`](longhorn-storage-network-cutover.md) — original cutover procedure
