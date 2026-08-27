# Three-node PVE cluster

**Status:** Active — 2026-08-27; core cluster implemented, remaining commissioning gates tracked in [`proxmox/README.md`](../../proxmox/README.md).

**Plan date:** 2026-08-14

**Target platform:** Proxmox VE 9.2-1 x86-64 through the pinned and verified local netboot entry

## Implementation checkpoint

The three nodes were installed through the pinned local PVE 9.2-1 netboot path, upgraded to PVE Manager 9.2.11 with kernel 7.0.14-14-pve, and formed into the quorate `pve-sbx` cluster on 2026-08-27. Management, the VLAN-aware 2.5 GbE guest/storage bridge, dual Corosync links, secure storage-network migration, the key-only `kelchm` Unix operator with sudo, the no-subscription repository baseline, shared NFS library and backup storage, a daily backup job, the local `kelchm@pve` administrator, and the PVE-specific Workloads containment rules are live.

Acceptance has proven failover between the two Corosync links, local-LVM guest I/O, online local-disk migration over VLAN 25, guest-agent-consistent backup to Athena, and an isolated restore onto another node. The restored copy booted with its NIC removed and was destroyed after verification; the backup archive remains.

Commissioning is not complete. As of 14:48 EDT, three correctable PCIe/AER events had appeared on the SN770 path of node 1, two on node 2, and two on node 3 under default APST and PCIe L1 behavior, which fails this plan's zero-AER storage gate even though SMART, NVMe error logs, guest I/O, migration, backup, and restore remained clean. Node 3's two events appeared after it completed a bounded 100-cycle idle-to-read screen without AER, so the three-node idle/I/O qualification must be repeated after diagnosis. Physical cold-power recovery, a timed restore measurement, TOTP enrollment, per-node ACME DNS-01 certificates, and an authenticated SMTP notification target also remain open. A verified encrypted off-node configuration capture was completed to the admin workstation. Current operator state and commands live in [`proxmox/README.md`](../../proxmox/README.md).

## Outcome

Build one three-node Proxmox VE cluster named `pve-sbx` on the HP EliteDesk 800 G3 hosts. PVE remains a separate failure and control domain from `k8s-prod`, while its operator documentation and deliberately applied host baseline live under the top-level `proxmox/` tree.

Initial operating model:

- install and form the hosts manually from a written, repeatable baseline;
- provision guests with OpenTofu from an admin workstation; Flux does not apply
  PVE changes;
- use the installer-default ext4 root and local LVM-thin guest storage;
- use the Synology for templates and backups, not as the default runtime disk;
- optionally put only selected HA guests on shared Synology NFS after a
  performance and failure-mode test;
- patch one drained node at a time in a scheduled maintenance window; and
- defer Ansible, Ceph, PBS, and PDM until each solves an observed problem.

This is Git-managed infrastructure with deliberate applies, not a claim that
PVE itself has a pull-based GitOps reconciler.

## Goals and non-goals

### Goals

- A general VM and LXC environment that is useful when Kubernetes is unhealthy.
- No PVE bootstrap, state, credential, runner, DNS, or recovery dependency on
  the Kubernetes control plane.
- Stable node identity and complete address assignments before cluster creation.
- Node-by-node maintenance without taking every guest down.
- Restore-tested backups outside the PVE hosts.
- A repeatable rebuild procedure that does not depend on recalling prior UI
  operations.

### Non-goals

- Running another Kubernetes cluster on these hosts by default.
- Making every guest highly available.
- Zero-RPO storage or zero-downtime host maintenance.
- Ceph on three single-disk, 32 GB nodes and a 2.5 GbE network.
- Fully unattended PVE package installation or automatic host reboots.
- Deploying PDM solely to manage this one PVE cluster.
- Pretending that cluster membership makes node-local guest disks highly
  available.

## Known inventory and selected defaults

| Item | Value |
|---|---|
| Cluster | `pve-sbx` |
| Nodes | `pve-sbx-1`, `pve-sbx-2`, `pve-sbx-3` |
| Hardware | 3x HP EliteDesk 800 G3 Mini |
| CPU | Intel Core i5-6500T, 4 cores / 4 threads |
| Memory | 32 GB per node |
| Local disk | 1 TB WD_BLACK SN770 NVMe per node, firmware `731100WD`, LBA format 0 with 512-byte logical sectors |
| Management NIC | Onboard Intel 1 GbE, exposed consistently as `nic1` |
| Storage/guest-trunk NIC | PCIe Realtek RTL8125 2.5 GbE, exposed consistently as `nic0` |
| PVE release | 9.2-1 x86-64 for initial commissioning; later upgrades require a separately reviewed version bump |
| Root storage | Installer-default ext4 root and LVM-thin guest storage ID `local-lvm` |
| Shared system | Synology DS1821+ on `10.32.25.5` |

All three PVE hosts were directly inventoried with `WD_BLACK SN770 1TB` drives on firmware `731100WD`, using LBA format 0 with 512-byte logical sectors. Their model, firmware, capacity, logical sector format, Linux interface names, sanitized SMART facts, BIOS settings, and host keys are recorded in the ignored `.private/pve-sbx/inventory.yaml`. Serial numbers, MAC addresses, and raw SMART output remain outside the tracked repository.

