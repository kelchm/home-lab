# Longhorn storage-boundary check (#226)

**Baseline and post-filter checks executed on 2026-10-29:** the baseline confirmed tenant access to Longhorn listeners over IPv4 and IPv6. After [storage-interface isolation](longhorn-storage-network-cutover.md#storage-interface-isolation-1120), the 23:16:27–23:16:59 UTC batch completed all eight attempts once in blocked mode: T1–T7 timed out and T8 returned curl 28/HTTP 000. Target identities were stable, both runs' diagnostic resources and IPAM allocations were removed, and final storage health passed. This establishes the tested network boundary, not authentication or volume access. Further runs need separate approval. This replaces the oversized local probe controller for [#226](https://github.com/kelchm/home-lab/issues/226). The historical package and its unapproved `259d9877…b3a27` seal remain untouched. Review refreshed inputs before any further run; a merged PR grants no execution authority.

The three [manual diagnostic manifests](../../tools/longhorn-boundary/) create one new namespace, a primary-veth default-deny policy, and one disposable Pod on `k8s-prod-3`. They follow the existing `tools/iperf3` convention, outside Flux's `./kubernetes/apps` path; no reconciled kustomization references them. Merging cannot create these resources. Do not move them under `kubernetes/apps` or run a directory-wide apply.

The Pod attaches `cnpg-system/storage-network`: a database-tenant source class, using one Whereabouts address from `.160–.175`, rather than a Longhorn or host source. It retains UID/GID 65534, no host namespaces/token/volumes, ALL capabilities dropped, no privilege escalation, read-only root and RuntimeDefault. Requests remain 10m/16Mi and limits 100m/64Mi. The script runs once; its seven 3s TCP connect limits and 5s HTTP limit allow about 26s of network waiting. The Pod's 300s deadline, including image pull/startup, and 5s termination grace bound a stalled run; there is no separate whole-script timeout. No exec, tool installation or script download. The primary NetworkPolicy does **not** isolate `net1`; verify current Cilium policy context before use. It is not evidence of a storage boundary.

## Exact scope

These numeric addresses are **historical**, from the reviewed 2026-10-29T03:03Z snapshot. Fresh reads must identify the same roles, node placement and listeners immediately before any future execution. Refresh changed literals in `pod.yaml`, including route checks, and review that diff before root decides. Do not discover destinations at runtime.

| ID | Destination role / path | Historical destination | Single operation / expected result |
|---|---|---|---|
| T1 | IM on node 3 / same node | `10.32.25.131:8504` | TCP connect, refusal delivery control |
| T2 | IM on node 3 / same node | `10.32.25.131:8500` | TCP connect/close, zero payload, connected |
| T3 | IM on node 1 / cross node | `10.32.25.129:8504` | TCP connect, refusal delivery control |
| T4 | IM on node 1 / cross node | `10.32.25.129:8500` | TCP connect/close, zero payload, connected |
| T5 | IM on node 3 / same node | `[fe80::c0aa:c7ff:fe47:6cc9%net1]:8500` | TCP connect/close, zero payload, connected |
| T6 | IM on node 1 / cross node | `[fe80::b43e:9fff:fe07:b6e%net1]:8500` | TCP connect/close, zero payload, connected |
| T7 | manager on node 3 / same node | `10.32.25.130:9500` | TCP connect, refusal bind control |
| T8 | manager on node 3 / same node | `10.32.25.130:9503` | HTTP `GET /`, discard body, record status, expect 404 |

There are at most eight sequential application attempts: TCP connect timeout 3s, HTTP total 5s, no retries, DNS, proxy or redirect following. Normal kernel SYN retransmission is not an application retry. The shell checks `net1`'s actual IPv4 pool/prefix, usable IPv6 link-local `/64`, connected routes, and each destination route before sending anything; it binds the validated sources and scopes IPv6 to `net1`. It does not require an exact whole-Pod interface list. Extra harmless interfaces do not invalidate the run. Any unexpected result stops the sequence: distinguish refusal from timeout, record the result, clean up, and return for review. Never escalate to another port or rerun a failed tuple.

Excluded: gRPC health/reflection or other RPCs, iSCSI 3260, profiling 6060, engine/replica/data requests, other HTTP paths/methods, database traffic, host-source tests and infrastructure changes. Report only tested tuples and this source class; neither success nor failure qualifies every other listener, authentication or volume access.

## After storage filtering is activated

For the post-change check in [the isolation rollout](longhorn-storage-network-cutover.md#storage-interface-isolation-1120), use the same eight attempts and cleanup procedure with `EXPECT_STORAGE_ACCESS=blocked` in a private copy of `pod.yaml`. Refresh its literal addresses and route checks from current Pod/sandbox identities before approval. The default `baseline` mode keeps the table above; `blocked` requires **T1–T7: nc exit 1 with a timed-out diagnostic**, and **T8: curl exit 28 with HTTP 000**. Blocked mode pauses one second after each TCP attempt (seven seconds total) so an unresolved ARP lookup finishes before the next check reuses that destination; baseline mode adds no pause. A refusal, successful connection, HTTP response or local routing/tool error is not a pass. An unexpected result ends this single batch; retain it and diagnose without automatically running another Pod.

The tenant still has IPv6. Protected instance-managers must have `disable_ipv6=1` and no `lhnet1` IPv6 address; derive each would-be link-local EUI-64 from its current storage-interface MAC and record it as a calculated negative-test destination, not an assigned live address. Pair the timeouts with the rollout's direct filter-state checks and positive replication/iSCSI/backup evidence. Timeouts alone cannot distinguish filtering from an unavailable destination.

## Before a separately authorized run

Use a fresh **bash** shell from the repository root (the blocks also use ordinary zsh-compatible commands, not fish). Have the repository's kubectl, jq and talosctl available. Create a local evidence directory and save its path before any cluster mutation:

```bash
bash --noprofile --norc
set -euo pipefail
context=$(kubectl config current-context)  # Verify this is k8s-prod, not merely the current default.
tool="$PWD/tools/longhorn-boundary"
ns=netprobe-226
pod=storage-boundary
run=$(mktemp -d "${TMPDIR:-/tmp}/longhorn-boundary-226.XXXXXX")
k() { kubectl --context "$context" --request-timeout=15s "$@"; }
printf 'context=%s namespace=%s pod=%s evidence=%s\n' "$context" "$ns" "$pod" "$run" | tee "$run/recovery.txt"

health() {
  k get nodes -o wide
  k -n longhorn-system get volumes.longhorn.io \
    -o custom-columns='NAME:.metadata.name,STATE:.status.state,ROBUSTNESS:.status.robustness,NODE:.status.currentNodeID'
  k -n longhorn-system get pods -l longhorn.io/component=instance-manager -o json |
    jq '.items[] | {name:.metadata.name, node:.spec.nodeName, phase:.status.phase,
      ready:[.status.containerStatuses[]?.ready],
      networks:.metadata.annotations["k8s.v1.cni.cncf.io/network-status"]}'
}
health | tee "$run/before.txt"
k get namespace "$ns" --ignore-not-found  # Must print no resource; never adopt an existing namespace.
k -n cnpg-system get network-attachment-definition storage-network -o yaml
k -n kube-system get ippools.whereabouts.cni.cncf.io,overlappingrangeipreservations.whereabouts.cni.cncf.io -o yaml > "$run/ipam-before.yaml"
k -n kube-system get daemonset kube-multus-ds -o yaml > "$run/multus.yaml"
k -n kube-system get configmap cilium-config -o yaml > "$run/cilium.yaml"
k get networkpolicies,cnp,ccnp -A > "$run/policies.txt"
k -n longhorn-system get pods -o json | jq '[.items[] |
  select(.metadata.name | test("^(instance-manager-|longhorn-manager-)")) |
  {name:.metadata.name, uid:.metadata.uid, node:.spec.nodeName, primary:.status.podIP,
    networks:.metadata.annotations["k8s.v1.cni.cncf.io/network-status"],
    containers:.status.containerStatuses}]' > "$run/destinations-before.json"
```

Require all three nodes Ready, every in-use Longhorn volume attached/healthy and none degraded/faulted, and all three IMs Running/Ready with `longhorn-system/storage-network` on `lhnet1`. Check that the tenant NAD is still the bridge-backed `.160–.175` pool with free addresses, Multus's effective node-3 configuration still permits cross-namespace attachment, and no applicable policy/injector changes the source. If the DaemonSet flags refer to a config file, read that effective file through Talos; the DaemonSet alone is not proof. Stop on an unhealthy prerequisite, namespace collision or unexplained change; do not repair it as part of this check.

Select the current node-3 IM, node-1 IM and node-3 manager from the saved read. Read their listeners through Talos, without production Pod exec. For example, after setting `im3`, `im1`, `manager3` to those current names:

```bash
talosctl -n 10.32.30.13 netstat --listening --programs --tcp "longhorn-system/$im3" > "$run/im3-sockets.txt"
talosctl -n 10.32.30.11 netstat --listening --programs --tcp "longhorn-system/$im1" > "$run/im1-sockets.txt"
talosctl -n 10.32.30.13 netstat --listening --programs --tcp "longhorn-system/$manager3" > "$run/manager3-sockets.txt"
```

Require IM 8500 listening and 8504 absent on both nodes; manager 9500 primary-IPv4-only and 9503 listening. For each socket-owner PID, read `/proc/<PID>/cgroup` and `/proc/<PID>/net/if_inet6` with `talosctl -n <node-ip> read <path>`; match Pod UID/container ID against the current Pod status and `talosctl -n <node-ip> containers --namespace cri`. Re-read the cgroup after the address read to detect PID replacement. Record the `lhnet1` IM link-locals and manager `net1` IPv4, and the selected images/imageIDs. Do not substitute a host netns read or stale Pod name. These reads may expose configuration/metadata but need no application credentials.

The stock pin is `docker.io/nicolaka/netshoot:v0.14@sha256:7f08c4aff13ff61a35d30e30c5c1ea8396eac6ab4ce19fd02d5a4b3b5d0d09a2`; linux/amd64 manifest `sha256:34eeca872db74067b1ed7fdc6201f278578bf57df7bd3081e99b5097a28464b5`. Anonymous registry index/manifest/config reads on 2026-10-29 verified both digests; no layers or workload were run. The old package's Python pin is inapplicable. Re-read this exact digest's metadata/availability before execution (for example `crane manifest <image@digest>`); do not replace it with the current tag. Its [Dockerfile](https://github.com/nicolaka/netshoot/blob/v0.14/Dockerfile) supplies bash, jq, iproute2, curl and [OpenBSD nc](https://man.openbsd.org/nc). The roughly 198 MiB pull consumes part of the Pod deadline. The baseline image/Pod completed successfully once; resource peaks and future admission/CNI startup are not guaranteed by that run.

Immediately before creation, repeat the destination identity/address/listener/image and health reads. Root's decision must cover the reviewed source, current exact destinations, one namespace/policy/Pod lifecycle and cleanup. If refreshed evidence changes that decision's inputs, return for review. API checks and creation are not atomic; zero-payload TCP cannot attest a remote Pod UID. No controller or extra packet test closes that race.

## Create, let startup run, retain logs

Only after that decision, use this **single chained create sequence**. UID recovery records come directly from successful create responses. No `apply`, replacement or retries. If any prerequisite fails, later commands cannot create the Pod:

```bash
k create -f "$tool/namespace.yaml" -o json > "$run/namespace.json" &&
  jq -er '"Namespace \(.metadata.name) UID=\(.metadata.uid)"' "$run/namespace.json" | tee -a "$run/recovery.txt" &&
k create -f "$tool/primary-deny.yaml" -o json > "$run/policy.json" &&
  jq -er '"NetworkPolicy \(.metadata.namespace)/\(.metadata.name) UID=\(.metadata.uid)"' "$run/policy.json" | tee -a "$run/recovery.txt" &&
k create -f "$tool/pod.yaml" -o json > "$run/pod.json" &&
  jq -er '"Pod \(.metadata.namespace)/\(.metadata.name) UID=\(.metadata.uid)"' "$run/pod.json" | tee -a "$run/recovery.txt"
```

On any failure, preserve the directory and clean up acknowledged creates below. A lost/ambiguous create response is not an ownership receipt: stop, inspect read-only and let root reconcile it; never adopt or delete an object solely by name. Cilium policy realization is asynchronous; creating the policy first does not atomically attest enforcement. The program uses only `net1` destinations and contains no primary-network test.

Wait for container termination, including script failure. If startup never happens, the bounded wait may expire; record Pod status/events and proceed to cleanup. Do not create a second Pod. The single container's termination record is the completion signal; the Pod phase can briefly remain Running afterwards. Verify the UID and termination record before reading logs:

```bash
k -n "$ns" wait "pod/$pod" --for=jsonpath='{.status.containerStatuses[0].state.terminated.finishedAt}' --timeout=330s --request-timeout=350s || true
k -n "$ns" get pod "$pod" -o json > "$run/pod-final.json"
k -n "$ns" get events > "$run/events.txt"
jq -e --slurpfile created "$run/pod.json" \
  '.metadata.uid == $created[0].metadata.uid and .status.containerStatuses[0].state.terminated.finishedAt != null' "$run/pod-final.json"
if [[ ! -s "$run/successful-log-path.txt" ]]; then
  for read_number in 1 2 3; do
    log_file=$(mktemp "$run/pod-logs.XXXXXX")
    k -n "$ns" get pod "$pod" -o json > "$log_file.before.json"
    jq -e --slurpfile created "$run/pod.json" \
      '.metadata.uid == $created[0].metadata.uid' "$log_file.before.json" >/dev/null
    if k -n "$ns" logs "$pod" -c probe > "$log_file" 2> "$log_file.stderr"; then
      read_ok=true
    else
      read_ok=false
    fi
    k -n "$ns" get pod "$pod" -o json > "$log_file.after.json"
    jq -e --slurpfile created "$run/pod.json" \
      '.metadata.uid == $created[0].metadata.uid' "$log_file.after.json" >/dev/null
    if [[ $read_ok == true ]]; then
      printf '%s\n' "$log_file" > "$run/successful-log-path.txt"
      break
    fi
    printf 'Log read %s failed; preserve %s\n' "$read_number" "$log_file" >&2
  done
fi
if [[ ! -s "$run/successful-log-path.txt" ]]; then
  printf 'STOP before Pod deletion: logs unavailable; root must decide whether to discard results\n' >&2
  exit 1
fi
```

Allow up to three read-only log fetches, each into a new file, with the Pod UID checked before and after. These reads send no probe traffic. Stop after the first successful capture and preserve it; cleanup never fetches logs or overwrites it. If all three reads fail, leave the terminated Pod in place and return for a deliberate decision before deleting it without results. If the container never started, record that fact and make the same explicit cleanup decision. Preserve partial/failed captures too. Report each attempted tuple's source, destination, exit/result, and HTTP status for T8; mark unattempted tuples explicitly. Unexpected controls end this batch. Timeouts do not prove delivery; in blocked mode they must be paired with the activation and positive-flow checks above. IM debug logs may record handshake EOFs; no gRPC bytes are sent. Re-read destination identities after the batch and qualify results if they changed.

## Cleanup, including failed attempts

Keep the evidence directory. Before Pod deletion, require the saved successful log path or root's explicit decision to discard unavailable results; do not turn a transient log-read error into automatic cleanup. If a failed command closed the shell, reopen bash, restore `context`, `ns`, `pod`, `run` from the printed recovery record and redefine `k`/`health`; do not repeat creates. Use only resources with valid create responses from this run; skip steps for objects never created. Before each operation compare current UID with that receipt; a mismatch/conflict or ambiguous API response stops automatic cleanup for root to reconcile. If an acknowledged deletion is already terminating, only wait; if confirmed absent, continue. The [raw DELETE](https://github.com/kubernetes/kubectl/blob/v0.37.1/pkg/cmd/delete/delete.go) below sends native `DeleteOptions` UID preconditions, protecting replacements. Never use name-only namespace deletion, force/finalizer overrides, or direct PV/Longhorn/IPAM deletion.

```bash
jq -e '{apiVersion:"v1",kind:"DeleteOptions",preconditions:{uid:(.metadata.uid | select(length > 0))}}' \
  "$run/pod.json" > "$run/delete-pod.json" &&
k delete --raw "/api/v1/namespaces/$ns/pods/$pod" -f "$run/delete-pod.json" > "$run/pod-delete-response.json" &&
k -n "$ns" wait --for=delete "pod/$pod" --timeout=120s --request-timeout=130s
k -n "$ns" get pods  # Must have no consumers, including replacements/foreign Pods.
k -n kube-system get ippools.whereabouts.cni.cncf.io -o json > "$run/pools-after.json"
k -n kube-system get overlappingrangeipreservations.whereabouts.cni.cncf.io -o json > "$run/reservations-after.json"
jq -e --arg prefix "$ns/" '[.items[].spec.allocations // {} | .[] |
  select((.podref // "") | startswith($prefix))] | length == 0' "$run/pools-after.json"
jq -e --arg prefix "$ns/" '[.items[] |
  select((.spec.podref // "") | startswith($prefix))] | length == 0' "$run/reservations-after.json"
```

**Stop here until both IPAM checks pass and there are no consumers.** Re-read until released within a bounded operator wait (up to 120s), saving fresh snapshots; if not released, leave the policy/namespace and record the blocker. Confirm the assigned address from `pod-final.json`'s network-status/logs is free or reused by a different, identified Pod, not still reserved to this run. Release is about this consumer, not permanent vacancy of a shared slot. If no Pod was acknowledged, still verify no consumer/allocation exists before removing the namespace.

Then remove the acknowledged policy, inspect namespace inventory, and remove the acknowledged namespace:

```bash
jq -e '{apiVersion:"v1",kind:"DeleteOptions",preconditions:{uid:(.metadata.uid | select(length > 0))}}' \
  "$run/policy.json" > "$run/delete-policy.json" &&
k delete --raw "/apis/networking.k8s.io/v1/namespaces/$ns/networkpolicies/primary-deny" \
  -f "$run/delete-policy.json" > "$run/policy-delete-response.json"
k api-resources --namespaced --verbs=list -o name > "$run/namespaced-resources.txt"
while IFS= read -r resource; do
  k -n "$ns" get "$resource" --ignore-not-found
done < "$run/namespaced-resources.txt" > "$run/namespace-inventory.txt"
cat "$run/namespace-inventory.txt"
```

Expect only the default ServiceAccount, `kube-root-ca.crt` ConfigMap and this run's Events. Stop if inventory fails or any foreign resource/consumer appears; inspect Secret names/counts only, never credential contents. Namespace inventory and DELETE race with concurrent creators: coordinate exclusive use of this dedicated namespace. UID preconditions protect namespace identity, not its changing contents.

```bash
jq -e '{apiVersion:"v1",kind:"DeleteOptions",preconditions:{uid:(.metadata.uid | select(length > 0))}}' \
  "$run/namespace.json" > "$run/delete-namespace.json" &&
k delete --raw "/api/v1/namespaces/$ns" -f "$run/delete-namespace.json" > "$run/namespace-delete-response.json" &&
k wait --for=delete "namespace/$ns" --timeout=120s --request-timeout=130s
health | tee "$run/after.txt"
```

Compare the same small health read with `before.txt`; record any new node, volume or IM fault and stop further tests. Cleanup is still required after a failed script/health check; no repair or whole-drill acceptance framework is part of this procedure. Keep exact results, cleanup/release evidence, recovery identifiers and verification limits in #226 after execution. No storage isolation change is authorized here.
