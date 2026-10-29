# Longhorn Storage Network Cutover

Runbook for moving Longhorn replica engine ↔ replica engine traffic onto VLAN 25 (2.5GbE storage NIC) via Multus + bridge CNI. **Already executed for the prod cluster on 2026-05-02.** Kept as a reference for similar work; re-inventory cluster-specific node paths, namespaces, workloads, and safety gates before reuse.

## Storage-interface isolation (#1120)

**Prepared; production activation is pending.** The 2026-10-29 tenant test reached Longhorn control listeners over IPv4 and IPv6. The proposed bridge → tuning chain limits incoming storage-interface IPv4 to Longhorn peers, nodes and the NAS, and disables IPv6 on that interface. This section is a rolling filter activation, separate from the historical May cutover below. It does not change the Longhorn storage-network setting, detach every volume together, or upgrade/reboot Talos.

Merging the isolation PR deploys a privileged CNI-binary installer and changes the live NAD through Flux. Existing sandboxes keep their configuration, but any replacement can immediately use the filter. **Merge only inside the approved activation window.** Approval must include manager replacement, one-node-at-a-time drains with the chosen database availability strategy, read-only network inspection inside Longhorn containers, the eight-check acceptance batch, one normal Longhorn backup, and rollback if needed.

### Before merge

