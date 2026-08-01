# Talos Pod Security Admission rollout

Talos' default API server configuration enforces the Kubernetes `baseline`
Pod Security Standard and warns/audits at `restricted`. It exempts
`kube-system`. This repository additionally labels only the namespaces whose
workloads require baseline-prohibited host access:

| Namespace | Required exception |
| --- | --- |
| `longhorn-system` | Privileged CSI and storage-engine host access |
| `media` | qBittorrent's Gluetun sidecar uses `NET_ADMIN` and `/dev/net/tun` |
| `network-perf` | iperf3 uses the host network to measure the underlay |
| `observability` | node-exporter and Alloy use host namespaces and paths |
| `tailscale` | The operator-generated subnet router runs privileged containers |

All other namespaces inherit `baseline` enforcement and `restricted`
warn/audit from Talos. Existing non-compliant pods are not evicted; the policy
is evaluated when pods are created or updated.

## Prepare and inspect

Perform this rollout only after the PR is merged and Flux has reconciled the
namespace labels. Do not apply a generated configuration from an unmerged
branch.

```sh
git switch main
git pull --ff-only
flux reconcile kustomization flux-system --with-source
kubectl wait --for=condition=Ready kustomization --all --all-namespaces --timeout=10m

kubectl get namespace longhorn-system media network-perf observability tailscale \
  -L pod-security.kubernetes.io/enforce \
  -L pod-security.kubernetes.io/warn \
  -L pod-security.kubernetes.io/audit

task talos:generate-config
for config in talos/clusterconfig/kubernetes-k8s-prod-*.yaml; do
  echo "$config"
  yq 'select(.cluster.apiServer.admissionControl) |
    .cluster.apiServer.admissionControl' "$config"
done
```

Each generated file must contain the `PodSecurity` admission configuration
with these defaults before any node is changed:

- `enforce: baseline`
- `audit: restricted`
- `warn: restricted`
- `kube-system` in the namespace exemptions

Run an apply dry-run against every node and review the complete diff locally.
Talos machine configurations contain credentials, so do not paste or attach
the dry-run output to an issue or PR:

```sh
talosctl apply-config --nodes 10.32.30.11 --dry-run \
  --file talos/clusterconfig/kubernetes-k8s-prod-1.yaml
talosctl apply-config --nodes 10.32.30.12 --dry-run \
  --file talos/clusterconfig/kubernetes-k8s-prod-2.yaml
talosctl apply-config --nodes 10.32.30.13 --dry-run \
  --file talos/clusterconfig/kubernetes-k8s-prod-3.yaml
```

The approval must cover every change shown, not only Pod Security Admission.
At the time this runbook was prepared, the live configuration also lagged the
repository's Kubernetes patch version (`v1.36.2` live versus `v1.36.3`
generated), so an immediate apply would update the kubelet and control-plane
component images. Complete that pending upgrade first or explicitly include it
in the rollout approval. Stop if any other unexpected drift is present.

Confirm cluster health immediately before the first node:

```sh
talosctl health
kubectl get nodes
kubectl get pods --all-namespaces --field-selector=status.phase!=Running,status.phase!=Succeeded
```

## Roll out one node at a time

Get explicit approval before starting. Apply and validate one command at a
time; do not put these commands in a loop.

```sh
task talos:apply-node IP=10.32.30.11
```

Wait for `k8s-prod-1` and its API server to recover, then check the API server
on that node directly rather than relying on the control-plane VIP:

```sh
talosctl --nodes 10.32.30.11 services kube-apiserver
kubectl wait --for=condition=Ready node/k8s-prod-1 --timeout=10m
kubectl --server=https://10.32.30.11:6443 \
  --tls-server-name=k8s-prod.home.kelch.io get --raw='/readyz?verbose'
```

Verify that an unlabeled namespace rejects a baseline violation. This is a
server-side dry run and creates no pod:

```sh
kubectl --server=https://10.32.30.11:6443 \
  --tls-server-name=k8s-prod.home.kelch.io \
  --namespace=default run psa-baseline-rejection \
  --image=registry.k8s.io/pause:3.10.1 \
  --restart=Never --overrides='{"spec":{"hostNetwork":true}}' \
  --dry-run=server
```

Expected result: `Forbidden` with `violates PodSecurity "baseline:latest"`.
Also confirm a declared exception accepts the same dry-run request:

```sh
kubectl --server=https://10.32.30.11:6443 \
  --tls-server-name=k8s-prod.home.kelch.io \
  --namespace=media run psa-privileged-exemption \
  --image=registry.k8s.io/pause:3.10.1 \
  --restart=Never --overrides='{"spec":{"hostNetwork":true}}' \
  --dry-run=server -o name
```

Expected result: `pod/psa-privileged-exemption` with no object persisted.
Use the updated API server to audit regular namespaces against `restricted`.
These are server-side dry runs; warnings identify existing incompatibilities
without changing namespace labels:

```sh
for namespace in ai cert-manager cnpg-system default flux-system identity iot network; do
  kubectl --server=https://10.32.30.11:6443 \
    --tls-server-name=k8s-prod.home.kelch.io \
    label namespace "${namespace}" \
    pod-security.kubernetes.io/enforce=restricted \
    --overwrite --dry-run=server >/dev/null
done
```

Review every warning before proceeding. A restricted warning is an audit
finding, not a reason to weaken the cluster-wide `baseline` enforcement.
Check cluster-wide health again before proceeding:

```sh
talosctl health
kubectl get pods --all-namespaces --field-selector=status.phase!=Running,status.phase!=Succeeded
```

Repeat the same sequence for `10.32.30.12` / `k8s-prod-2`, changing the direct
API server address in both admission tests. Then repeat it for
`10.32.30.13` / `k8s-prod-3`.

After all three nodes are updated, inspect the live Talos admission resource
and repeat the rejection test through the VIP:

```sh
talosctl get admissioncontrolconfigs.kubernetes.talos.dev admission-control -o yaml
kubectl --namespace=default run psa-baseline-rejection \
  --image=registry.k8s.io/pause:3.10.1 \
  --restart=Never --overrides='{"spec":{"hostNetwork":true}}' \
  --dry-run=server
```

## Roll back

Stop if a node or workload does not recover. Do not continue to the next node.
Revert the PR so `cluster.apiServer.admissionControl` is deleted again and the
namespace-label changes are removed, regenerate the Talos configuration, and
apply that configuration only to nodes already changed, one at a time in
reverse order. Run the health checks after every node. Because Flux watches
`main`, land the Git revert before removing privileged namespace labels from
the live cluster.

The rollback is complete when the live Talos admission-control resource is
absent, all nodes are ready, and Flux reports every Kustomization ready.
