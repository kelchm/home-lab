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

```sh
kubectl -n <namespace> delete pod <name> --force --grace-period=0
```

`--force --grace-period=0` is mandatory, not stylistic. A graceful delete can hit a containerd sandbox-name-reservation conflict — kubelet's CNI ADD got cancelled mid-flight (RST_STREAM / DeadlineExceeded), the sandbox name stays reserved against the dead pod's UID, and replacements with the same UID fail with `failed to reserve sandbox name "<podname>_<namespace>_<UID>_0": name "..." is reserved for "<old-sandbox-id>"`. Force-delete works because the controller (DaemonSet / StatefulSet / Deployment) creates the replacement pod with a fresh UID.

For Longhorn instance-managers, replicas inside the recycled IM may be marked failed and rebuilt against the new IM. Watch progress:

```sh
kubectl -n longhorn-system get replica -o wide -w
```

Wait until rebuilds complete and the engine reports the volume Healthy in the Longhorn UI before considering the recovery done.

## After recovery

If you executed this runbook, capture which node it happened on, what triggered the Multus disturbance (DS roll? crash? reboot?), and whether the reconciler taint was applied at the time. A second occurrence in normal operation suggests the reconciler is unreliable — investigate `cni-ready-untaint` DS health rather than treating recurring recycles as routine.

## See also

- Issue [#4](https://github.com/kelchm/home-lab/issues/4) — original analysis of the conf-absent race
- PR [#1](https://github.com/kelchm/home-lab/pull/1) — predecessor fix (one-shot untaint, since superseded by the continuous reconciler)
- [`docs/storage-benchmarks.md`](../storage-benchmarks.md) — why we run Multus + storage VLAN at all
- [`docs/runbooks/longhorn-storage-network-cutover.md`](longhorn-storage-network-cutover.md) — original cutover procedure
