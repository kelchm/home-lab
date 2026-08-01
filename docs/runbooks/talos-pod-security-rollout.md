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

The five namespace exceptions relax only enforcement. They, and every other
namespace, retain Talos' cluster-wide `restricted` warn/audit settings so the
exceptions do not suppress security telemetry. All other namespaces also
inherit `baseline` enforcement. Existing non-compliant pods are not evicted;
the policy is evaluated when pods are created or updated.

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

The five namespaces must show `privileged` only in the `ENFORCE` column. Their
`WARN` and `AUDIT` columns must be blank so the Talos `restricted` defaults
continue to apply.

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

Expected result: `pod/psa-privileged-exemption` with no object persisted, plus
a warning that it violates `restricted:latest`. The warning confirms that the
namespace exception relaxes enforcement without suppressing telemetry.

Before changing a second node, use the updated API server to validate the
final, live workload definitions. This checks existing Pods by dry-running a
`baseline` namespace label and submits every stored Deployment, StatefulSet,
DaemonSet, Job, CronJob, and ReplicationController Pod template as a
server-side dry-run. It therefore covers Helm-rendered and operator-generated
objects currently stored in the API, without creating or changing anything:

```sh
KUBE_API_SERVER=https://10.32.30.11:6443 \
KUBE_TLS_SERVER_NAME=k8s-prod.home.kelch.io \
  scripts/verify-pod-security-baseline.sh
```

Any rejection is a rollout blocker. Review restricted-policy warnings as
telemetry, not as baseline failures. This validation must run against the
first updated API server because the live cluster does not evaluate Pod
Security Admission before that point.

At the time this runbook was prepared, the existing-Pod audit reported four
completed, unowned `node-debugger-*` Pods in `default` with host namespace and
hostPath access. Confirm they are no longer needed, remove those stale Pods,
and rerun the audit before rollout; do not exempt the `default` namespace.

Next, use the updated API server to audit regular namespaces against
`restricted`. These are server-side dry runs; warnings identify existing
incompatibilities without changing namespace labels:

```sh
for namespace in $(kubectl --server=https://10.32.30.11:6443 \
  --tls-server-name=k8s-prod.home.kelch.io get namespaces -o json | \
  jq -r '.items[] | select((.metadata.labels["pod-security.kubernetes.io/enforce"] // "") != "privileged") | .metadata.name'); do
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
The rollback has two deliberately ordered phases:

1. Prepare a rollback Talos configuration that restores the
   `cluster.apiServer.admissionControl: {$$patch: delete}` override. Keep all
   five namespace `enforce: privileged` labels in Git and on the live cluster.
2. Regenerate and validate the Talos configuration, then apply it only to nodes
   already changed, one at a time in reverse order. Run the direct API-server,
   node readiness, workload, and `talosctl health` checks after every node.
3. Verify the admission-control resource has an empty configuration on every
   rolled-back node; do not rely only on the control-plane VIP:

   ```sh
   for node in 10.32.30.13 10.32.30.12 10.32.30.11; do
     talosctl --nodes "${node}" get \
       admissioncontrolconfigs.kubernetes.talos.dev admission-control \
       -o json | jq -e '.spec.config == []'
   done
   ```

   Each node that was rolled back must pass the `jq` assertion. The restored
   `$$patch: delete` state retains the Talos resource with `spec.config: []`;
   it does not make the resource return `NotFound`. Skip nodes that were never
   changed.
4. Only after no API server enforces the restored policy, remove the five
   namespace labels in a second Git change (or complete the original PR
   revert) and let Flux reconcile them.

Because Flux watches `main`, never merge or reconcile namespace-label removal
while even one API server still enforces `baseline`. The rollback is complete
when the admission-control resource has `spec.config: []` on every changed
node, all nodes are ready, and Flux reports every Kustomization ready.
