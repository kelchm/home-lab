# Multus Conf-Absent Pod Recovery

Recovery procedure for pods stuck running on Cilium-only after a Multus restart, when the continuous taint reconciler didn't catch the window.

## Background

`multus-cni` v4.2.4 thick mode deletes `/etc/cni/net.d/00-multus.conf` on SIGTERM (the [`monitorPluginConfiguration`](https://github.com/k8snetworkplumbingwg/multus-cni/blob/v4.2.4/pkg/server/config/manager.go) goroutine's deferred `os.Remove`). Upstream PR [#1338](https://github.com/k8snetworkplumbingwg/multus-cni/issues/1338) attempted graceful shutdown but was closed stale Dec 2024; only OpenShift carries a downstream fix. We work around it with the continuous reconciler in `cni-ready-untaint` (kube-system), which applies `node.multus.io/not-ready:NoSchedule` whenever the conf file is absent.

## When this runbook applies

The reconciler is the primary defense — sub-second reaction time, no human action needed. A pod can still end up Cilium-only if either:

- It was admitted in the brief window between the conf disappearing and the reconciler tainting (very rare; the original 2026-05-07 incident was caused by *no* reconciler, not by a race within it)
- The reconciler itself was unhealthy when Multus went down — check `kubectl -n kube-system get ds cni-ready-untaint` shows desired = current = ready on every node

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
   kubectl -n kube-system logs ds/kube-multus-ds --since=2m \
     --selector spec.nodeName=<node> | grep -iE 'DelegateAdd|temporarily unavailable'
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