1. Run the [Talos rollout preflight](../../.agents/skills/talos-rollout/SKILL.md#preflight-the-cluster) with usable explicit kubeconfig/talosconfig paths. Preserve the original NAD, Pod UIDs/container and sandbox IDs, storage addresses, routes/sysctls, primary routes, IPAM allocations, volume/replica state and BackupTarget status. Confirm direct recovery access, healthy etcd, three Ready nodes, every in-use volume attached/healthy and every IM Ready with `lhnet1`.
2. Freshly inspect every Longhorn storage-network consumer and the old Whereabouts pool/reservations. **No old consumer may use 10.32.25.143:** it becomes broadcast inside a new /28. If one exists, stop and agree how to retire it before deploying the NAD. The saved .128 consumer is usable as a peer; it need not be removed solely for its address. New allocations are .129–.142. Keep the old pool while old consumers exist.
3. Select node order from current workloads. For each CNPG singleton on a candidate, obtain the explicit temporary-replica/switchover or accepted-downtime decision with a verified restorable backup. Resolve PDB blockers deliberately; do not force drains, disable eviction or silently change database topology. Save an etcd snapshot before the first drain. Confirm no overlapping NAS maintenance/backup work.
4. Merge the approved stack. Wait for `daemonset/cni-tuning` Ready on all three nodes, successful checksum-verified install logs, and the exact bridge → tuning NAD with its /28, four /32 routes and three sysctls. Stop on CNI setup errors. No direct host sysctl edit is part of this change.

### Prove each replacement has the filter

Record old/new Pod UIDs and container/sandbox IDs; a container restart in the same sandbox is insufficient. After each replacement, inspect the actual network namespace. Use `net1` for a manager and `lhnet1` for an IM, with the selected container name (normally `longhorn-manager` / `instance-manager`) and `peer_ip` set to a different current Longhorn storage address:

```bash
# Read-only, after recording the selected Pod UID and container/sandbox identity.
kubectl -n longhorn-system exec "$pod" -c "$container" -- sh -ceu '
  iface=$1
  ip -d link show dev "$iface"
  ip -4 address show dev "$iface"
  ip -6 address show dev "$iface"
  ip -4 rule show
  ip -4 route show table all
  ip -4 route get "$2"
  for destination in 10.32.25.5 10.32.25.11 10.32.25.12 10.32.25.13 10.32.25.161; do
    ip -4 route get "$destination"
  done
  ip -6 route show dev "$iface"
  cat /proc/sys/net/ipv4/conf/all/rp_filter
  cat "/proc/sys/net/ipv4/conf/$iface/rp_filter"
  cat "/proc/sys/net/ipv6/conf/$iface/disable_ipv6"
' sh "$iface" "$peer_ip"
```

Require an actual .129–.142 **/28** address, connected `10.32.25.128/28`, gatewayless storage routes to NAS .5 and nodes .11–.13, and the unchanged primary default route. Read the three sysctls as **0 / 1 / 1** and require **no inet6 address or IPv6 route on the storage interface**. Use local `ip -4 route get` lookups for a current peer, NAS .5 and nodes .11–.13 to confirm the storage interface; a tenant address such as .161 must resolve through the primary interface. Recheck the Pod UID and container/sandbox identity after inspection. If a pinned image lacks an inspection tool, stop for a read-only Talos netns alternative; do not install tools in production containers.

These checks prove activation; `verify-node.sh` alone only proves ordinary storage health. The static source filter does not authenticate allowed addresses, and privileged/shared-VLAN actors can still forge them.

### Roll managers, then one node at a time

1. Select the manager on the `default` BackupTarget's current `status.ownerID` node as the first canary, so its fresh target synchronization exercises the NAS path. Verify the owner before and after; if it moved, defer that functional proof until its new owner is replaced. Replace **one** selected manager using a UID-preconditioned Pod delete (the [diagnostic cleanup](longhorn-storage-boundary.md#cleanup-including-failed-attempts) shows the native DeleteOptions form). Wait for its replacement Ready, verify the filter above, and check fresh manager/controller health and the `default` BackupTarget. Stop on new errors/unavailability before replacing the other managers, individually with the same checks. A DaemonSet-wide restart is not a paused canary.
2. For the selected node, repeat candidate preflight and its database/PDB decision. Perform a bounded PDB-respecting `kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout=15m`; do not add `--force` or `--disable-eviction`. A failed drain ends the sequence for diagnosis. Observe the old IM Pod/sandbox gone and its allocations released; do not directly delete an IM carrying engines or replicas.
3. Uncordon that node. Wait for a new IM Pod/sandbox and verify its filter. Run `.agents/skills/talos-rollout/scripts/verify-node.sh "$node"` and Talos/etcd health. Require all in-use volumes attached/healthy, expected replicas restored and all IMs Ready. Record actual replica recovery and host iSCSI established connections to the current storage addresses through read-only Talos netstat. Ordinary peer/node/NAS routes must still work.
4. Only then preflight the next node; database placement may have changed. Old /24 and new /28 consumers can coexist for the checked address set without .143; overlap reservations prevent duplicate allocation but do not remove the broadcast constraint.

### Acceptance and rollback

After every manager and IM has passed, run the separately authorized [eight-check tenant probe in blocked mode](longhorn-storage-boundary.md#after-storage-filtering-is-activated), once, with freshly bound destinations. Require T1–T7 TCP timeouts and T8 curl timeout/HTTP 000, preserve the logs, and remove its Pod/policy/namespace and IPAM allocation. Pair these negative results with the exact filter state, healthy replicas, active node iSCSI connections, and **a completed normal Longhorn backup to the existing NAS NFS target** by creating one on-demand backup in the Longhorn UI for an existing small volume already covered by the normal backup policy. Record the volume/backup ID and completed result; use the existing default target and leave retention unchanged. The [backup runbook](longhorn-backup-restore.md) describes that target. A TCP port-2049 connection is not NFS backup proof. Do not alter retention or create a new backup destination.

Stop at a failed check; preserve the observed cause and choose recovery instead of continuing automatically. To roll back, revert the isolation layer and **wait for Flux to restore the original NAD** before recreating affected managers/IMs by the same safe sequence. Verify the original /24 routes, original sysctl values and storage-interface IPv6 against the captured baseline, plus full storage/backup health. Reverting Git does not change a running sandbox. Keep `tuning` installed until all chained sandboxes are gone; pruning its installer does not remove the host binary.

Close #1120 only after functional acceptance and cleanup, with the residual explicitly accepted: NAS/node/host-network identities remain trusted, shared-L2 source forgery remains possible, and host listeners/CNPG reach beyond this destination boundary remain under #226. Until then the production filter is unverified.

## Why bridge, not macvlan or ipvlan

The first three cutover attempts on prod tried macvlan, then ipvlan L2. Both got the network *between pods* working (cross-host replica traffic flowed fine) but blocked Longhorn's iSCSI flow at the host: `iscsiadm` runs in the host's network namespace and has to reach the same-node engine's iSCSI target on the secondary interface IP. macvlan and ipvlan L2 both isolate the host's parent-NIC stack from same-node Multus-attached pods at the kernel level — host-to-pod ARP doesn't loop back through the wire. Volumes hung mid-attach with `Host is unreachable`.

The Longhorn-Multus community fix is "host's IP must live on a macvlan sub-interface so host and pods share an L2 plane." On Talos the cleanest expression of that idea is a **Linux bridge**: `enp6s0` becomes a bridge slave, `br-storage` carries the host IP, pods get veth into the bridge via bridge CNI. Host and pods sit on one L2 broadcast domain, same-host iscsiadm just works, and bridge plugin is bundled in Talos's `/opt/cni/bin` (no extra installer needed).

## Preconditions

- [ ] Multus CNI running (`kubectl -n kube-system get ds kube-multus-ds` reports all nodes ready)
- [ ] Whereabouts running (`kubectl -n kube-system get ds whereabouts` reports all nodes ready)
- [ ] CRDs present: `network-attachment-definitions.k8s.cni.cncf.io`, `ippools.whereabouts.cni.cncf.io`, etc.
- [ ] **Per-node Talos config has `br-storage` with `enp6s0` as a bridge slave**, host's storage VLAN IP on the bridge. See `talos/patches/k8s-prod-{1,2,3}/network-extras.yaml`. Apply via `task talos:generate-config` + `task talos:apply-node IP=<node>` per node; expect a brief VLAN 25 connectivity blip.
- [ ] Verify on each node: `talosctl --nodes <ip> get addresses` shows `br-storage/10.32.25.X/24` and `enp6s0` has no IPv4.
- [ ] Verify cross-host AND same-host VLAN 25 reachability (`ping -c 2 <other-node-storage-ip>` from a `hostNetwork: true` debug pod on each node) — both must work before proceeding. If same-host fails the bridge is misconfigured.
- [ ] `kubectl -n longhorn-system get net-attach-def storage-network` exists with `type: bridge, bridge: br-storage`
- [ ] `kubectl -n longhorn-system get setting storage-network -o jsonpath='{.value}'` returns **empty** (cutover not yet applied)
- [ ] `helmrelease.yaml` change adding `storageNetwork: longhorn-system/storage-network` is **staged but not committed** (or in a separate commit not yet pushed)

## Workloads with Longhorn PVCs

All currently in `observability`:

| Workload | Kind | PVC | Owned by operator? |
|---|---|---|---|
| `vmalertmanager-victoria-metrics-k8s-stack` | StatefulSet | 1 Gi | yes — victoria-metrics-operator |
| `victoria-logs-single-server` | StatefulSet | 30 Gi | no |
| `vmsingle-victoria-metrics-k8s-stack` | Deployment | 50 Gi | yes — victoria-metrics-operator |
| `grafana` | Deployment | 10 Gi | no |

The "owned by operator" column matters for step 2: operator-managed workloads need their operator scaled down too, otherwise the operator reconciles the workload back to its declared replica count.

## Maintenance window

Estimated 30–60 min including verification. Outage of the observability stack is acceptable.

### 1. Suspend Flux for stateful HelmReleases

```sh
flux suspend hr -n observability \
  victoria-logs-single victoria-metrics-k8s-stack grafana
```

### 2. Scale operators to 0 first, then their workloads

```sh
kubectl -n observability scale deployment \
  victoria-metrics-k8s-stack-victoria-metrics-operator \
  --replicas=0

kubectl -n observability scale statefulset \
  vmalertmanager-victoria-metrics-k8s-stack \
  victoria-logs-single-server \
  --replicas=0

kubectl -n observability scale deployment \
  grafana \
  vmsingle-victoria-metrics-k8s-stack \
  --replicas=0
```

### 3. Wait for volumes to detach

```sh
kubectl -n longhorn-system get volumes.longhorn.io \
  -o custom-columns='NAME:.metadata.name,STATE:.status.state,ROBUST:.status.robustness'
```

All volumes should reach `STATE=detached`. Anything stuck attached needs investigation before proceeding.

### 4. Apply the storage-network setting

Push the commit that adds `storageNetwork: longhorn-system/storage-network` to `helmrelease.yaml`. Helm renders it into the `longhorn-default-setting` ConfigMap; Longhorn manager reconciles the ConfigMap onto the `setting/storage-network` CR within seconds. Force reconcile to skip Flux's polling delay:

```sh
flux reconcile source git flux-system
flux reconcile hr longhorn -n longhorn-system

kubectl -n longhorn-system get setting storage-network -o jsonpath='{.value}{"\n"}'
# Expect: longhorn-system/storage-network
```

### 5. Force IM pods to roll with the secondary interface

Setting the storage-network value triggers Longhorn's manager to recreate IM pods with the new attachment, but explicitly deleting them removes any ambiguity about old vs new pods:

```sh
kubectl -n longhorn-system delete pods -l longhorn.io/component=instance-manager
```

Watch them come back:

```sh
kubectl -n longhorn-system get pods -l longhorn.io/component=instance-manager -w
```

### 6. Verify each IM has the secondary interface

```sh
kubectl -n longhorn-system get pods -l longhorn.io/component=instance-manager \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n  "}{.metadata.annotations.k8s\.v1\.cni\.cncf\.io/network-status}{"\n\n"}{end}'
```

Each IM pod should show two interfaces: Cilium primary (`eth0` on `10.42.x.x`) and the bridge secondary (`lhnet1` on an address from the `.128/28` Whereabouts pool, typically `.128-.130` for three nodes).

Cross-check Whereabouts:

```sh
kubectl -n kube-system get ippool 10.32.25.0-24 -o yaml | grep -A 15 'allocations:'
```

Three allocations, one per node, all in `.128/28` (.128-.143).

### 7. Resume Flux and scale workloads back up

Helm doesn't reset replica counts after a `kubectl scale --replicas=0`, so scaling back is manual:

```sh
flux resume hr -n observability \
  victoria-logs-single victoria-metrics-k8s-stack grafana

kubectl -n observability scale deployment \
  victoria-metrics-k8s-stack-victoria-metrics-operator \
  grafana \
  vmsingle-victoria-metrics-k8s-stack \
  --replicas=1

kubectl -n observability scale statefulset \
  victoria-logs-single-server \
  --replicas=1
```

The VMAlertmanager StatefulSet is recreated by its operator from the CR once the operators are back; no manual scaling is needed for it.

### 8. Verify replica traffic on VLAN 25

From a privileged `hostNetwork: true` debug pod (e.g., `nicolaka/netshoot`) on any node:

```sh
tcpdump -i enp6s0 -n -c 20 'net 10.32.25.128/28'
```

Expect TCP traffic between the IM-pod IPs. **No** Longhorn replica traffic should appear on `eno1` going forward.

## Rollback

If the cutover goes sideways:

1. Suspend / scale down stateful workloads (steps 1–3 again, including operators)
2. Revert the `helmrelease.yaml` commit; push; force reconcile
3. **Manually clear the Setting CR.** Helm renders the value into the `longhorn-default-setting` ConfigMap, which Longhorn syncs onto the Setting CR — but Longhorn doesn't unset existing settings on its own when the value disappears from the ConfigMap. Force it:
   ```sh
   kubectl -n longhorn-system patch setting storage-network --type=merge -p '{"value":""}'
   ```
4. Delete IM pods so they restart on the primary network only
5. Resume HRs / scale workloads back up per step 7

The Multus, Whereabouts, NAD, and Talos bridge config can stay deployed — they're inert without the Longhorn `storage-network` setting referencing the NAD.

## PVC retention caveat

Every observability stateful workload defaults to `persistentVolumeClaimRetentionPolicy: Retain` and survives scale-to-0 with data intact. Before adding a workload to the scale-down list, check its StatefulSet: a chart that sets `whenScaled: Delete` combined with the `longhorn` StorageClass `reclaimPolicy: Delete` destroys the volume on scale-to-0 and returns an empty PVC on scale-up.

```sh
kubectl -n observability get statefulset -o custom-columns=\
'NAME:.metadata.name,WHEN_SCALED:.spec.persistentVolumeClaimRetentionPolicy.whenScaled'
```

## Lessons from the prod cutover (2026-05-02)

- **Pre-flight `/opt/cni/bin`** before assuming a CNI plugin is available. Talos bundles a curated subset (bridge, host-local, loopback, portmap, firewall, plus the chosen primary CNI's binary). macvlan, ipvlan, vlan, ptp, tuning, etc. are not shipped. We discovered this mid-cutover the first time.
- **macvlan and ipvlan L2 both isolate host-to-same-host-pod traffic** at the kernel level. Cross-host pings work; same-host pings ARP-fail. Bridge is the only bundled CNI that cleanly puts host and pods on one L2 plane without host-side workarounds.
- **operator-managed workloads need the operator scaled to 0 first.** Otherwise it reconciles workloads back faster than you can scale them down.
- **Helm doesn't reset replica counts after `kubectl scale --replicas=0`.** Resume + force-reconcile leaves replicas at 0; manual scale-up is required.
- **Longhorn's `defaultSettings` apply via a ConfigMap, not a one-time install.** When you remove a value from `defaultSettings` in the HelmRelease, the ConfigMap loses it, but Longhorn's manager doesn't unset existing Setting CRs — only sets new values. Rollback requires `kubectl patch` directly.