## Address plan

There is no PVE cluster VIP. Every node runs the complete UI/API, so bookmarks,
automation, and certificates use the node FQDNs. Do not create round-robin DNS
for `pve.home.kelch.io`; individual records expose node availability directly
and avoid session or console routing ambiguity.

### Management and Corosync — VLAN 20, `10.32.20.0/24`

| Address | DNS | Use |
|---|---|---|
| `10.32.20.1` | `gateway-infra.home.kelch.io` | UniFi gateway |
| `10.32.20.5` | `nas.home.kelch.io` | Synology management |
| `10.32.20.7` | `pbs.home.kelch.io` | Reserved for an independent future PBS appliance; octet matches `pbs-storage` |
| `10.32.20.10` | `glkvm.home.kelch.io` | GL-RM1PE KVM (existing) |
| `10.32.20.20` | `pdm.home.kelch.io` | Reserved; PDM is not initially deployed |
| `10.32.20.21` | `pve-sbx-1.home.kelch.io` | PVE UI/API, SSH, Corosync link 0 |
| `10.32.20.22` | `pve-sbx-2.home.kelch.io` | PVE UI/API, SSH, Corosync link 0 |
| `10.32.20.23` | `pve-sbx-3.home.kelch.io` | PVE UI/API, SSH, Corosync link 0 |
| `10.32.20.24-.29` | — | Future PVE nodes; leave unallocated |

