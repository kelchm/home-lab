# Longhorn Storage Network Cutover

Runbook for moving Longhorn replica engine ↔ replica engine traffic onto VLAN 25 (2.5GbE storage NIC) via Multus + bridge CNI. **Already executed for the prod cluster on 2026-05-02.** Kept around as the reference procedure for future similar work (sandbox cluster, plugin/version migrations, replacement nodes).

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
| `alertmanager-kube-prometheus-stack-alertmanager` | StatefulSet | 1 Gi | yes — kube-prometheus-stack-operator |
| `prometheus-kube-prometheus-stack-prometheus` | StatefulSet | 50 Gi | yes — kube-prometheus-stack-operator |
| `loki` | StatefulSet | 30 Gi | no — **but** chart sets `pvcRetentionPolicy: Delete` so scaling to 0 wipes data; see "Loki caveat" below |
| `victoria-logs-single-server` | StatefulSet | 30 Gi | no |
| `vmsingle-victoria-metrics-k8s-stack` | Deployment | 50 Gi | yes — victoria-metrics-operator |
| `openobserve` | StatefulSet | 50 Gi | no |
| `grafana` | Deployment | 10 Gi | no |

The "owned by operator" column matters for step 2: operator-managed workloads need their operator scaled down too, otherwise the operator reconciles the workload back to its declared replica count.

## Maintenance window

Estimated 30–60 min including verification. Outage of the observability stack is acceptable.

### 1. Suspend Flux for stateful HelmReleases

```sh
flux suspend hr -n observability \
  kube-prometheus-stack loki victoria-logs-single \
  victoria-metrics-k8s-stack openobserve grafana
```

### 2. Scale operators to 0 first, then their workloads

```sh
kubectl -n observability scale deployment \
  kube-prometheus-stack-operator \
  victoria-metrics-k8s-stack-victoria-metrics-operator \
  --replicas=0

kubectl -n observability scale statefulset \
  alertmanager-kube-prometheus-stack-alertmanager \
  prometheus-kube-prometheus-stack-prometheus \
  loki \
  victoria-logs-single-server \
  openobserve \
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

Each IM pod should show two interfaces: Cilium primary (`eth0` on `10.42.x.x`) and the bridge secondary (`lhnet1` on `10.32.25.11{1,2,3}`).

Cross-check Whereabouts:

```sh
kubectl -n kube-system get ippool 10.32.25.0-24 -o yaml | grep -A 15 'allocations:'
```

Three allocations, one per node, all in `.111-.119`.

### 7. Resume Flux and scale workloads back up

Helm doesn't reset replica counts after a `kubectl scale --replicas=0`, so scaling back is manual:

```sh
flux resume hr -n observability \
  kube-prometheus-stack loki victoria-logs-single \
  victoria-metrics-k8s-stack openobserve grafana

kubectl -n observability scale deployment \
  kube-prometheus-stack-operator \
  victoria-metrics-k8s-stack-victoria-metrics-operator \
  grafana \
  vmsingle-victoria-metrics-k8s-stack \
  --replicas=1

kubectl -n observability scale statefulset \
  loki \
  victoria-logs-single-server \
  openobserve \
  --replicas=1
```

The Prometheus and Alertmanager StatefulSets are recreated by the operator from their CRs once the operator is back; no manual scaling needed for those.

### 8. Verify replica traffic on VLAN 25

From a privileged `hostNetwork: true` debug pod (e.g., `nicolaka/netshoot`) on any node:

```sh
tcpdump -i enp6s0 -n -c 20 'host 10.32.25.111 or host 10.32.25.112 or host 10.32.25.113'
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

## Loki caveat

The Loki Helm chart sets `persistentVolumeClaimRetentionPolicy: whenScaled=Delete, whenDeleted=Delete` on its StatefulSet. Combined with our `longhorn` StorageClass `reclaimPolicy: Delete`, **scaling Loki to 0 destroys its data.** The volume gets deleted; on scale-up Loki gets a fresh empty PVC. Every other observability stateful workload defaults to `Retain` and survives scale-to-0 with data intact.

If Loki retention matters for the maintenance window, override before starting:

```yaml
# kubernetes/apps/observability/loki/app/helmrelease.yaml
spec:
  values:
    singleBinary:
      persistentVolumeClaimRetentionPolicy:
        whenScaled: Retain
        whenDeleted: Retain
```

For a homelab where Loki retention is short anyway, accepting the data loss is reasonable — Alloy keeps shipping current logs and the gap is small.

## Lessons from the prod cutover (2026-05-02)

- **Pre-flight `/opt/cni/bin`** before assuming a CNI plugin is available. Talos bundles a curated subset (bridge, host-local, loopback, portmap, firewall, plus the chosen primary CNI's binary). macvlan, ipvlan, vlan, ptp, tuning, etc. are not shipped. We discovered this mid-cutover the first time.
- **macvlan and ipvlan L2 both isolate host-to-same-host-pod traffic** at the kernel level. Cross-host pings work; same-host pings ARP-fail. Bridge is the only bundled CNI that cleanly puts host and pods on one L2 plane without host-side workarounds.
- **operator-managed workloads need the operator scaled to 0 first.** Otherwise it reconciles workloads back faster than you can scale them down.
- **Helm doesn't reset replica counts after `kubectl scale --replicas=0`.** Resume + force-reconcile leaves replicas at 0; manual scale-up is required.
- **Longhorn's `defaultSettings` apply via a ConfigMap, not a one-time install.** When you remove a value from `defaultSettings` in the HelmRelease, the ConfigMap loses it, but Longhorn's manager doesn't unset existing Setting CRs — only sets new values. Rollback requires `kubectl patch` directly.
