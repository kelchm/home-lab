# Longhorn Storage Network Cutover

Runbook for moving Longhorn replica engine ↔ replica engine traffic from VLAN 30 (1GbE) onto VLAN 25 (2.5GbE) via Multus + macvlan + Whereabouts. One-shot operation; not a recurring procedure.

## Why

Until this cutover, replica traffic rode the pod network on the 1GbE NIC despite VLAN 25 being designed and validated for it. See [`architecture.md`](architecture.md#storage-strategy) and the project memory note that `storage-benchmarks.md` (2026-04-25) was run pre-cutover.

## Preconditions

These must be in place before starting the maintenance window. They're independent infrastructure changes and can land in git well ahead of the cutover.

- [ ] `kubernetes/apps/kube-system/multus/` deployed; `kubectl -n kube-system get ds kube-multus-ds` reports all 3 desired/ready
- [ ] `kubernetes/apps/kube-system/whereabouts/` deployed; `kubectl -n kube-system get ds whereabouts` reports all 3 desired/ready
- [ ] `kubernetes/apps/kube-system/cni-plugins-installer/` deployed; `kubectl -n kube-system get ds cni-plugins-installer` reports all 3 desired/ready
- [ ] CRD `network-attachment-definitions.k8s.cni.cncf.io` exists (installed by Multus)
- [ ] CRDs `ippools.whereabouts.cni.cncf.io` etc. exist (installed by Whereabouts)
- [ ] **macvlan binary present on every node** (Talos doesn't ship it; the cni-plugins-installer DaemonSet handles it):
  ```sh
  for n in k8s-prod-1 k8s-prod-2 k8s-prod-3; do
    pod=$(kubectl -n kube-system get pods -l app=multus -o jsonpath="{.items[?(@.spec.nodeName=='${n}')].metadata.name}")
    echo "${n}:"
    kubectl -n kube-system exec "$pod" -c kube-multus -- /opt/cni/bin/macvlan --version 2>&1 | head -1
  done
  ```
  All three should report `CNI macvlan plugin v<version>`.
- [ ] `kubectl -n longhorn-system get net-attach-def storage-network` returns the macvlan NAD
- [ ] `kubectl -n longhorn-system get setting storage-network -o jsonpath='{.value}'` returns **empty** (cutover not yet applied)
- [ ] `helmrelease.yaml` change adding `storageNetwork: longhorn-system/storage-network` is **staged but not committed** (or staged in a separate commit not yet pushed)

## Workloads with Longhorn PVCs

All currently in `observability`:

| Workload | Kind | PVC | Owned by operator? |
|---|---|---|---|
| `alertmanager-kube-prometheus-stack-alertmanager` | StatefulSet | 1 Gi | yes — kube-prometheus-stack-operator |
| `prometheus-kube-prometheus-stack-prometheus` | StatefulSet | 50 Gi | yes — kube-prometheus-stack-operator |
| `loki` | StatefulSet | 30 Gi | no |
| `victoria-logs-single-server` | StatefulSet | 30 Gi | no |
| `vmsingle-victoria-metrics-k8s-stack` | Deployment | 50 Gi | yes — victoria-metrics-operator |
| `openobserve` | StatefulSet | 50 Gi | no |
| `grafana` | Deployment | 10 Gi | no |

The "owned by operator" column matters for step 2: scaling an operator-managed workload to 0 alone doesn't stick — the operator reconciles it back. Scale the operator down first.

There's also an orphan PVC `data-openobserve-openobserve-standalone-0` in observability with no owning workload (left from a chart rename). It's already detached and stays detached; no action needed.

## Maintenance window

Estimated 30–60 minutes including verification. No external-facing service depends on the observability stack, so a brief outage there is acceptable.

### 1. Suspend Flux for stateful workloads

Prevents Flux from racing against the manual scale-down.

```sh
flux suspend hr -n observability \
  kube-prometheus-stack loki victoria-logs-single \
  victoria-metrics-k8s-stack openobserve grafana
```

### 2. Scale operators to 0 first, then their workloads

The Prometheus and VictoriaMetrics operators reconcile their managed workloads (Prometheus, Alertmanager, VMSingle) back to non-zero replicas if you scale just the underlying STS/Deploy. Scale the operators first so the subsequent scale-to-0 sticks.

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

All volumes should reach `STATE=detached`. Anything stuck attached needs investigation before proceeding (likely a workload still holding a mount).

### 4. Apply the storage-network setting

Push the commit that adds `storageNetwork: longhorn-system/storage-network` to `helmrelease.yaml`. Then force reconcile:

```sh
flux reconcile source git flux-system
flux reconcile hr longhorn -n longhorn-system
```

### 5. Wait for instance managers to restart with secondary interface

Longhorn manager rolls instance-manager pods one at a time. Watch:

```sh
kubectl -n longhorn-system get pods -l longhorn.io/component=instance-manager -w
```

When stable, verify each IM pod has a secondary interface with an IP in the storage-pod range:

```sh
kubectl -n longhorn-system get pods -l longhorn.io/component=instance-manager \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n  "}{.metadata.annotations.k8s\.v1\.cni\.cncf\.io/network-status}{"\n"}{end}'
```

Expect two interfaces per pod: the Cilium primary (`10.42.x.x`) and a `storage-network` secondary (`10.32.25.11{1,2,3}`).

### 6. Cross-check with Whereabouts pool

```sh
kubectl get ippools.whereabouts.cni.cncf.io -A
kubectl -n longhorn-system get ippool 10.32.25.0-24 -o yaml | grep -A 20 'allocations:'
```

Three allocations expected, one per node, all in `.111-.119`.

### 7. Resume Flux and scale workloads back up

Resuming the HRs is necessary but **not sufficient** — Helm doesn't reset replica counts after a `kubectl scale --replicas=0` (it tracks template-level fields, not live replica counts). Manually scale everything back up after resuming.

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

The Prometheus and Alertmanager StatefulSets are recreated by the operator from their CRs once the operator is back up — no manual scaling needed for those.

### 8. Verify replica traffic on VLAN 25

Pick a node, exec into a privileged debug pod or use `talosctl` to capture on `enp6s0` while a workload is writing:

```sh
# From a debug shell on a node
tcpdump -i enp6s0 -n 'host 10.32.25.111 or host 10.32.25.112 or host 10.32.25.113' -c 50
```

Expect traffic between IM pod IPs on the storage-pod range. **No** Longhorn replica traffic should appear on `eno1` going forward.

## Rollback

If something goes wrong and we need to back out the storage-network change:

1. Suspend / scale down stateful workloads (steps 1–3 again, including operators per step 2's note)
2. Revert the `helmrelease.yaml` commit; push; force reconcile
3. **Manually clear the setting CR** — Longhorn's `defaultSettings` only applies on initial install, so removing the value from helm values does *not* unset it on the existing Settings CR. Force it:
   ```sh
   kubectl -n longhorn-system patch setting storage-network --type=merge -p '{"value":""}'
   ```
4. IM pods restart on the primary network only
5. Resume / scale back up per step 7

The Multus, Whereabouts, NAD, and cni-plugins-installer manifests can stay deployed — they're inert without Longhorn referencing the NAD.

## Follow-ups

- [ ] Re-bench Longhorn (`tools/longhorn-bench/`) and amend `storage-benchmarks.md` with new numbers; revisit the "engine-latency-bound" claim with replica traffic on 2.5GbE
- [ ] Backup MVP (Longhorn → NFS on Synology) — the original goal that uncovered this misconfiguration
