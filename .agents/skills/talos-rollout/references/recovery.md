# Talos rollout recovery

## Reading the upgrade tracker

`talosctl upgrade` streams task events while it stops the node. **Lines containing `error` routinely describe shutdown tasks that completed.** Services being torn down report their teardown as an error condition, and the tracker prints it verbatim. The word `error` in that stream is not by itself evidence that the upgrade failed, and treating it as one leads to power-cycling a node that was shutting down normally.

The inverse is also true: the command exiting 0 does not mean the node came back. It means the upgrade was accepted and the node began the sequence.

Neither the tracker output nor the exit code is a health signal. `verify-node.sh` is.

## Reboot stops after shutdown

`k8s-prod-1` reproduced this twice with Talos's default reboot mode. The console showed a clean shutdown through workload stop, filesystem unmount, bootloader selection, kexec preparation, and service termination, then video went black without completing the restart. This points at the kexec handoff, not an etcd, kubelet, or Longhorn shutdown failure.

The permanent workaround is executable: `.taskfiles/talos/Taskfile.yaml` passes `--reboot-mode=powercycle`, which bypasses kexec.

### Gather independent evidence before touching power

A dead display is not a dead node, and a stalled `talosctl` session is not a stalled node. Removing power from a node that is mid-write is how a clean rollout becomes a Longhorn recovery. Collect all four before intervening:

1. **Ports.** `nc -z -w 3 <ip> 50000` for the Talos API and `nc -z -w 3 <ip> 6443` for the Kubernetes API. A refused connection and a timeout mean different things — a refusal means something is answering on that host.
2. **Talos API.** `talosctl -n <ip> version` and `talosctl -n <ip> service`. If these answer, the node is running and this is not a reboot failure.
3. **Kubernetes.** `kubectl get node <name>` and `kubectl -n kube-system get pods --field-selector spec.nodeName=<name>`, run against a *surviving* control-plane endpoint so you are not asking the suspect node about itself.
4. **KVM console.** Capture the final frame and the exact timestamps.

If Talos is still reachable, capture `dmesg`, `controller`, and `kubelet` logs before power is removed — after a power cycle they are gone.

Only when ports are unreachable, the Talos API does not answer, Kubernetes sees the node gone, and the console is stopped should you use the KVM or physical power control. Do not retry the default reboot path. Afterwards run `verify-node.sh` and complete every cluster and Longhorn check before touching another node.

## Drain is blocked

Talos cordons the node before draining it. If the drain fails, the node stays cordoned even though no reboot occurred — the cluster is now running with one node unschedulable and no upgrade to show for it.

1. Inspect `.spec.unschedulable` and what is still on the node. Identify whether the blocker is an operator-managed singleton, a database primary, or an access-path pod.
2. Do not turn a PodDisruptionBudget failure into a forced drain. `--force` and `--disable-eviction` bypass Longhorn's drain policy for every workload on the node, not only the one blocking you.
3. To continue, resolve the specific blocker deliberately — see [cnpg-failover.md](cnpg-failover.md) for a database primary — and retry the guarded single-node task.

### Clean-retry path after abandoning an attempt

A failed drain leaves state behind. Retrying on top of it stacks a second attempt onto an already-degraded cluster. Reset first:

1. Confirm the node and its workloads are safe to resume scheduling.
2. `kubectl uncordon <node-name>`, deliberately and explicitly. `preflight.sh` fails on any cordoned node precisely so a forgotten cordon cannot be carried into the next attempt.
3. Let the workloads that were evicted reschedule, and let Longhorn return to fully healthy. Rebuilds triggered by the partial drain are still in flight.
4. Re-run the **full** preflight, not a spot check: `preflight.sh <candidate>` plus `talosctl health`. Then make a fresh go/no-go decision.

## Tailscale route moves

The destination address does not tell you which link you are using. `10.32.30.x` is a LAN range, but reaching it from the workstation may still traverse the tailnet — which means it depends on a pod running inside the cluster you are about to reboot. `preflight.sh` resolves this by asking the host routing table which interface actually carries the Kubernetes API endpoint and each Talos node address, and reports whether that is a direct link or a tunnel.

Before rolling a candidate, know two things: whether your path runs over the tailnet, and whether the candidate hosts the subnet router or the operator. Preflight prints both and flags the overlap.

When the candidate hosts the router on a path you depend on, pick one deliberately:

- **Pre-move it.** Evict or reschedule the subnet-router pod onto a surviving node first, confirm the Connector is Ready there and your route still resolves, then start the rollout with a stable path.
- **Expect the reset.** Accept that draining the node will reset your API connection while the pod reschedules, and know in advance that this is the expected transient rather than a failure.

Either way, when the connection drops: reconnect, confirm the router is Ready on a surviving node and the route resolves again, then inspect cluster state. Do not retry anything until a clean health check passes — a reconnect that lands mid-drain tells you nothing about whether the drain succeeded. A loss of access that does not recover is a stop condition, not an expected transient.

## Instance-manager is missing `lhnet1`

During the v1.13.8 rollout, an instance-manager could start before Multus had republished `/etc/cni/net.d/00-multus.conf`. Kubernetes then reported the node Ready while the pod had only Cilium networking. This occurred after two node reboots and left Longhorn without its storage-VLAN path.

Detection requires all of the following in the instance-manager's state and `k8s.v1.cni.cncf.io/network-status` annotation:

- pod phase `Running` with every container Ready
- network `longhorn-system/storage-network`
- interface `lhnet1`
- an address in `10.32.25.0/24`

If any are absent:

1. Stop the rollout and cordon the affected node.
2. Follow `docs/runbooks/multus-conf-absent-recovery.md` through every pre-recovery check: Multus running, the CNI conf present, the node untainted, and no active CNI EAGAIN cascade.
3. Identify volumes and stateful workloads using the bad instance-manager. Allow active volumes to detach or move before recycling it. During the observed incident this required evicting the affected Kanidm replica.
4. Delete only the bad instance-manager after the active-volume risk is understood. Longhorn may not recreate it while the node is unschedulable; once CNI is healthy, uncordon so the replacement can start.
5. Require the replacement to show `lhnet1`, then wait until every Longhorn volume is healthy. Re-run `verify-node.sh` before proceeding.

Use `docs/runbooks/longhorn-storage-network-cutover.md` for the expected network-status shape and `docs/runbooks/multus-conf-absent-recovery.md` for the authoritative pod-recovery procedure.

## Longhorn looks stalled

`verify-node.sh` reports unhealthy counts, trends, and which volumes changed state, with a full snapshot on an interval. Read it against Longhorn's own pacing before calling it a stall — the script prints the live values:

- at most `concurrent-replica-rebuild-per-node-limit` replica rebuilds run per node at once (5)
- a missing replica is not replaced until `replica-replenishment-wait-interval` has passed (600s)

So a flat unhealthy count with rebuilds in progress is throughput, not a stall, and a flat count with no rebuilds for less than ten minutes is the replenishment wait. The snapshot line reports active rebuild count and maximum progress for this reason.

Suspect a genuine stall when the count has not moved and no rebuilds are active for longer than the replenishment interval. Then check the instance-managers for `lhnet1` first — a missing storage-network attachment stalls rebuilds while leaving every pod Running and Ready.
