# Roadmap

Forward-looking design and deferred work. Current-state reference lives in
[architecture.md](architecture.md); much of the addressing, BGP, and storage
design there is deliberately shaped to make the items below land cleanly.

## Proxmox virtualization cluster

The three HP EliteDesk 800 G3 machines now run the independent `pve-sbx` Proxmox VE cluster. PVE uses VLAN 20 for management and primary Corosync, matching `.21-.23` identities on VLAN 25 for storage, migration, and secondary Corosync, and the shared VLAN 21 Workloads network for general-purpose guests. VLAN 31 remains reserved for a second Kubernetes cluster.

The design, acceptance record, and remaining rollout gates are in the [PVE cluster plan](plans/20260814-pve-cluster.md). Current endpoints, checks, backup and restore operations, and manually applied host artifacts live in [`proxmox/README.md`](../proxmox/README.md).

Important boundaries:

- PVE remains operable with Kubernetes completely unavailable.
- Initial host bootstrap and rolling updates are documented manual operations; OpenTofu manages guests. Ansible is deferred until repeated host drift or rebuild work justifies it.
- PVE operator documentation and manually applied host artifacts live under `proxmox/`; guest OpenTofu will join that owner directory when the first durable guest is declared.
- Local LVM-thin is the default runtime tier. Synology NFS holds templates and
  backups; shared guest storage is optional for workloads that justify PVE HA
  and the resulting NAS runtime dependency.
- Ceph is not planned for these single-NVMe, 32 GB, 2.5 GbE nodes.

## Second Kubernetes cluster

The network model reserves a second Kubernetes identity without choosing its
deployment substrate.

### Two-cluster topology

It may run as one Talos VM per PVE host or later move to bare metal while
retaining the same addresses and BGP policy:

- compute VLAN 31 and `k8s-sbx.home.kelch.io` API VIP `.8`;
- `k8s-sbx-{1,2,3}` system-4 host identities `.41-.43` on VLANs 31 and 25;
- storage-pod range `.144/28`;
- BGP ASN 65021 and `admin-sandbox` / `services-sandbox` LB pools; and
- per-neighbor prefix filters that prevent either cluster from advertising the
  other's pool prefixes.

No implementation date or hardware assignment is committed. PVE does not
consume these reservations merely by hosting unrelated guests.

## Out of scope (future considerations)

- **Cilium Gateway API replacing Traefik**: defer until Cilium implements GEP-1494 (external auth filter). Until then Traefik's middleware story is irreducible for auth-less app UIs.
- **Pod CIDR BGP advertisement**: Cilium can advertise pod IPs via BGP for direct LAN reachability. Not needed for current use cases.
- **OpenObserve alerting on BGP session state**: deferred during the BGP migration; revisit once OpenObserve is the obvious place for it.
