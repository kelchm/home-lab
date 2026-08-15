# Roadmap

Forward-looking design and deferred work. Current-state reference lives in
[architecture.md](architecture.md); much of the addressing and storage
design there is deliberately shaped to make the items below land cleanly.

## Proxmox virtualization cluster

The three HP EliteDesk 800 G3 machines are planned as an independent three-node
Proxmox VE cluster. This replaces the earlier proposal to use them for a second
Talos cluster. PVE uses VLAN 20 for management and primary Corosync, the
already-reserved `.21-.23` addresses on VLAN 25 for 2.5 GbE storage/migration
and secondary Corosync, and VLAN 31 as the isolated guest network.

The detailed draft—including every host IP, DNS name, bridge, storage tier,
backup/retention policy, HA class, update cadence, Git/IaC boundary, and rollout
gate—is in [the PVE cluster plan](plans/20260814-pve-cluster.md).

Important boundaries:

- PVE remains operable with Kubernetes completely unavailable.
- Declarations will live in a self-contained top-level `proxmox/` tree, but
  Flux and Kubernetes-hosted runners will never apply them.
- Initial host bootstrap and rolling updates are documented manual operations;
  OpenTofu manages guests. Ansible is deferred until repeated host drift or
  rebuild work justifies it.
- Local LVM-thin is the default runtime tier. Synology NFS holds templates and
  backups; a separate shared-guest export is optional for the few guests that
  justify PVE HA and the resulting NAS runtime dependency.
- Ceph is not planned for these single-NVMe, 32 GB, 2.5 GbE nodes.

## Out of scope (future considerations)

- **Cilium Gateway API replacing Traefik**: defer until Cilium implements GEP-1494 (external auth filter). Until then Traefik's middleware story is irreducible for auth-less app UIs.
- **Pod CIDR BGP advertisement**: Cilium can advertise pod IPs via BGP for direct LAN reachability. Not needed for current use cases.
- **OpenObserve alerting on BGP session state**: deferred during the BGP migration; revisit once OpenObserve is the obvious place for it.
