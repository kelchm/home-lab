---
name: talos-rollout
description: Safely upgrade or reboot Talos nodes in the k8s-prod homelab cluster. Use for Talos version rollouts, node restarts, post-reboot validation, moving a database primary off a node before draining it, or recovery from a stuck reboot, failed drain, unhealthy Longhorn volume, or missing Longhorn lhnet1 storage-network attachment.
---

# Talos rollout

Roll Talos through the three combined control-plane/worker nodes without losing etcd quorum, storage health, or remote access. Treat every node as stateful.

This is an operator-led procedure. The scripts collect facts and enforce mechanical invariants; they do not decide anything. Database strategy, recovery choices, and every go/no-go remain explicit human actions.

## Safety invariants

- Operate on exactly one node at a time.
- Take an etcd snapshot before the first node and verify it at its path.
- Use `task talos:upgrade-node IP=<node-ip>` for upgrades. It pins `--reboot-mode=powercycle`; never use Talos's default kexec reboot path on these nodes.
- Never force a drain. `--force` and `--disable-eviction` bypass Longhorn's drain policy for every workload on the node, not only the one blocking you.
- Check etcd and Talos health after every node.
- Require all Longhorn volumes healthy and every instance-manager Running and Ready with `longhorn-system/storage-network`, interface `lhnet1`, and a `10.32.25.x` address before moving to the next node.
- Stop after any failed check. Never continue merely because Kubernetes marks the rebooted node Ready.
- Keep an out-of-band power path available, especially for `k8s-prod-1`.

Two more, learned from the v1.13.9 rollout:

- **Flux reverts live patches.** Any temporary topology change — a scaled database, a disabled PodDisruptionBudget — is owned by the Kustomization that manages it and is undone at the next reconcile, including mid-rollout after the controllers themselves move. Suspend only that Kustomization or stage a temporary Git change, record what you changed, and restore it deliberately.
- **Verify your own access path, don't infer it.** A `10.32.30.x` destination does not mean a direct LAN link. Confirm which interface actually carries the Kubernetes API and Talos endpoints before assuming a reboot cannot cut you off.

## Establish the rollout target

1. Confirm the Talos image/version change is merged to `main`; Flux does not apply Talos configuration or OS upgrades.
2. Update the checkout from `main` and compare the target in `talos/talenv.yaml`, the `talosctl` pin in `.mise.toml`, and the installer image in `talos/talconfig.yaml`.
3. Work where `KUBECONFIG` and `TALOSCONFIG` point to usable credentials. The bundled scripts honor explicit environment paths.

## Preflight the cluster

Run preflight against the node you intend to roll first. The candidate argument makes the access-path and CloudNativePG checks specific to it:

```bash
.agents/skills/talos-rollout/scripts/preflight.sh <candidate-node>
talosctl health --nodes <healthy-control-plane-ip>
```

Preflight prints a one-line summary per check and expands raw output only for checks that fail. Set `VERBOSE=1` to see every table. It exits non-zero listing every failure it found, so read the whole list rather than fixing the first line and re-running.

It gates on: routes to every rollout destination, Talos reachable at each node, etcd quorum, all Kubernetes nodes Ready **and uncordoned**, Multus safeguards Ready, all Longhorn volumes healthy, and all instance-managers Ready with `lhnet1`. It reports without judging: which interface carries your API and Talos paths, which node hosts each Tailscale pod, every CloudNativePG cluster with its instance count and primary placement, every suspended Flux Kustomization, and every PodDisruptionBudget at zero allowed disruptions.

Then, in order:

1. Save an etcd snapshot before the first node, to an operator-chosen local path outside the repository:

   ```bash
   talosctl -n <healthy-control-plane-ip> etcd snapshot <snapshot-path>
   ```

   Verify the snapshot at that path before relying on it.

2. **Settle the access path.** Preflight reports whether your route to the Kubernetes API and each Talos node runs over a direct link or a tunnel, and whether the candidate hosts the Tailscale subnet router or operator. If your path depends on a pod the candidate hosts, choose deliberately: pre-move the router to a surviving node and confirm the Connector is Ready there, or accept that eviction will transiently reset your API connection. Do not discover this mid-drain.

3. **Decide about database primaries.** Preflight flags any CloudNativePG singleton primary on the candidate. This needs an explicit availability decision — a temporary replica and switchover, or accepted downtime with a verified restorable backup. Follow [references/cnpg-failover.md](references/cnpg-failover.md). Do not automatically patch or scale a database cluster.

4. **Resolve remaining eviction blockers.** Review the candidate's workloads and the PodDisruptionBudgets preflight listed at zero allowed disruptions. Resolve each deliberately. Do not use a server-side drain dry-run: its simulated cordon is not persisted, so Longhorn cannot react to it and its instance-manager PDB produces a false failure.

5. Let the real Talos cordon/drain exercise Longhorn's drain policy.

## Roll one node

Use the stable mapping:

| Node | Talos IP |
| --- | --- |
| `k8s-prod-1` | `10.32.30.11` |
| `k8s-prod-2` | `10.32.30.12` |
| `k8s-prod-3` | `10.32.30.13` |

For the selected node:

1. Keep its KVM or physical power control open.
2. Run the guarded upgrade task:

   ```bash
   task talos:upgrade-node IP=<node-ip>
   ```

3. Observe the real drain in the task output. If it times out or fails, stop and inspect the node and remaining workloads before choosing recovery; see [references/recovery.md](references/recovery.md) for the clean-retry path.
4. Read the tracker carefully. Lines containing `error` frequently describe shutdown tasks that completed, and are not by themselves proof of failure. A zero exit code is not proof of success either. Neither is a health signal.
5. If issuing a raw `talosctl upgrade` for diagnosis, include `--drain-timeout=15m`, `--timeout=20m`, and `--reboot-mode=powercycle`. Stop if the rendered command lacks them.
6. Verify the node and wait for storage recovery:

   ```bash
   .agents/skills/talos-rollout/scripts/verify-node.sh <node-name-or-ip>
   talosctl health --nodes <healthy-control-plane-ip>
   ```

   The volume watcher reports unhealthy counts, the trend since it started, and which volumes changed state, with a full snapshot every two minutes rather than reprinting everything each poll. It also prints Longhorn's configured rebuild concurrency and replenishment interval so you can tell normal pacing from a stall.

7. Re-run the candidate review for the next node — access path, database primaries, disruption budgets — then repeat the full preflight. Placement changed when the last node drained.

## Finish the rollout

After all nodes have been rolled:

```bash
.agents/skills/talos-rollout/scripts/preflight.sh
talosctl health --nodes <healthy-control-plane-ip>
```

Confirm every Talos server reports the target version, all three nodes are Ready and uncordoned, etcd is healthy, all Longhorn volumes are healthy, all instance-managers are Running and Ready with `lhnet1`, and the Tailscale connector is Ready.

The rollout is not complete until every temporary change is reversed: database topologies restored with no leftover PVCs, Longhorn volumes, or inactive replication slots; and no Flux Kustomization left suspended. Preflight lists suspended Kustomizations for exactly this reason.

Report the node order, target version, etcd snapshot path, any drain, database, or storage intervention, what was temporarily changed and how it was restored, and final cluster health.

## Recover safely

Read [references/recovery.md](references/recovery.md) when a reboot stalls, the API route moves, a drain fails, Longhorn looks stalled or degraded, or an instance-manager returns without `lhnet1`. It covers how to read the upgrade tracker, what independent evidence to gather before touching power, and how to retry cleanly. Follow the linked repository runbooks rather than improvising pod deletion around active volumes.
