---
name: talos-rollout
description: Safely upgrade or reboot Talos nodes in the k8s-prod homelab cluster. Use for Talos version rollouts, node restarts, post-reboot validation, or recovery from a stuck reboot, failed drain, unhealthy Longhorn volume, or missing Longhorn lhnet1 storage-network attachment.
---

# Talos rollout

Roll Talos through the three combined control-plane/worker nodes without losing etcd quorum, storage health, or remote access. Treat every node as stateful.

## Safety invariants

- Operate on exactly one node at a time.
- Use `task talos:upgrade-node IP=<node-ip>` for upgrades. It pins `--reboot-mode=powercycle`; never use Talos's default kexec reboot path on these nodes.
- Stop after any failed check. Never continue merely because Kubernetes marks the rebooted node Ready.
- Require all Longhorn volumes to be healthy and every instance-manager to be Running and Ready with `longhorn-system/storage-network`, interface `lhnet1`, and a `10.32.25.x` address before moving to the next node.
- Keep an out-of-band power path available, especially for `k8s-prod-1`.

## Establish the rollout target

1. Confirm the Talos image/version change is merged to `main`; Flux does not apply Talos configuration or OS upgrades.
2. Update the checkout from `main` and compare the target in `talos/talenv.yaml`, the `talosctl` pin in `.mise.toml`, and the installer image in `talos/talconfig.yaml`.
3. Work where `KUBECONFIG` and `TALOSCONFIG` point to usable credentials. The bundled scripts honor explicit environment paths.

## Preflight the cluster

Run:

```bash
.agents/skills/talos-rollout/scripts/preflight.sh
talosctl health --nodes <healthy-control-plane-ip>
```

Then:

1. Save an etcd snapshot before the first node to an operator-chosen local path outside the repository:

   ```bash
   talosctl -n <healthy-control-plane-ip> etcd snapshot <snapshot-path>
   ```

   Verify the snapshot at that path before relying on it.
2. Record the Tailscale subnet-router pod's current node. Do not reboot the only path by which the operator can reach the cluster without confirming a second path.
3. Inspect the current workloads and PodDisruptionBudgets on the candidate node. Resolve known blockers deliberately. Do not use a server-side drain dry-run: its simulated cordon is not persisted, so Longhorn cannot react to it and its instance-manager PDB can produce a false failure.
4. Discover CloudNativePG clusters and primary placement at runtime. If the candidate hosts a singleton primary protected by a PDB, make an explicit choice:
   - For PostgreSQL availability, scale or replicate it, wait for the new instance to be healthy, and verify the primary has moved off the node.
   - To accept brief downtime, first verify a suitable recoverable backup, then deliberately permit eviction for only that cluster. Restore its PDB protection after recovery.

   Do not automatically patch or scale a database cluster.
5. Let the real Talos cordon/drain exercise Longhorn's drain policy. Do not bypass eviction safeguards with `--force` or `--disable-eviction`.

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

3. Observe the real drain in the task output. If it times out or fails, stop and inspect the node and remaining workloads before choosing recovery.
4. If issuing a raw `talosctl upgrade` for diagnosis, include `--drain-timeout=15m`, `--timeout=20m`, and `--reboot-mode=powercycle`. Stop if the rendered command lacks them.
5. Verify the node and wait for storage recovery:

   ```bash
   .agents/skills/talos-rollout/scripts/verify-node.sh <node-name-or-ip>
   talosctl health --nodes <healthy-control-plane-ip>
   ```

6. Review the candidate node's workloads and the Tailscale subnet-router placement again. Only then select the next node and repeat the full preflight/drain review.

## Finish the rollout

After all nodes have been rolled:

```bash
.agents/skills/talos-rollout/scripts/preflight.sh
talosctl health --nodes <healthy-control-plane-ip>
```

Confirm that every Talos server reports the target version, all three nodes are Ready, etcd is healthy, all Longhorn volumes are healthy, all instance-managers are Running and Ready with `lhnet1`, and the Tailscale connector is Ready.

Report the node order, target version, etcd snapshot path, any drain or storage intervention, and final cluster health.

## Recover safely

Read [references/recovery.md](references/recovery.md) when a reboot stalls, the API route moves, a drain fails, Longhorn becomes degraded, or an instance-manager returns without `lhnet1`. Follow the linked repository runbooks rather than improvising pod deletion around active volumes.
