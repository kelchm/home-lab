# Network topology: workloads VLAN, DGX Spark placement, VLAN 40 retirement

**Status:** Active 2026-08-22. Partially implemented; current host state and
remaining phase-3 work are tracked in the
[DGX Spark bring-up runbook](../runbooks/dgx-spark-bringup.md).

**Scope:** VLAN/zone model for the whole lab — DGX Spark placement and interconnect, PVE guest wiring (amending the [PVE cluster plan (PR #373)](https://github.com/kelchm/home-lab/pull/373)), VLAN 40 teardown, and the reserved identity for a second Kubernetes cluster. Each migration phase lists its runtime and repository changes.

## Implementation status

- Phase 2 is partial: VLAN 21 `Workloads` and the `spark-trunk` switch profile
  are applied, but the firewall matrix remains deferred.
- Phase 3 is active: both Sparks have their LAN, storage, and direct-fabric
  interfaces configured; jumbo Ethernet, raw RDMA, NCCL, reboot persistence,
  RTL8127 burn-in, and host anti-transit have passed. DSM/NFS throughput, DNS,
  gateway route read-back, firewall tests, and temporary-access cleanup remain.
- All unlisted phases remain planned rather than applied.

The implemented fabric allocation differs from the original proposal. The
initial `192.168.100/101.0/31` rails overlapped the gateway's existing
Starlink-management meaning for `192.168.100.1`. On 2026-08-22 the Sparks were
readdressed onto the isolated `/24` rails documented below, then revalidated.

## Decision summary

- Add exactly one VLAN: **21 — Workloads** (`10.32.21.0/24`) for LAN-serving servers that aren't part of a more specific plane: the two DGX Sparks and general-purpose PVE guests.
- Retire VLAN 40 end-to-end. Live-verified 2026-08-21: zero `CiliumL2AnnouncementPolicy` objects exist (CRD installed, never instantiated), legacy `admin`/`services` pools show 0 IPs used, and all three LoadBalancer services hold BGP-pool addresses. The L2 path is dead code.
- The Spark-to-Spark QSFP interconnect is a physical, unrouted point-to-point fabric — not a VLAN, not in `10.32.0.0/16`, never advertised.
- Amend the PVE plan's wiring: guests move to the 2.5 GbE trunk; the onboard 1 GbE carries only management + Corosync link 0. Guest network is VLAN 21, not VLAN 31.
- VLAN 31, LB pools `10.32.131/141.0/24`, ASN 65021, and storage slots `10.32.25.41-.43` + `.144/28` stay coherently reserved for a second Kubernetes cluster — whether it lands as PVE VMs or bare metal.
- Service reachability policy continues to live on routed BGP prefixes; VLANs are reserved for genuinely different link-layer needs (quorum latency, bulk L2 adjacency, RDMA, management blast radius, client trust grades).

## Traffic and trust model

Six classes, and only six:

| Class | Members | Boundary mechanism |
|---|---|---|
| Client zones | Main, IoT, Guest, Cameras | VLANs 10/90/99/5 + gateway policy |
| Management plane | NAS admin, PVE/Talos/K8s APIs, GLKVM, PDM/PBS | VLAN 20; admin-clients-only ingress |
| Bulk storage | Longhorn replication, NFS, migration, backups, Spark model reads | VLAN 25, switched at L2, off the gateway |
| Workload servers | Sparks, general PVE guests, future standalone servers | VLAN 21; client-reachable service ports only |
| Cluster-internal planes | K8s pod/node east-west; Corosync; NCCL/RDMA | VLAN 30/31; dedicated NIC; physical fabric |
| Routed service prefixes | BGP LB pools 130/140/150 (+131/141 reserved) | Per-prefix gateway policy; no VLAN membership |

A dedicated Spark VLAN was rejected: two devices whose policy profile is identical to Workloads, and their unique east-west need is already a separate physical network. Placing Sparks in VLAN 20 or 30 was rejected as the wrong trust class in both directions. Switch-local L3 routing for bulk flows was rejected because it bypasses the gateway firewall entirely and splits the routing brain; the flows that would benefit are instead kept switched at L2 on VLAN 25.

### Why VLAN 21 and not 50

The LB-pool tens digit is inherited from the VLANs the legacy L2 pools were carved out of: `admin` lived in VLAN 30's subnet, `services` in VLAN 40's, and the BGP pools are those prefixes plus 100 (`130`, `140`). That makes `150 = shared` a phantom VLAN 50. Numbering Workloads as 50 would falsely pair it with the shared pool for any reader who spots the pattern. VLAN 21 avoids the pool-digit namespace entirely and makes the decades read as families:

| Range | Meaning |
|---|---|
| 1–19 | Client/edge networks (1 UniFi mgmt, 5 cameras, 10 main) |
| 20–29 | Server-side shared planes (20 infra mgmt, 21 workloads, 25 storage) |
| 30–39 | Kubernetes compute; units digit = cluster index (30 prod, 31 reserved) |
| 90–99 | Untrusted (90 IoT, 99 guest) |
| 100+ | Not VLANs — routed BGP pool space; tens digit = policy class |

VLAN names drop the "Lab" prefix; it no longer distinguishes anything: 20 **Infra Mgmt**, 25 **Storage**, 30 **K8s Prod**, 31 **K8s 2 (reserved)**.

## Addressing

### VLAN 21 — Workloads, `10.32.21.0/24`

| Range | Allocation |
|---|---|
| `.1` | UniFi gateway (`gateway-workloads.home.kelch.io`) |
| `.2-.10` | Network/service anchors (reserved) |
| `.11` | `spark-1.home.kelch.io` (10 GbE, native) |
| `.12` | `spark-2.home.kelch.io` |
| `.13-.19` | Future physical workload hosts |
| `.20-.49` | Fixed platform guests (e.g. `.21` Home Assistant VM, `ha.home.kelch.io`) |
| `.50-.99` | Fixed application guests (OpenTofu/cloud-init static) |
| `.100-.199` | Long-lived DHCP reservations |
| `.200-.239` | Dynamic DHCP |
| `.240-.254` | Reserved |

### VLAN 25 — Storage additions

Convention change: storage decades allocate **per system in commissioning order**, not by Kubernetes cluster index — `.1X` k8s-prod, `.2X` pve-lab, `.3X` workload hosts, `.4X` next system.

| Address | DNS | Use |
|---|---|---|
| `10.32.25.31` | `spark-1-storage.home.kelch.io` | Spark 1 tagged storage leg |
| `10.32.25.32` | `spark-2-storage.home.kelch.io` | Spark 2 tagged storage leg |
| `10.32.25.41-.43` | — | Reserved: k8s cluster 2 storage NICs |
| `10.32.25.144-.159` | — | Reserved: k8s cluster 2 storage-pod `/28` (only if it runs Longhorn+Multus) |

### Spark interconnect fabric

Each QSFP port exposes two logical ~100 G interfaces (the ConnectX-7 aggregates two PCIe Gen5 x4 links behind one cage), so a single DAC yields two netdev pairs; NVIDIA's playbook addresses them as two subnets. The lab reserves `198.19.240.0/20` from the RFC 2544 benchmarking block for isolated machine fabrics and assigns one `/24` per rail:

| Link | spark-1 | spark-2 | MTU |
|---|---|---|---|
| Subnet A | `198.19.240.11/24` | `198.19.240.12/24` | 9000 |
| Subnet B | `198.19.241.11/24` | `198.19.241.12/24` | 9000 |

The third octet is the rail identifier beginning at 240; `.11/.12` preserve the workload-host identity and `/24` permits a future switched rail without renumbering. No gateway, no DNS, `/etc/hosts` entries only. `NCCL_SOCKET_IFNAME` pins control traffic to the 10 GbE. Fabric prefixes exist only as directly connected routes on participating endpoints. They never enter the gateway routing table, router firewall/address objects, BGP/OSPF, or Tailscale advertisements. Workload and service software uses `10.32.21.x`; fabric addresses are limited to RDMA/NCCL and explicit operator diagnostics. If that boundary changes, renumber to fallback rails `10.254.240.0/24` and `10.254.241.0/24` rather than adding exceptions.

## PVE wiring amendment

Amendments to the [PVE cluster plan (PR #373)](https://github.com/kelchm/home-lab/pull/373); its addressing, storage, backup, IaC, and rollout content stands.

- **Onboard 1 GbE**: access port on VLAN 20, plain static interface, no bridge. Carries PVE UI/API, SSH, and Corosync link 0 — nothing else. UI/SSH traffic is negligible, so this is effectively the dedicated Corosync NIC the [Proxmox docs](https://pve.proxmox.com/pve-docs/chapter-pvecm.html) recommend.
- **RTL8125 2.5 GbE**: trunk carrying one VLAN-aware bridge (`bridge-vids 10 21 25 90`, no native VLAN — an untagged guest NIC still fails closed). Host addresses only on the `.25` subinterface (storage, migration, Corosync link 1). Guests attach tagged: 21 by default, 90 or 10 only by deliberate zone placement. Manage the tag set as a Proxmox SDN VLAN zone so all three hosts stay identical.
- Rationale for moving guests off the 1 GbE: the original wiring put guest traffic on the same physical link as Corosync link 0, contradicting its own jitter rationale for keeping link 0 off VLAN 25 — a guest saturating 1 GbE creates exactly the congestion [Proxmox staff warn about](https://forum.proxmox.com/threads/proxmox-corosync-cluster-dedicated-network-why.139557/). Under this wiring a 2.5 GbE/RTL8125 failure costs a node its storage and guests but not quorum or management; the reverse held before.
- Trade accepted: guests now contend with storage/migration/backup on the 2.5 GbE. Quorum stability is the higher-value invariant, and guests also gain 2.5× the bandwidth ceiling.
- A VM needing L2/mDNS adjacency to IoT (Home Assistant) starts in VLAN 21 with an allow rule plus the UniFi mDNS repeater; a second vNIC tagged 90 is the escalation if discovery breaks empirically.

## Switch-port profiles

| Profile | Native | Tagged | Applied to |
|---|---|---|---|
| `mgmt-infra` (access 20) | 20 | — | PVE 1 GbE ×3, NAS 1 GbE, GLKVM |
| `storage` (access 25) | 25 | — | k8s 2.5 GbE ×3, NAS SFP+ |
| `k8s-node` (access 30) | 30 | — (VLAN 40 tag removed) | k8s 1 GbE ×3 |
| `pve-guest-trunk` | none | 10, 21, 25, 90 (+31 when cluster 2 lands) | PVE 2.5 GbE ×3 |
| `spark-trunk` | 21 | 25 | Spark 10 GbE ×2 (aggregation switch) |
| Uplinks/LAGs | all | — | Restore the lab-switch LAG to 2×10 G first |

## Firewall-policy matrix

One UniFi zone per VLAN — never merge two VLANs into a zone, since intra-zone traffic is unconditionally allowed. Zone rules are stateful. Kubernetes pod egress arrives SNAT'd as node IPs, so K8s-sourced rules key on `10.32.30.11-.13`.

| From ↓ To → | Mgmt 20 | Storage 25 | K8s 30 | Workloads 21 | LB-admin 130 | LB-services 140 | IoT 90 | Guest/Cam | WAN |
|---|---|---|---|---|---|---|---|---|---|
| Admin devices (Main group) | allow 8006/22/443/DSM | allow (diagnostics) | allow 6443/50000 | allow all | allow | allow | allow | allow | allow |
| Main (rest) | deny | deny | deny | service ports only | deny | allow | mDNS repeater only | deny | allow |
| Mgmt 20 | — | allow (NAS backup paths) | deny | deny | deny | deny | deny | deny | DNS/NTP/HTTPS |
| Storage 25 | deny | L2 enclave; deny all routed | deny | deny | deny | deny | deny | deny | deny |
| K8s 30 (node IPs) | deny | (L2, not routed) | — | explicit inference ports on `.21.11-.12`; else deny | — | — | deny | deny | allow |
| Workloads 21 | deny | deny routed (Sparks use the L2 leg; guest NFS by per-IP exception) | deny | intra-zone open (accepted) | deny | allow | HA VM `.21.21` → allow; else deny | deny | allow |
| IoT / Guest / Cameras | deny | deny | deny | deny | deny | deny | — | — | Guest/IoT allow; Cameras deny |
| WAN | deny | deny | deny | deny | deny | deny | deny | deny | — |

Most of this table does not exist on the controller today — the documented `bgp-lb-restricted` posture is unapplied intent. Phase 2 is where documented intent becomes applied state.

## Migration phases

### Phase 0 — verification, no windows

1. Inventory and record in `network/unifi/README.md`: gateway model, switch models, and a port map (the only port documentation today is a comment in `tools/longhorn-bench/fio-loadtest.yaml`).
2. Measure gateway inter-VLAN throughput with IDS/IPS in its current mode (iperf3 Main ↔ VLAN 20) to establish the real routed ceiling; IDS/IPS disables hardware offload on UniFi gateways.
3. Verify whether the gateway's FRR installs true ECMP for the LB `/32`s (`vtysh -c 'show bgp summary'` and the kernel route table).
4. Replace the missing SFPs to restore the aggregation↔lab-switch LAG to 2×10 G.
5. Confirm why all three BGP sessions re-established on 2026-08-20 before building on top.

### Phase 1 — retire VLAN 40 (window ~30 min)

1. Delete the legacy `admin`/`services` pools (`kubernetes/apps/kube-system/cilium/app/networks.yaml`), set `l2announcements.enabled: false` and `devices: eno+` in the Cilium HelmRelease; reconcile; verify all three VIPs from Main.
2. Per node, one at a time with full recovery between: remove the VLAN 40 subinterface from `talos/patches/k8s-prod-N/network-extras.yaml` and `talos/talconfig.yaml`, apply, verify BGP re-established and Longhorn healthy.
3. Remove the VLAN 40 tag from the k8s node switch ports; delete the UniFi network.
4. Rollback at every step is `git revert` + reconcile. Gate: continuous VIP reachability and 3/3 established BGP sessions throughout.

### Phase 2 — create VLAN 21 + apply the firewall for real

Create the network, DHCP scope (`.200-.239`), zones, and the full matrix above — including the long-documented `bgp-lb-restricted` rules. Inert until consumers arrive. Negative-test from IoT and Guest: `curl` to `10.32.130.1` and `10.32.140.1` must fail.

### Phase 3 — DGX Sparks

Execution state, exact host configuration, benchmarks, and remaining gates are
tracked in the [DGX Spark bring-up runbook](../runbooks/dgx-spark-bringup.md).

1. Connect each Spark's 10 GbE to the aggregation switch on `spark-trunk`; static `10.32.21.11/.12` plus storage legs `10.32.25.31/.32`.
2. Disable EEE on the RTL8127 ports preemptively ([known instability report](https://forums.developer.nvidia.com/t/defective-onboard-rtl8127-nic-on-dgx-spark/378356)).
3. Scope the Synology NFS exports the Sparks need to `.25.31-.32`.
4. Cable the QSFP DAC using the same port number on both ends ([NVIDIA clustering guide](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html), [connect-two-sparks playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/connect-two-sparks/README.md)); address both `/24` rails; MTU 9000; `NCCL_SOCKET_IFNAME=<10GbE>`.
5. Tests: iperf3 Spark→NAS over the VLAN 25 leg ≥9 Gbit/s with `traceroute` proving one hop; iperf3 Spark→Main documenting the routed ceiling for contrast; `ib_write_bw`/NCCL all_gather expecting ~22-24 GB/s on current firmware (pre-2026-02 firmware caps near 100 Gb/s); `ip route` on each Spark shows the fabric as connected-only; the gateway has no path to `198.19.240.0/20`.

### Phase 4 — PVE

Follow the [PVE cluster plan (PR #373)](https://github.com/kelchm/home-lab/pull/373) phases 0-6 with the wiring amendment above. Added gates:

- Corosync under load: saturate the 2.5 GbE with iperf3 plus a vzdump run while watching `corosync-cfgtool -s` link stats and sub-ms ping on link 0; no retransmit/jitter events.
- Pull the 1 GbE on one node: quorum retained via link 1, no reboot (watchdog disarmed without HA resources). Restore and verify fallback.

### Phase 5 — deferred: second Kubernetes cluster

Fills VLAN 31, pools `131/141`, ASN 65021, storage `.41-.43` (+`.144/28` if Longhorn) exactly as reserved. As PVE VMs: one Talos VM per host, vNIC 0 tagged 31, vNIC 1 tagged 25, VIP `10.32.31.8` via vipController, new pod/service CIDRs (e.g. `10.44.0.0/16`/`10.45.0.0/16`), no PVE HA on node VMs, per-vNIC PVE MAC/IP filtering off (GARP VIP), deterministic MACs in OpenTofu. The same cluster can later migrate VM-by-VM to bare metal without touching any address, BGP session, or firewall rule.

## Repo-alignment checklist

Edits land with their phases, not before:

- `docs/architecture.md` — VLAN table (add 21, rename 20/25/30/31, delete 40), storage-VLAN decade convention, hardware table (add Sparks), DNS table (spark/ha entries), key-decisions (workloads zone; fabric-not-VLAN).
- `docs/plans/20260814-pve-cluster.md` (once PR #373 merges) — guest bridge to the 2.5 GbE, guest network VLAN 21, VLAN 31 stays reserved; its network-policy table and phase 0 gate reference 31 throughout.
- `network/unifi/README.md` — PVE rules amendments, Spark/VLAN 21 rules, VLAN 40 teardown record, new switch/port-map section.
- `talos/talconfig.yaml` + `talos/patches/k8s-prod-{1,2,3}/network-extras.yaml` — remove VLAN 40.
- `kubernetes/apps/kube-system/cilium/app/helmrelease.yaml` + `networks.yaml` — devices, l2announcements, legacy pools.
- `docs/roadmap.md` — Sparks; second-cluster stance.
- Untracked `cluster.yaml` — stale `10.32.40.30`; fix or delete locally.

Contradictions this plan resolves: PR #373 reserves k8s cluster 2's LB pools while consuming its VLAN and storage decade; PR #373's guest/Corosync NIC sharing contradicts its own link-priority rationale; the repo's firewall documentation reads as posture but is unapplied; main's hardware table lists SN850s for the G3s while the PVE plan expects SN770s (unverified either way).

## Assumptions and unresolved decisions

- The gateway model is not recorded anywhere; BGP support, IDS/IPS ceiling, and routed throughput depend on it. Phase 0 resolves it; nothing here assumes a specific model.
- Core switch model, NAS SFP+ physical landing, and current DHCP scopes exist only in the controller; inventoried in phase 0.
- Sparks are assumed to connect via 10GBase-T SFP+ transceivers in the aggregation switch; verify thermals/compatibility or use the lab switch's SFP+ ports.
- One DAC is installed between physical port 0 on both Sparks; it exposes both logical-interface pairs.
- Direct client access to Spark inference (as designed) vs. fronting through the services Traefik gateway is a reversible taste decision.
- Whether PVE HA is ever enabled changes the Corosync risk calculus (disarmed watchdog vs. fleet-wide self-fencing on a lab-switch outage); the wiring is safe under both.
- Jumbo frames on VLAN 25 were considered and rejected: mixed 1G/2.5G/10G endpoints plus the whereabouts pod range make silent-blackhole risk that isn't worth single-digit gains at 2.5 G line rate. MTU 9000 applies to the Spark fabric only.

## References

- [Proxmox cluster requirements (pvecm)](https://pve.proxmox.com/pve-docs/chapter-pvecm.html) — <5 ms latency, PPS over bandwidth, dedicated-NIC recommendation, knet link priorities.
- [Proxmox HA manager](https://pve.proxmox.com/pve-docs/chapter-ha-manager.html) — watchdog disarmed without HA resources.
- [Proxmox SDN](https://pve.proxmox.com/wiki/Software-Defined_Network) — cluster-wide VLAN zones over VLAN-aware bridges.
- [NVIDIA DGX Spark clustering](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html) and [dgx-spark-playbooks](https://github.com/NVIDIA/dgx-spark-playbooks) — dual-system cabling, two-subnet addressing, keep CX-7 off management traffic.
- [ServeTheHome on GB10 ConnectX-7](https://www.servethehome.com/the-nvidia-gb10-connectx-7-200gbe-networking-is-really-different/) — two ~100 G MACs per QSFP cage.
- [RFC 2544](https://datatracker.ietf.org/doc/html/rfc2544) — benchmarking address block used for the isolated machine-fabric allocation.
- [Cilium L2 announcements](https://docs.cilium.io/en/stable/network/l2-announcements/) — single-announcer model retired here in favor of BGP ECMP.
- [UniFi BGP](https://help.ui.com/hc/en-us/articles/16271338193559-UniFi-Border-Gateway-Protocol-BGP) — FRR config upload, supported gateways.
- ShiftCTRL on [IDS/IPS throughput](https://shiftctrl.net/articles/unifi-ids-ips-throughput-ceiling) and [inter-VLAN hairpin/zone behavior](https://shiftctrl.net/articles/unifi-vlan-mistakes).
