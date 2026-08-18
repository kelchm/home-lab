# Multus Fail-Closed CNI Publication Cutover

One-time cutover moving Cilium's CNI conflist out of containerd's live CNI directory, so `00-multus.conf` becomes the only config containerd can load.

**Read this fully before starting.** Applied carelessly this leaves a node unable to create any pod sandbox. It is safe when rolled one node at a time with the verification below.

## What changes

| | Before | After |
| --- | --- | --- |
| Cilium conflist | `/etc/cni/net.d/05-cilium.conflist` | `/etc/cni/multus/net.d/05-cilium.conflist` |
| Containerd's live dir | Multus conf **and** a standalone-valid Cilium conflist | `00-multus.conf` only |
| Multus down | containerd falls back to Cilium; pods start missing their Multus interfaces | containerd reports CNI uninitialized; no sandbox is created |

Two settings do this:

- `cni.confPath: /etc/cni/multus/net.d` in the Cilium HelmRelease — the hostPath of the agent's `etc-cni-netd` volume (`type: DirectoryOrCreate`, so the directory is created on demand).
- `multusAutoconfigDir` / `multusMasterCNI` / `readinessindicatorfile` in the `multus-daemon-config` ConfigMap.

No DaemonSet patch is needed: the upstream thick manifest already mounts `multus-conf-dir` at `/etc/cni/multus/net.d`, using the same path inside the container as on the host. Verify this holds before starting, since it is the assumption the whole cutover rests on:

```sh
kubectl -n kube-system get ds kube-multus-ds -o json \
  | jq -r '.spec.template.spec.volumes[] | select(.name=="multus-conf-dir") | .hostPath.path'
```

Expected: `/etc/cni/multus/net.d`. If this is absent, stop — the ConfigMap change would point Multus at a directory it cannot see.

## Why fail-closed is the goal

A `NoSchedule` taint cannot protect pods that are already bound to a node. After a reboot kubelet recreates their sandboxes directly and the scheduler is never consulted, so any node where containerd can load a Cilium-only config will happily produce pods missing their Multus interfaces. Making the config *unloadable* is the only thing that closes it. See [`multus-conf-absent-recovery.md`](multus-conf-absent-recovery.md) for the incident this came from.

## Accepted trade-off

If Multus never becomes healthy on a node, that node runs no CNI-networked pods at all. This is deliberate: a node that cannot attach storage NICs is more useful loudly broken than quietly serving pods with missing interfaces.

Host-networked components are unaffected and can still bootstrap the node, because CRI does not invoke CNI for them — this covers the Cilium agent, Multus itself, `cni-ready-untaint`, and Talos control-plane static pods.

## Pre-cutover checks

1. All nodes healthy and all Longhorn volumes `healthy` — this cutover restarts the Cilium agent, and you do not want it racing a rebuild.
   ```sh
   kubectl get nodes
   kubectl -n longhorn-system get volumes.longhorn.io \
     -o custom-columns='NAME:.metadata.name,ROBUSTNESS:.status.robustness'
   ```
2. No node currently carries `node.multus.io/not-ready`.
3. Record the current Cilium conflist so you can compare after:
   ```sh
   talosctl -n <node> read /etc/cni/net.d/05-cilium.conflist
   ```

## Cutover

Merge the PR, then let Flux reconcile **one node at a time**. Both DaemonSets roll independently, so pin them rather than letting both sweep the cluster.

For each node, in order, completing every step before moving on:

1. Wait for the Cilium agent on that node to restart and write the conflist to the new location:
   ```sh
   talosctl -n <node> read /etc/cni/multus/net.d/05-cilium.conflist
   ```
   It must match the content recorded in the pre-checks.

2. Wait for Multus on that node to regenerate its config against the new master path:
   ```sh
   talosctl -n <node> read /etc/cni/net.d/00-multus.conf
   ```
   `clusterNetwork` must now read `/etc/cni/multus/net.d/05-cilium.conflist`.

3. **Delete the stale conflist.** Changing `cni.confPath` does not remove the old file, and while it remains, containerd can still fall back to it — the cutover has no effect until this is done.
   ```sh
   talosctl -n <node> ls /etc/cni/net.d/
   ```
   `/etc/cni/net.d` must end up containing `00-multus.conf` and nothing else containerd recognizes (`.conf`, `.conflist`, `.json`). The `multus.d` subdirectory is fine.

4. Verify a new pod on that node gets its Multus interface:
   ```sh
   kubectl -n longhorn-system delete pod <longhorn-manager-on-node>
   kubectl -n longhorn-system exec <new-pod> -c longhorn-manager -- ip -br addr show net1
   ```
   Expect an address in the storage range. Restarting `longhorn-manager` is safe; **do not** restart `instance-manager` pods to test this — that detaches volumes.

5. Confirm volumes are still `healthy` before touching the next node.

## Verifying fail-closed actually works

Do this once, on one node, after the rollout. It is the only way to know the property holds rather than assuming it.

```sh
# Stop Multus on a single node
kubectl -n kube-system delete pod <multus-pod-on-node>

# Immediately, while it is down, force a sandbox recreation on that node
kubectl -n longhorn-system delete pod <longhorn-manager-on-node>
kubectl -n longhorn-system get pod <new-pod> -o wide
```

Expected: the pod sits in `ContainerCreating` with `FailedCreatePodSandBox` until Multus returns, then starts normally with `net1`.

A pod that reaches `Running` without `net1` means the cutover did not take — almost always a leftover config in `/etc/cni/net.d` from step 3.

## Rollback

Revert the PR and delete the relocated conflist so Cilium republishes to the original path:

```sh
talosctl -n <node> ls /etc/cni/multus/net.d/
```

Rollback restores the previous behaviour, including the original failure mode. It does not require a reboot.

## See also

- [`multus-conf-absent-recovery.md`](multus-conf-absent-recovery.md) — recovering pods that are already Cilium-only, and the staleness detection query
- [`longhorn-storage-network-cutover.md`](longhorn-storage-network-cutover.md) — why Longhorn depends on the storage VLAN
- [containerd CRI config](https://github.com/containerd/containerd/blob/main/docs/cri/config.md) — `max_conf_num` and CNI config selection