Host octets `.21-.23` match the storage addresses below per the system identity
rule in [architecture.md](../architecture.md#system-identity-rule): one final
octet per member on every VLAN it touches.

The PVE addresses are configured statically on the hosts. Matching UniFi fixed
assignments may document the MAC-to-IP relation, but DHCP is not authoritative
for cluster identity.

### Storage and migration — VLAN 25, `10.32.25.0/24`

| Address | DNS | Use |
|---|---|---|
| `10.32.25.1` | `gateway-storage.home.kelch.io` | UniFi gateway; not a PVE default route |
| `10.32.25.5` | `nas-storage.home.kelch.io` | Synology NFS endpoint |
| `10.32.25.6` | `s3-storage.home.kelch.io` | Reserved by the NAS out-of-cluster workload plan |
| `10.32.25.7` | `pbs-storage.home.kelch.io` | Reserved for a future PBS data interface |
| `10.32.25.21` | `pve-sbx-1-storage.home.kelch.io` | Migration, NFS, Corosync link 1 |
| `10.32.25.22` | `pve-sbx-2-storage.home.kelch.io` | Migration, NFS, Corosync link 1 |
| `10.32.25.23` | `pve-sbx-3-storage.home.kelch.io` | Migration, NFS, Corosync link 1 |
| `10.32.25.24-.29` | — | PVE expansion; leave unallocated |

Set the datacenter migration network to `10.32.25.0/24` with secure migration.
Each PVE node has exactly one address in that CIDR, which is the invariant PVE
uses to select its migration endpoint. The 2.5 GbE path also carries NFS and
backup traffic, so bulk migration and backup jobs are scheduled rather than all
started simultaneously.

Corosync link 0 uses VLAN 20 on the 1 GbE NIC. Link 1 uses VLAN 25 as a
lower-priority fallback on a separate physical NIC. Corosync stays on link 0 in
normal operation. VLAN 25 is not the primary because concurrent migration,
shared-storage, and backup traffic can introduce latency and jitter that affects
quorum traffic.

### Guest network — VLAN 21, `10.32.21.0/24`

Guests attach to the shared Workloads VLAN. VLAN 31 stays reserved for a
second Kubernetes cluster; hosting guests is not a Kubernetes compute role.
Allocations follow the Workloads table in the
[network-topology plan](20260821-network-topology.md): OpenTofu-managed,
long-lived guests get a fixed address from `.100-.149` through cloud-init;
interactive test VMs use dynamic DHCP (`.200-.239`). A guest that needs a
direct storage leg registers as a workload-system member and takes matching
`10.32.21.3X`/`10.32.25.3X` octets. Guests never inherit a PVE host identity,
and PVE hosts hold no address on VLAN 21. There is no `.8` VIP on this VLAN;
the `.8 = Kubernetes API` mnemonic applies only to a Kubernetes compute VLAN.

DNS remains flat under `home.kelch.io`. A service that exists only on PVE gets
its natural name. A PVE copy of a service that also exists in production
Kubernetes gets `-pve`, for example `foo-pve.home.kelch.io`. The hostname says
which instance it is; the VLAN is never encoded in the name.

## Physical and Linux network design

The owner-confirmed Lab Switch layout is intentionally parallel with `k8s-prod`: `pve-sbx-{1,2,3}` use ports 1–3 for onboard 1 GbE management and ports 13–15 for RTL8125 2.5 GbE trunks, while `k8s-prod-{1,2,3}` use the immediately following blocks at ports 4–6 and 16–18. Treat the PVE and production Kubernetes port blocks as separate change scopes.

### Onboard 1 GbE

The switch port is an access/untagged member of VLAN 20. Put the node's static
management address and only default route directly on the physical interface —
no bridge. It carries PVE UI/API, SSH, and Corosync link 0, nothing else.
UI/SSH traffic is negligible, so this is effectively the dedicated Corosync
NIC the Proxmox docs recommend: a guest saturating a shared link is exactly
the congestion that destabilizes quorum.

Illustrative node-1 shape; substitute the inventoried NIC name:

```text
auto <onboard-nic>
iface <onboard-nic> inet static
    address 10.32.20.21/24
    gateway 10.32.20.1
```

### RTL8125 2.5 GbE

The switch port is a trunk (`pve-guest-trunk`: tagged 10, 21, 25, 90 — no
native VLAN). Create one VLAN-aware bridge, `vmbr0`, over the NIC:

- `vmbr0` itself has no address;
- `vmbr0.25` carries `10.32.25.21/24` (then `.22`, `.23`) with no gateway —
  storage, migration, and Corosync link 1;
- guest NICs attach to `vmbr0` tagged `21` by default; `90` or `10` require
  deliberate zone placement, and `25` is only for a registered
  storage-attached guest with an explicit per-IP NAS/export ACL; and
- no guest may be created with an untagged NIC.

With no native VLAN on the trunk, an accidentally untagged guest fails closed
instead of landing on any host network. Keep the explicit `bridge-vids` tag
set identical on all three hosts. Defer a Proxmox SDN zone/VNet layer until it
solves a repeated management problem; do not configure direct bridge tags and
SDN as competing sources of truth. If the installer cannot express the final
config, install temporarily on an access VLAN 20 port, convert the host from
its local console, and only then create or join the cluster.

```text
auto <2.5g-nic>
iface <2.5g-nic> inet manual

auto vmbr0
iface vmbr0 inet manual
    bridge-ports <2.5g-nic>
    bridge-stp off
    bridge-fd 0
    bridge-vlan-aware yes
    bridge-vids 10 21 25 90

auto vmbr0.25
iface vmbr0.25 inet static
    address 10.32.25.21/24
```

Under this wiring a 2.5 GbE/RTL8125 failure costs a node its storage and
guests but not quorum or management; guests trade contention with
storage/migration/backup traffic for a 2.5× bandwidth ceiling and a
congestion-free Corosync link 0.

### Name resolution and time

- Create all six node A records as static UniFi-local records before
  `pvecm create` or `pvecm add`; never make PVE host resolution depend
  exclusively on `k8s-gateway`.
- Put all three management names and addresses in every node's `/etc/hosts`.
- Use `home.kelch.io` as the search domain.
- Use the UniFi gateway as the local DNS resolver so internal names resolve;
  cluster traffic itself uses the fixed addresses.
- Use `America/New_York` and two external NTP sources. A bad clock is a cluster
  and certificate problem, not cosmetic drift.

Hostnames and management IPs are difficult to change safely after cluster
formation. Validate both before a node joins the cluster.

## Network policy

UniFi remains the inter-VLAN enforcement point. Same-subnet Corosync traffic is
not routed and must be trusted at the switch layer.

| Source | Destination | Policy |
|---|---|---|
| Admin device group on Main | PVE nodes on VLAN 20 | Allow TCP 8006; allow TCP 22 only from operator devices |
| Other Main clients | VLAN 20 | Deny |
| IoT and Guest VLANs | VLAN 20 and VLAN 25 | Deny |
| PVE nodes on VLAN 20 | Internet | Allow DNS, NTP, HTTPS/HTTP for package and image retrieval |
| PVE node storage IPs | `10.32.25.5` | Allow NFSv4 TCP 2049 |
| PVE node storage IPs | Other PVE storage IPs | Allow host-to-host migration and cluster-link traffic |
| PVE guests on VLAN 21 | VLAN 20 and VLAN 25 | Deny by default |
| PVE guests on VLAN 21 | Kubernetes VLAN 30 and `admin-prod` LB prefix | Deny by default; add per-service exceptions only |
| PVE guests on VLAN 21 | `services-prod` LB prefix | Allow |
| Kubernetes VLAN 30 | PVE guests on VLAN 21 | Deny by default; add per-service exceptions only |
| PVE guests on VLAN 21 | Internet | Allow, subject to normal DNS and egress controls |
| Admin device group on Main | PVE guests on VLAN 21 | Allow for administration; narrow later if guest classes justify it |

Start with UniFi policy and guest OS firewalls. Do not enable a cluster-wide PVE
firewall policy until local console recovery is proven on all three nodes; a
mistyped PVE rule can lock out every node even when the router rules are sound.

Applied on UniFi Network 10.5.67 on 2026-08-27: `Block Workloads to Protected Networks` denies VLAN 21 to the `Infra Mgmt`, `Storage`, and `K8s Prod` network objects; `Block Workloads to Admin Prod Routed` denies VLAN 21 to `10.32.130.0/24`; and `Block K8s Prod to Workloads` denies the reverse Kubernetes-to-guest path. The BGP-routed `admin-prod` prefix is classified in UniFi's `External` destination zone even though its next hops are internal Kubernetes nodes, so that rule must not be converted to an `Internal`-zone IP rule without a new negative test. The disposable acceptance guest proved the protected paths blocked while `services-prod`, local DNS, Internet HTTP, and Main-to-guest administration remained available.

## Installation baseline

### Firmware and burn-in gate

Before forming a cluster:

1. Before any firmware change or partitioning, boot a trusted live environment
   and record the BIOS state plus `nvme id-ctrl`, `nvme id-ns`, `nvme
   smart-log`, and `smartctl` output. Verify each drive's model, firmware,
   capacity, and logical sector size. Keep serials, MACs, and raw captures in
   ignored/private inventory; track only sanitized facts.
2. Compare every candidate drive with the remaining
   [SN770 qualification](../sn770-zfs-qualification.md) matrix. Preserve any
   old-firmware specimen, control drive, and same-host baseline needed by that
   work; do not change its firmware, sector format, or host firmware until the
   qualification hold is explicitly released.
3. After the hold is cleared, normalize each host's BIOS configuration against the [PVE node bootstrap runbook](../runbooks/pve-node-bootstrap.md). Record the installed BIOS revision before changing settings. A BIOS firmware flash is a separate, product-ID-specific operation and is not implied by loading defaults. For each released drive, use the vendor-supported tool to check and, when applicable, apply an NVMe firmware update one drive at a time; record the sanitized before/after revision.
4. While no valuable data exists, run memory and CPU burn-in plus a destructive
   full-device write/read verification and sustained mixed/sync NVMe I/O. A
   quick SMART pass is insufficient. Reject a drive or host that logs any NVMe
   reset, timeout, PCIe/AER error, media error, or capacity change.
5. Install only `pve-sbx-1` using the committed local Proxmox VE 9.2-1 submenu and verified assets. Keep the default supported kernel; do not opt into a test kernel to make the RTL8125 work unless the stable kernel demonstrably fails. Use the [PVE node bootstrap runbook](../runbooks/pve-node-bootstrap.md) for the GLKVM and netboot.xyz control path.
6. Record `lspci -nnk`, interface names, private MAC inventory, and `ethtool`
   link and driver/firmware data. The RTL8125 must hold a negotiated 2.5 Gb/s
   link and sustain `iperf3` without resets or PCIe/AER errors.
7. Exercise the installed storage with a disposable VM: sustained I/O, backup
   to `backups-pve-sbx`, restore, and—after node 2 exists—local-disk migration over
   VLAN 25. Recheck the kernel and NVMe error logs after every test.
8. Test a reboot and complete power removal. Confirm both NIC identities and
   all LVM volumes return unchanged.

Only after node 1 passes should the same baseline move to nodes 2 and 3. This
keeps any NIC, NVMe, or kernel compatibility problem outside the cluster while
it is being diagnosed.

### PVE install choices

- Install Proxmox VE 9.2-1 x86-64 through the committed local submenu and verified `9.2-1-4bbcc809` asset set. A later installer release first requires a reviewed menu, checksum, plan, and runbook update.
- Install in UEFI mode onto the single verified local NVMe using ext4 and the
  installer's default LVM layout. Keep the default `local-lvm` thin pool rather
  than inventing fixed sizes until the installer shows the actual available
  capacity.
- Keep the installer-created `local` storage for ISO/template/snippet staging
  and `local-lvm` for guest disks. Record the final root, swap, thin-pool data,
  thin-pool metadata, and free-space sizes in the inventory.
- Set the final FQDN, management address, gateway, timezone, and DNS during the
  install or before cluster formation.
- Install `intel-microcode`; keep Debian's firmware repository enabled.
- Use `pve-no-subscription`; disable the subscription-gated enterprise source
  and do not enable `pvetest`. A Community subscription can be evaluated later
  for access to the more heavily tested enterprise repository; it does not
  change the update workflow.
- Do not install Ceph packages.
- Preserve at least 6 GiB of effective host headroom and tune only from
  measured contention.

### Cluster formation

1. Finish and validate both networks on all three standalone nodes.
2. Create `pve-sbx` on node 1 with VLAN 20 as Corosync link 0 and VLAN 25 as link 1.
3. Join node 2, verify quorum and both links, then join node 3.
4. Confirm expected votes `3`, quorum `2`, and that removal of either physical
   link does not destroy quorum.
5. Do not add a QDevice or change expected votes. A symmetric three-node
   cluster already has the correct quorum model. Before HA resources are
   configured, losing one link should retain quorum without rebooting; once HA
   services and resources are enabled, the watchdog provides self-fencing when
   a node loses cluster communication.
6. Configure `10.32.25.0/24` as the secure migration network.
7. Create a non-root `kelchm@pve` administrator and API-token service identity;
   retain `root@pam` as local-console break glass.
8. Require MFA for the human administrator. Keep the root password and recovery
   material in the existing out-of-band credential store, not in this repo.
9. Configure per-node ACME DNS-01 certificates for the exact node FQDNs using a
   PVE-owned Cloudflare credential. Do not reuse Kubernetes cert-manager.
10. Optional Kanidm OIDC may be added for convenience, but a Kubernetes-hosted
   identity service can never be the only PVE login path.

## Storage design

### Why no ZFS on the expected drives

The expected drives are 1 TB WD_BLACK SN770s. A read-only query of all three
live Kubernetes nodes on 2026-08-15 reported that model, firmware
`731100WD`, and 512-byte logical sectors. The PVE drives still require direct
inventory because "same drives" has not yet been verified on those machines.

The available failure reports are sufficient to reject ZFS as the launch
default on SN770s. The long-running OpenZFS discussion documents SN770
controllers disappearing from PCIe under sustained ZFS send, resilver, and
other I/O; recovery can require a full power cycle. Reports are hardware- and
workload-dependent, and some SN770 installations are stable, so this is not
proof that every drive will fail. It is nevertheless a poor risk for the only
disk in a virtualization host.

There is no accepted universal fix. Disabling APST/ASPM, forcing slower PCIe
generations, changing ZFS queue limits, throttling I/O, changing schedulers, and
increasing the NVMe host-memory buffer have produced mixed or temporary results.
The vendor's later HMB firmware advisory lists the 2 TB SN770, not this 1 TB
model. The current 512-byte logical sector format also avoids one later-reported
4K-sector trigger, but does not prove that the controller is safe under ZFS.

A destructive [qualification run](../sn770-zfs-qualification.md) on one 1 TB
specimen subsequently completed 18 full 700 GiB sends and 8 full 700 GiB scrubs
without a controller failure. It covered 0/32/128/200 MiB HMB screens, the
historical 32 MiB/eight-descriptor allocator, and both 512-byte and 4096-byte
logical sectors. This is a strong negative result for that specimen and host,
not a model-wide qualification or a firmware A/B; the multi-specimen launch
decision therefore remains unchanged.

Do not build ZFS and suppress or weaken scrubs to keep it alive: routine
integrity work cannot be treated as an optional dangerous workload. Use ext4
and LVM-thin for these drives, retain tested backups, and watch the kernel log
for NVMe resets under all storage stacks. LVM-thin avoids the best-documented
ZFS workload trigger; it cannot repair a defective controller or firmware.

If local ZFS replication becomes a requirement, replace the SN770s with drives
whose exact model, firmware, host platform, and sustained ZFS scrub/send workload
pass a destructive burn-in. Reconsider ZFS only after that hardware change.

### Why no Ceph

Ceph is not part of this design. Each node has one NVMe for both the host and
guests, 32 GB RAM, a four-core CPU, and one 2.5 GbE data link. Proxmox
recommends a dedicated 10+ GbE public Ceph network and warns that recovery
traffic can disrupt latency-sensitive Corosync; NVMe deployments may justify
even faster links. Local LVM-thin plus tested external backups fits the
available hardware with fewer resource and recovery-path constraints.

### Runtime tiers

| Storage ID | Backing | Content | Policy |
|---|---|---|---|
| `local-lvm` | Each node's LVM thin pool | VM disks, LXC root disks | Default fast runtime tier; node-local, not HA |
| `local` | Each node's root filesystem | Temporary ISO/template staging, snippets | Do not store backups here |
| `library-pve` | Synology NFS at `10.32.25.5` | ISO images, CT templates, snippets, import staging | Shared convenience tier; no default guest disks |
| `backups-pve-sbx` | Synology NFS at `10.32.25.5` | `vzdump` backups only | Recovery tier, mounted on all nodes |
| `guests-pve-sbx` | Optional Synology NFS at `10.32.25.5` | Selected VM disks | Shared HA tier; enable only after benchmark and failure test |

Create distinct Synology exports:

```text
/volume1/library-pve          # live; platform-specific, reusable across PVE clusters
/volume1/backups-pve-sbx      # live; cluster-specific recovery data
/volume1/guests-pve-sbx       # optional; not created
```

Use NFSv4.1, scope export access to `10.32.25.21-.23`, and map root to an account that can write the backup directory. Do not use `soft` mounts. Synology snapshots and its offsite backup policy must include `backups-pve-sbx`; a backup on the same NAS is independent of PVE host loss, not of NAS loss.

Use each DSM shared-folder leaf as the corresponding PVE storage ID so the same external resource has one canonical name across both systems. Backup job names describe their schedule and purpose separately; the live cluster job is `daily-backups`.

Create `guests-pve-sbx` only if a real HA guest justifies it. Use `qcow2` there so
the directory/NFS backend can provide snapshots and clones. First benchmark
latency, sustained mixed I/O, backup contention, and VM behavior during an NFS
interruption. Shared NFS lets a surviving PVE node see the guest disk without a
copy, but makes the Synology and storage network part of the guest's runtime
failure domain. Do not put ordinary guests there for convenience.

Synology iSCSI plus shared LVM is not the initial design: it adds SAN-style
failure handling while normal shared LVM lacks the snapshot and clone behavior
available with `qcow2` on NFS. Btrfs remains a PVE technology-preview backend.

### Availability classes

PVE quorum and HA management do not make `local-lvm` shared. A controlled
maintenance move can copy a local guest disk over VLAN 25, but after an abrupt
node loss that disk is unavailable until the node returns. Recovery is restore
from `backups-pve-sbx`, not an automatic HA restart.

| Class | Storage and placement | Failure behavior |
|---|---|---|
| `shared-ha` | `guests-pve-sbx`; no passthrough or node-local resources | PVE HA may restart it on a surviving node; NAS and VLAN 25 must be healthy |
| `local` | `local-lvm`; migratable virtual hardware | Migrate with disk copy for planned work; node failure requires node recovery or restore |
| `pinned` | Local devices, PCI/USB passthrough, or node-specific data | Bound to one node; backup is the recovery path |
| `disposable` | `local-lvm` | Recreate from OpenTofu/cloud-init |

Do not add a guest to HA merely because it can be migrated. Require
`shared-ha`, confirm that every target node can provide each mapped resource,
and test power-loss failover. Do not enable PVE 9.2 dynamic balancing initially;
deliberate placement is more useful than background movement on three small
symmetric nodes.

### Capacity guardrails

Each 32 GB host keeps at least 6 GiB free for PVE, migration, and transient
load, leaving about 26 GiB for guests. If every running guest must remain online
through a planned one-node evacuation, aggregate running memory across the
cluster stays at or below about 52 GiB (roughly 17 GiB per host before
evacuation), with placement that leaves destination headroom. A denser 18-20
GiB-per-host baseline explicitly accepts stopping lower-priority guests during
maintenance. For abrupt failover, count surviving non-HA guests plus every
`shared-ha` guest eligible to restart—not only the HA subset—against the same
two-host ceiling. CPU is intentionally overcommittable, but sustained host CPU
above 60% would leave limited headroom for evacuation.

Thin-provisioned storage is not capacity. Alert when either `local-lvm` data or
metadata reaches 70%, stop routine growth at 80%, and treat 90% as an incident.
Monitor both percentages with `lvs`; metadata exhaustion is independently
dangerous. Never count a snapshot as a backup.

## Backup and disaster recovery

### Initial backup policy

- Cluster job `daily-backups`: all non-disposable guests to `backups-pve-sbx` at **05:00
  America/New_York daily**.
- Mode: snapshot where the guest/storage supports it; zstd compression.
- Retention: `keep-last=3`, `keep-daily=7`, `keep-weekly=4`, `keep-monthly=6`.
- Install and enable the QEMU guest agent in every VM image; freeze-capable
  applications still own their application-consistency hooks.
- Stagger any high-write guest or exclude/re-schedule it if the one cluster job
  saturates VLAN 25.
- Send backup failures through a PVE-owned notification target that remains
  useful during a Kubernetes outage.

After every material cluster configuration change and weekly thereafter,
capture at least `/var/lib/pve-cluster/config.db`, `/etc/network/interfaces`,
`/etc/hosts`, repository configuration, storage configuration, and the hardware
inventory into an encrypted archive outside the nodes. Secrets in this archive
make it recovery material, not a Git artifact.

### Restore gates

Before hosting anything important:

1. Restore a VM under a new VMID from `backups-pve-sbx` and boot it without its
   production NIC connected.
2. Restore an LXC container if LXC will be used.
3. If `guests-pve-sbx` is enabled, interrupt one compute node and prove that a test
   `shared-ha` guest restarts safely on another node. Separately interrupt NFS
   in a controlled test and document the guest and PVE behavior.
4. Write the elapsed restore time and observed throughput into the runbook.

The initial VM restore gate passed on 2026-08-27. VM 300 backed up from node 2 to `backups-pve-sbx` in 14 seconds as a 334 MB Zstandard archive, restored on node 3 as VM 301, and booted with its NIC removed. The root filesystem and QEMU guest agent passed inspection; the isolated restore and source acceptance VM were then destroyed, while the backup archive was retained.

Whole-cluster recovery favors rebuilding from documented state: reinstall one
node with its recorded identity, recreate the intended cluster and storage
configuration, attach `backups-pve-sbx`, and restore the critical guests. Restoring
the pmxcfs database is an additional recovery option, not the default procedure.

### Future PBS trigger

PBS is deferred until full `vzdump` archives are materially too slow or large,
or restore-time requirements justify deduplication, incremental transfer, and
live restore. When added, it runs on independent hardware at the reserved
`pbs`/`pbs-storage` addresses—not as the only backup server inside the cluster
it protects.

## Guest conventions

### VMID allocation

| VMID | Use |
|---|---|
| `100-199` | Platform and recovery-critical guests |
| `200-299` | Persistent application/service guests |
| `300-399` | Lab and test guests |
| `400-499` | Disposable automation/CI guests |
| `9000-9099` | Cloud-init templates; never boot directly |

Initial artifacts:

- `9000`: current Debian stable generic cloud image;
- `9001`: current Ubuntu LTS cloud image; and
- `300`: `pve-smoke-1`, the disposable acceptance VM used to validate provisioning, local-disk migration, backup, restore, and VLAN isolation; destroyed after the checks passed, with its VZDump archive retained.

Every managed guest has a description, owner/purpose, class tag from the HA
table, backup policy, source module path, and DNS/IP allocation. Use VirtIO SCSI
single, discard, SSD emulation, QEMU guest agent, and serial console by default.
Use CPU type `x86-64-v2-AES` (or the lowest common tested model) for migratable
VMs; `host` is allowed only for explicitly pinned guests because it weakens
migration portability.

Prefer VMs for security boundaries, custom kernels, or appliances. Use
unprivileged LXC only for small Linux services whose host-kernel coupling is
acceptable. No privileged container is a default.

## Repository and apply model

Keep PVE code in this repository because one operator owns the network, DNS,
NAS, Kubernetes, and virtualization estate. Preserve independence through the
execution boundary:

```text
home-lab/
├── proxmox/
│   ├── README.md                         # live scope, checks, backup, restore, and recovery entrypoints
│   ├── capture-host-config.sh            # encrypted off-node host and cluster capture
│   └── host/
│       ├── pve-no-subscription-popup     # exact-match UI patch
│       └── 99-pve-no-subscription-popup  # package hook
└── .private/pve-sbx/
    ├── inventory.yaml                    # ignored hardware, identity, BIOS, and commissioning evidence
    └── recovery/                         # ignored age-encrypted configuration archives
```

The host-operations tree and ignored inventory are live. Add `proxmox/cloud-init/` and `proxmox/tofu/` only when phase 4 guest provisioning starts.

### Ownership boundary

| Layer | Owner at launch |
|---|---|
| BIOS, installer, final host networking, cluster create/join | Manual runbook + `.private/pve-sbx/inventory.yaml` evidence |
| PVE package repository and node maintenance | Manual node-by-node runbook |
| Cluster-wide storage, backup job, users/roles, notification target | Manual bootstrap, then import into OpenTofu only where provider behavior is reliable |
| VM/LXC definitions, tags, pools, cloud-init, and placement | OpenTofu using `bpg/proxmox` |
| In-guest service configuration | cloud-init plus the guest's native declarative mechanism; add Ansible only when repeated configuration drift earns it |

Flux, tofu-controller, Kubernetes-hosted CI runners, and Kanidm are not required
apply dependencies. Pull requests run formatting, validation, policy checks,
and a saved plan. CI is plan-only; reviewed changes are applied explicitly from
an admin workstation with a least-privilege PVE API token.

The provider may need SSH for operations the PVE API cannot perform. If used,
it gets a dedicated restricted service identity; root SSH is not the permanent
provider credential.

Pin the exact provider version and read its migration notes before phase 4. At
design time the newly named `proxmox_vm` resource is explicitly experimental.
Use the provider's mature VM resource for the pinned version, or delay that
resource migration until the replacement is declared stable. Import only
resources the provider can round trip without a perpetual diff.

### State and secrets

Before the first OpenTofu-managed guest, provision the independent S3 endpoint
already planned for the Synology and create a versioned `tofu-state` bucket.
First prove that the selected S3 gateway correctly implements conditional
object creation (`If-None-Match`) because OpenTofu's native lockfile depends on
it; do not use that backend if this test fails. Use the native lockfile and
OpenTofu state/plan encryption only after that gate passes. The
encryption key and S3/PVE credentials are supplied at runtime from outside Git;
SOPS-encrypted source files may transport credentials, but decrypted values and
`.terraform/` never enter the repository.

Losing the state-encryption key makes the state unrecoverable. Back up that key
separately and test access before relying on it. PVE recovery must still be
possible from guest backups and this plan if both the state service and state
file are lost.

### Why no Ansible initially

Ansible is not rejected; it is deferred. Three stable hosts do not justify
turning Debian package state and `/etc/pve` into a broad mutable playbook before
the manual baseline is understood. Add it when at least one of these is true:

- a rebuilt node takes too many repetitive, error-prone steps;
- host repository, package, kernel-module, or sysctl drift appears in practice;
- a fourth host makes manual consistency tedious; or
- the exact same change must repeatedly cross all nodes.

Even then, Ansible may own host bootstrap and audits; it does not replace
OpenTofu's guest lifecycle or justify unattended rolling reboots.

## Updates and maintenance

PVE's update model remains mutable Debian hosts. The operating model is a
repeatable rolling procedure with explicit health gates. PDM and Ansible may
reduce individual steps, but neither changes that lifecycle model.

### Cadence

- Routine window: **second Saturday of each month at 10:00
  America/New_York**.
- Security fixes with meaningful exposure: review promptly and schedule within
  72 hours when warranted.
- Major PVE release: wait for the first point release and a clean upgrade check;
  treat it as a separate change, not part of the monthly patch window.
- Follow the stable/default kernel. Do not opt into test kernels cluster-wide.

### One-node rolling procedure

For nodes 1, 2, then 3—never two at once:

1. Confirm cluster quorum/Corosync, both physical links, PVE storage, NVMe
   health, LVM-thin data/metadata use, and the previous night's backups are
   healthy. Search the kernel log for NVMe resets, timeouts, and PCIe errors.
2. Confirm the remaining two nodes have memory and CPU headroom for the guests.
3. Put the node in HA maintenance mode and migrate or shut down every guest.
4. Run `apt update`, review the package/change list, then `apt full-upgrade`.
5. Reboot for kernel, microcode, systemd, QEMU, or PVE stack changes; when
   uncertain, reboot while the node is already empty.
6. Require clean boot, expected kernel/PVE versions, quorum, both Corosync links,
   `pvesm status`, healthy thin-pool data and metadata percentages, storage
   mounts, networking, and a smoke guest before leaving maintenance mode.
7. Confirm migrated guests and any shared-NFS guests are healthy, then let the
   cluster settle before touching the next node.

Stop the window on the first unexplained regression. Do not upgrade around a
failed node merely because quorum still exists. The initial PVE 9.2 dynamic
balancer remains disabled; maintenance evacuation is explicit and observable.

### PDM position

PDM 1.1 can centralize visibility and launch package updates through connected
PVE nodes, but it does not provide a Talos-style immutable, health-gated rolling
upgrade controller. Reserve `10.32.20.20`, but deploy PDM only after a second
PVE/PBS estate exists or its cross-system status view saves real time. It is not
a prerequisite for this one cluster.

## Observability and notifications

PVE must report failures without the Kubernetes observability stack:

- configure a direct PVE notification target for backup, fencing, package,
  storage, and SMART/NVMe failures;
- enable SMART/NVMe monitoring, alert on controller resets and timeouts, and
  alert on both LVM-thin data and metadata utilization;
- make UPS/power-loss notification and auto-start behavior explicit when a UPS
  is added; and
- retain seven days of local logs at minimum.

Kubernetes may scrape a read-only PVE exporter and display PVE metrics in
Grafana, but that is a convenience view. Alert delivery and the PVE UI remain
usable when Kubernetes is completely unavailable.

## Rollout phases and gates

| Phase | Work | Exit gate |
|---|---|---|
| 0 — network | Apply the `pve-guest-trunk` and VLAN 20 access switch profiles; add IP reservations, DNS, and firewall intent | Admin reaches reserved node IPs; VLAN 21 guests cannot reach VLANs 20/25/30 |
| 1 — one-node proof | BIOS/firmware, install node 1, RTL8125/NVMe burn-in, final bridges | Stable 1/2.5 GbE links across reboot and sustained load |
| 2 — cluster | Install nodes 2/3, form `pve-sbx`, configure redundant Corosync and migration network | Three votes; either NIC can fail without losing quorum; migration uses VLAN 25 |
| 3 — storage/recovery | Add NFS exports, backup job, config capture and restore drill | A disconnected restored VM boots; RTO and throughput recorded |
| 4 — guest IaC | Independent S3 state, OpenTofu encryption/provider, templates and smoke VM | A plan/apply/re-apply is clean; destroy/recreate works without Kubernetes |
| 5 — optional HA proof | Benchmark `guests-pve-sbx`; place one test guest there, enable HA, and pull power on its node | Guest restarts safely; NFS interruption behavior and NAS dependency are documented |
| 6 — operations | Execute a complete node-by-node update window | All three nodes updated with quorum, backups, storage and guests healthy |

Do not place an irreplaceable workload on PVE until phases 0-4 pass. Do not call
a workload HA unless the optional phase 5 passes with that workload class.

As of 2026-08-27, phases 0 and 2 have passed. Phase 3 proved backup and isolated restore functionality but remains open because restore RTO and throughput were not recorded. Phase 1 remains blocked on the zero-AER storage criterion despite clean higher-level I/O, and phase 4 has not started. No irreplaceable workload is cleared for placement.

## Deferred decisions with explicit triggers

| Deferred item | Revisit when |
|---|---|
| Ansible host automation | Rebuild/drift/repetition triggers in the Ansible section occur |
| Proxmox Backup Server | Full backups or restores no longer meet space/RTO goals |
| Proxmox Datacenter Manager | A second PVE/PBS system exists or centralized status is independently valuable |
| Shared runtime NFS storage | A guest needs HA restart more than it needs independence from NAS or VLAN 25 failure |
| Local ZFS and PVE replication | The SN770s are replaced by drives that pass sustained ZFS scrub/send burn-in on these exact hosts |
| Additional guest VLANs | Trust classes cannot be safely expressed with guest firewalls and VLAN 21 policy |
| 10 GbE | Measured migration/backup windows or a future storage design justify it |
| Ceph | Different hardware provides dedicated OSDs, substantially more RAM, and dedicated 10+ GbE networking |

## Primary references

- [Proxmox VE Administration Guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf) — cluster links, migration network, storage, replication, HA, backup, repositories, and node maintenance.
- [OpenZFS SN770 hardware discussion](https://github.com/openzfs/zfs/discussions/14793) — first-hand controller-drop reports, attempted mitigations, and mixed results across drives and hosts.
- [Linux NVMe host-memory-buffer thread](https://www.spinics.net/lists/kernel/msg4339024.html) — the kernel-side SN770 HMB investigation; improvements did not establish a complete fix.
- [SanDisk HMB firmware advisory](https://support-en.sandisk.com/app/answers/detailweb/a_id/51469) — official update scope; it lists the 2 TB SN770 rather than the 1 TB model used here.
- [Proxmox VE 9.2 release](https://proxmox.com/en/about/company-details/press-releases/proxmox-virtual-environment-9-2) — planning baseline and dynamic load balancer context.
- [OpenTofu S3 backend](https://opentofu.org/docs/language/settings/backends/s3/) — native lockfile and bucket-versioning guidance.
- [OpenTofu state and plan encryption](https://opentofu.org/docs/language/state/encryption/) — client-side protection and key-recovery caveats.
- [`bpg/proxmox` provider documentation](https://bpg.sh/docs/) — selected guest-IaC provider; pin and test the exact version during phase 4.
- [External PVE repository patterns](../other-labs.md#proxmox-ownership-and-repository-boundaries) — evidence for the same-repo/separate-execution boundary.
