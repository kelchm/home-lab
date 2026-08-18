# Talos rollout recovery

## Reboot stops after shutdown

`k8s-prod-1` reproduced this twice with Talos's default reboot mode. The
console showed a clean shutdown through workload stop, filesystem unmount,
bootloader selection, kexec preparation, and service termination, then video
went black without completing the restart. This points at the kexec handoff,
not an etcd, kubelet, or Longhorn shutdown failure.

1. Check `talosctl -n <ip> version` and `kubectl get node <name>` from another
   control-plane node. Distinguish a dead display from a node that is still
   serving.
2. Capture the final KVM frame and exact timestamps. If Talos is still
   reachable, capture `dmesg`, `controller`, and `kubelet` logs before power is
   removed.
3. If the node remains stuck, use the KVM/physical power control to power-cycle
   it. Do not retry the default reboot path.
4. Run `verify-node.sh`, then complete all cluster and Longhorn checks before
   touching another node.

The permanent workaround is executable: `.taskfiles/talos/Taskfile.yaml`
passes `--reboot-mode=powercycle`, which bypasses kexec.

## Instance-manager is missing `lhnet1`

During the v1.13.8 rollout, an instance-manager could start before Multus had
republished `/etc/cni/net.d/00-multus.conf`. Kubernetes then reported the node
Ready while the pod had only Cilium networking. This occurred after two node
reboots and left Longhorn without its storage-VLAN path.

Detection requires all of the following in the instance-manager's
`k8s.v1.cni.cncf.io/network-status` annotation:

- network `longhorn-system/storage-network`
- interface `lhnet1`
- an address in `10.32.25.0/24`

If any are absent:

1. Stop the rollout and cordon the affected node.
2. Follow `docs/runbooks/multus-conf-absent-recovery.md` through every
   pre-recovery check: Multus running, the CNI conf present, the node untainted,
   and no active CNI EAGAIN cascade.
3. Identify volumes and stateful workloads using the bad instance-manager.
   Allow active volumes to detach or move before recycling it. During the
   observed incident this required evicting the affected Kanidm replica.
4. Delete only the bad instance-manager after the active-volume risk is
   understood. Longhorn may not recreate it while the node is unschedulable;
   once CNI is healthy, uncordon so the replacement can start.
5. Require the replacement to show `lhnet1`, then wait until every Longhorn
   volume is healthy. Re-run `verify-node.sh` before proceeding.

Use `docs/runbooks/longhorn-storage-network-cutover.md` for the expected
network-status shape and `docs/runbooks/multus-conf-absent-recovery.md` for the
authoritative pod-recovery procedure.

## Drain is blocked

Do not turn a PDB failure into a forced drain. Identify whether the blocker is
an operator-managed singleton, a database replica, or an access-path pod.
Move or safely quiesce it, repeat the server-side drain dry-run, and preserve a
healthy etcd majority throughout.

## Tailscale route moves

Draining the node hosting the Tailscale subnet-router can briefly reset an API
client connection while the pod reschedules. Reconnect, confirm the router is
Ready on a surviving node, and repeat the cluster checks. Treat a continuing
loss of access as a stop condition, not as an expected transient.
