# Infrastructure Architecture

> Current-state reference for how the lab is wired and why. Forward-looking
> design (the planned PVE cluster and deferred work) lives in
> [roadmap.md](roadmap.md).

## Overview

Homelab with two deliberately separate compute environments:

- **Prod**: Bare-metal Talos Kubernetes cluster on 3x HP EliteDesk 705 G4 mini PCs
- **Virtualization** (future): Three-node Proxmox VE cluster on 3x HP EliteDesk 800 G3 mini PCs

Goals: container-native production workloads, a general-purpose VM/LXC lab,
Git-managed IaC, strong control-plane separation, and shared physical network
and storage infrastructure without shared operational dependencies.

## Hardware

| Role | Hardware | Specs |
|---|---|---|
| Prod cluster nodes (3x) | HP EliteDesk 705 G4 | Ryzen 5 2400GE, 64GB RAM, 1TB NVMe (WD_BLACK SN770), 1GbE + 2.5GbE |
| PVE cluster nodes (3x, future) | HP EliteDesk 800 G3 | Intel i5-6500T, 32GB RAM, 1TB NVMe (expected WD_BLACK SN770; verify before install), onboard 1GbE + PCIe Realtek RTL8125 2.5GbE |
| NAS | Synology DS1821+ | Ryzen V1500B, 6x 14TB Exos X16, 32GB RAM, 2x SFP+ + 4x 1GbE |

## Cluster Architecture

**Prod Talos cluster:**

- 3 nodes, all combined control-plane + worker (`allowSchedulingOnControlPlanes: true`)
- HA etcd across all three nodes
- Cilium CNI with kube-proxy replacement; **BGP control plane** advertises Service LB IPs as /32s to UniFi (no MetalLB; L2 announcements retired)
- Longhorn for replicated block storage on NVMe
- NFS from Synology for bulk storage
- Flux for GitOps

**Virtualization environment (future):** independent three-node `pve-lab`
cluster. PVE management/Corosync uses VLAN 20, storage/migration uses VLAN 25,
and guests use VLAN 31. It has no dependency on Flux or the Kubernetes control
plane. See the [detailed PVE cluster plan](plans/20260814-pve-cluster.md).

## VLAN Layout

| VLAN | Name | Subnet | Purpose |
|---|---|---|---|
| 1 | Default | 10.32.1.0/24 | UniFi management |
| 5 | Cameras | 10.32.5.0/24 | Existing |
| 10 | Main | 10.32.10.0/24 | Trusted household devices |
| 20 | Lab Infra | 10.32.20.0/24 | Classic mgmt planes for non-Talos tenants |
| 25 | Lab Storage | 10.32.25.0/24 | NFS/iSCSI to Synology |
| 30 | Lab Prod | 10.32.30.0/24 | Prod cluster compute (nodes + API VIP only) |
| 31 | Lab Virtualization | 10.32.31.0/24 | PVE guest network (future) |
| 90 | IoT | 10.32.90.0/24 | Existing |
| 99 | Guest | 10.32.99.0/24 | Existing |

VLAN 40 (Lab Services) is being retired — it existed only to host node subinterfaces required by Cilium L2 announcements. Under BGP, LB IPs are routed (not bridged) so no node interface on the pool subnet is needed. The legacy L2 pools and node subinterfaces remain in place pending a final maintenance window.

**Firewall principles:**

- Lab Virtualization ↔ Lab Prod: deny by default (environments isolated)
- Main → Lab Infra: allow from admin devices only
- Main (admin devices) → Lab Prod: allow on 50000/tcp (talosctl) + 6443/tcp (Kube API) — Talos consolidates its mgmt plane onto VLAN 30; mTLS enforces isolation
- PVE management/storage → Lab Storage: scoped to host-to-host and Synology NFS traffic
- Lab Virtualization → Lab Infra and Lab Storage: deny by default
- Lab Prod and Lab Virtualization → Internet: allow (image pulls, updates)
- Per-pool LB rules: see [LB Pool Allocation](#lb-pool-allocation)

## IP Addressing Convention

### Third-octet partitioning

```
10.32.0-99.X     VLAN subnets (third octet = VLAN ID)
10.32.100-254.X  LB pool prefixes (BGP-advertised)

Within 100-254:
  Hundreds digit  Always 1 (200+ reserved for future expansion)
  Tens digit      Policy class: 3=admin, 4=services, 5=shared, 6-9=future
  Units digit     Kubernetes cluster index: 0=prod, 1-9=reserved
```

So `10.32.130.0/24` = admin-prod and `10.32.150.0/24` = shared-prod.
The `*1` pool prefixes remain reserved for a possible future Kubernetes cluster;
PVE guests use addresses directly on VLAN 31 and do not advertise BGP LB pools.

### /24 skeleton

The same skeleton applies to both VLAN /24s and LB pool /24s, with complementary regions populated:

```
.1         Anchor       Router for VLAN; primary Traefik for pool
.2-.10     Specials     Cross-VLAN device anchors / API VIPs (VLAN);
                        secondary infra services / mnemonic-IP slots (pool)
.11-.19    Primary      Cluster nodes (VLAN); unused (pool)
.20-.29    Expansion    Reserved nodes (VLAN); unused (pool)
.30-.99    Secondary    Unused (VLAN); per-service IPs (pool)
.100-.254  Reserved     DHCP scope where VLAN class permits
```

Reserved sub-slot: `.8` = primary cluster API VIP (k8s mnemonic), used in compute VLANs. Storage VLAN uses `.2-.10` for storage providers.

Node numbering is 1-indexed (`k8s-prod-1` = `.11`, not `.10`).

### Cluster identity rule

> **Ones digit = within-cluster index. Tens digit varies by VLAN class.**
>
> - Compute/management VLAN: tens digit always `1` — primary nodes range
> - Storage VLAN (shared-resource class): tens digit = cluster's storage decade (cluster #1 → `.1X`, #2 → `.2X`, etc.)
> - LB pool: third-octet units digit identifies the Kubernetes cluster (prod=0, `1-9` reserved); host portion is service-slot

Examples:

- Prod k8s node 1 (cluster #1): `10.32.30.11` ↔ `10.32.25.11`
- Future PVE node 1: `10.32.20.11` management ↔ `10.32.25.21` storage/migration
- A future PVE guest may use `10.32.31.50`; it has no corresponding Kubernetes LB-pool address

### Storage VLAN specialization

Storage VLAN bends the skeleton because primary inhabitants are storage *providers*, not cluster nodes, and many clusters can have a presence. The /24 also carries pod-level endpoints for workloads that need a presence on the storage VLAN (Longhorn instance managers via Multus); those use a parallel allocation in the `.1XX` range with the same per-cluster decade structure.

| Range | Purpose |
|---|---|
| `.2-.10` | Storage providers (NAS units, MinIO, backup appliances) |
| `.11-.19` | Cluster #1 host NICs (prod) |
| `.20-.29` | PVE cluster host NICs (`.21-.23` planned) |
| `.30-.99` | Clusters #3-#9 host NICs (one decade each) |
| `.100-.127` | Reserved |
| `.128/28` | Cluster #1 storage-pod IPs (prod, 16 IPs) |
| `.144/28` | Reserved for a future Kubernetes cluster; PVE does not use pod IPs |
| `.160/28` | Cluster #3 storage-pod IPs (16 IPs) |
| `.176/28` | Cluster #4 storage-pod IPs (16 IPs) |
| `.192-.254` | Reserved |

**Reading rule:** Pod-level endpoints occupy `.128/26` (`.128-.191`), sub-allocated as one /28 per cluster — see table above. Unlike host IPs, the "tens digit = cluster" decode does **not** apply to pod IPs; the per-cluster /28 is the source of truth.

**Why CIDR for pods, decimal for hosts?** Host IPs are statically configured per-node and only humans ever read them — decimal alignment pays for itself in readability. Pod IPs are pool-allocated by Whereabouts and read by ACLs (NFS export rules, future firewall rules), both of which think in CIDR. Per-cluster /28 means a single rule scopes to every storage-VLAN pod for that cluster, instead of two /29s or nine /32s. The two address classes have different audiences and earn different schemes.

## Prod Cluster IP Allocation

**Lab Prod VLAN (30) — compute-only:**

```
10.32.30.1        gateway-prod             Router interface
10.32.30.8        k8s-prod                 Kubernetes API VIP (Talos vipController)
10.32.30.11-.13   k8s-prod-{1,2,3}         Cluster nodes (1GbE NIC); also BGP source IPs
10.32.30.14-.29   (reserved for future cluster nodes)
```

API VIP is managed by the Talos `vipController` (GARP-based at the machine-config layer), independent of Cilium's service-LB and of BGP convergence. Cluster API reachability does not depend on BGP being healthy.

**Lab Storage VLAN (25):**

```
10.32.25.1         gateway-storage           Router interface
10.32.25.5         nas-storage               Synology (SFP+ interface)
10.32.25.6-.10     (reserved for future storage providers)
10.32.25.11-.13    k8s-prod-{1,2,3}-storage  Cluster #1 host NICs (2.5GbE)
10.32.25.14-.19    (reserved for cluster #1 host expansion)
10.32.25.21-.23    pve-lab-{1,2,3}-storage  Future PVE storage/migration NICs (RTL8125 2.5GbE)
10.32.25.24-.29    (reserved for PVE host expansion)
10.32.25.30-.99    (reserved for clusters #3-#9 host NICs)
10.32.25.128-.143  cluster #1 storage-pod range (/28; longhorn-im-prod-{1,2,3} float here)
10.32.25.144-.159  (reserved for a future Kubernetes storage-pod range; unused by PVE)
10.32.25.160-.175  (reserved for cluster #3 storage-pod range)
10.32.25.176-.191  (reserved for cluster #4 storage-pod range)
```

**Lab Infra VLAN (20):**

```
10.32.20.1        gateway-infra            Router interface
10.32.20.5        nas                      Synology admin interface
10.32.20.10       pdm                      Reserved; PDM not initially deployed
10.32.20.11-.13   pve-lab-{1,2,3}          Future PVE management/Corosync addresses
10.32.20.14-.19   (reserved for PVE host expansion)
10.32.20.20       pikvm                    (future)
10.32.20.25       pbs                      Reserved for an independent future PBS appliance
10.32.20.30-.99   (static device assignments — switches, APs, future infra appliances)
```

Talos nodes do NOT have IPs on Lab Infra. Talos has no classic management plane — `talosctl` and `kubectl` (both mTLS) are the entire management surface, and run over VLAN 30 alongside workload traffic. Network-level isolation is replaced by cryptographic isolation. Lab Infra exists for tenants that *do* need a classic mgmt plane.

## LB Pool Allocation

LB pools are **not** VLAN inhabitants — they're routed prefixes. Cilium IPAM hands out IPs from a pool; Cilium BGP advertises each allocated VIP as a /32 to UniFi with the speakers' node IPs as next-hops. A pool's main job is firewall scoping: each pool maps to a policy class.

Three policy classes:

| Class | Pool (prod) | Reachable from | Use cases |
|---|---|---|---|
| **admin** | `admin-prod` (10.32.130.0/24) | VLAN 10 admin devices only | Operator UIs via Traefik admin gateway, k8s-gateway DNS |
| **services** | `services-prod` (10.32.140.0/24) | VLAN 10 (Main) | Household-facing apps (Traefik services gateway, per-service IPs) |
| **shared** | `shared-prod` (10.32.150.0/24) | All client VLANs (per-IP+port) | Cluster-wide shared services like DNS or NTP — not currently allocated |

The cluster-index-1 pools (`10.32.131.0/24`, `10.32.141.0/24`) remain
unallocated for a possible future Kubernetes cluster. They are not PVE guest
networks.

### admin-prod — 10.32.130.0/24

```
.1     traefik-admin            Operator UIs (Longhorn, Grafana, OpenObserve, …) via HTTPRoute;
                                Traefik middleware adds auth where the app lacks native.
.2     k8s-gateway              In-cluster authoritative DNS for home.kelch.io
                                (UniFi gateway forwards the zone to this IP).
.3-.10 (reserved infra)
.30-.99 (per-service admin IPs — rare; admin gateway is the default)
```

### services-prod — 10.32.140.0/24

```
.1     traefik-services         Household apps without mature native auth via HTTPRoute.
.30-.99 per-service IPs:
  .50  jellyfin                 Native auth, dedicated IP   (example, future)
  .51  nextcloud                Native auth, dedicated IP   (example, future)
  .52  home-assistant           Native auth, dedicated IP   (example, future)
  ...  Non-HTTP household services (MQTT brokers, game servers) also live here.
```

### shared-prod — 10.32.150.0/24 (future)

Reserved for cluster-wide services that need port-scoped reachability from untrusted VLANs (DNS, NTP). Add carve-out firewall rules per allocated IP+port — not pool-wide port allows.

### Firewall posture per pool

Configured in UniFi (no committable artifact lives in this repo; intent in [`network/unifi/README.md`](../network/unifi/README.md)):

```
VLAN 10 Main  → admin-prod          : allow (admin device group only)
VLAN 10 Main  → services-prod       : allow
VLAN 90 IoT   → admin-prod          : deny
VLAN 90 IoT   → services-prod       : deny
VLAN 99 Guest → admin-prod          : deny
VLAN 99 Guest → services-prod       : deny
VLAN 90/99    → shared-prod/X 53/udp+tcp : per-tenant allow (when shared-prod gains a tenant)
```

## Service categorization & exposure

Every Service falls into one of three buckets; the bucket determines exposure:

| Bucket | Exposure | Examples |
|---|---|---|
| Admin / control-plane HTTP | HTTPRoute on admin Traefik gateway, regardless of native-auth maturity | Longhorn UI, Grafana, Prometheus, Alertmanager, kubernetes-dashboard, OpenObserve UI |
| Household HTTP with mature native auth | Per-service LB IP from `services-prod`, own DNS A record, TLS terminated by app or per-service ingress | Jellyfin, Nextcloud, Home Assistant |
| Non-HTTP | Per-service LB IP, port-scoped firewall rules | MQTT, NTP, game servers |

Operator surfaces stay behind admin Traefik even when they have native auth — centralizing gives consistent IP allowlisting, future SSO, and uniform security headers/middleware. Native auth alone isn't sufficient to bypass that.

## Network Interface Assignments (per 705 G4 node)

- **1GbE NIC**: Lab Prod (VLAN 30) — node mgmt, Kube API, pod network, BGP source
- **2.5GbE NIC**: Lab Storage (VLAN 25, tagged) — NFS/iSCSI to Synology
- Default route via Lab Prod interface only; Storage interface is same-subnet only

(VLAN 40 subinterface remains plumbed pending teardown but binds nothing.)

## DNS Plan

**Domain:** `home.kelch.io` (owned). UniFi gateway forwards the zone to `k8s-gateway` (10.32.130.2) which serves records derived from in-cluster `HTTPRoute` and `Service` resources at TTL=1.

**Structure:** Fully flat — all hostnames directly under `home.kelch.io`. Environment/role info lives in the hostname prefix, not in DNS hierarchy. Keeps wildcard certs simple (Let's Encrypt wildcards only cover one level).

```
# Cluster identity
k8s-prod.home.kelch.io              10.32.30.8
k8s-prod-{1,2,3}.home.kelch.io      10.32.30.{11,12,13}
k8s-prod-{1,2,3}-storage.home.kelch.io  10.32.25.{11,12,13}
pve-lab-{1,2,3}.home.kelch.io       10.32.20.{11,12,13}  (future)
pve-lab-{1,2,3}-storage.home.kelch.io  10.32.25.{21,22,23}  (future)

# Shared infrastructure
nas.home.kelch.io                   10.32.20.5
nas-storage.home.kelch.io           10.32.25.5
gateway.home.kelch.io               10.32.1.1
gateway-infra.home.kelch.io         10.32.20.1
gateway-storage.home.kelch.io       10.32.25.1
pdm.home.kelch.io                   10.32.20.10    (reserved)
pbs.home.kelch.io                   10.32.20.25    (reserved)
pbs-storage.home.kelch.io           10.32.25.7     (reserved)
s3-storage.home.kelch.io            10.32.25.6     (planned separately)

# Wildcard for Traefik-fronted household services
*.home.kelch.io                     10.32.140.1    (services-prod primary gateway)

# Admin gateway tenants (HTTPRoute)
longhorn.home.kelch.io              10.32.130.1
grafana.home.kelch.io               10.32.130.1
o11y.home.kelch.io                  10.32.130.1
…etc                                10.32.130.1

# Per-service household IPs (when allocated)
jellyfin.home.kelch.io              10.32.140.50
nextcloud.home.kelch.io             10.32.140.51

# A PVE-hosted duplicate uses -pve
foo-pve.home.kelch.io               10.32.31.50
```

**Naming rules:**

- Hostnames describe what things are, not where they live on the network. VLAN is never encoded.
- Cross-environment duplicates: the PVE version takes a `-pve` suffix. No
  suffix continues to mean the primary/production instance.

**Certs:** Kubernetes uses cert-manager with DNS-01 for
`*.home.kelch.io`. PVE will issue exact per-node certificates through its own
ACME DNS-01 integration, so its UI remains valid when Kubernetes is down.

## BGP

- Prod cluster ASN: **65020**. UniFi gateway ASN: **65000**.
- Each Talos node runs a Cilium BGP speaker peering with UniFi over its VLAN 30 interface (3 sessions total).
- Allocated Service VIPs are advertised as exact `/32` routes; UniFi installs ECMP across the speakers currently advertising them.

**Safety controls (mandatory at peer-up):**

- Per-neighbor prefix-list filter: accept only `/32` routes covered by the cluster's own pool prefixes (FRR: `permit <pool>/24 ge 32 le 32`)
- Per-neighbor max-prefix limit sized to expected VIP count (currently 64)
- BGP MD5 password per session
- UniFi FRR config maintained as a versioned artifact in [`network/unifi/frr.conf`](../network/unifi/frr.conf)

**`externalTrafficPolicy` defaults:**

- Default `Cluster` for Traefik gateways and most Services — operationally simple, stable across pod rescheduling.
- Override to `Local` only when source-IP preservation matters (per-source rate limiting, geo-IP, log analytics tracking real client IPs). Pin replica placement when using `Local`.

PVE does not participate in this BGP control plane. VLAN 31 guests use ordinary
routed addresses behind the UniFi gateway.

## Storage Strategy

- **Longhorn on NVMe**: dedicated user volume per node mounted at `/var/mnt/longhorn` (~890 GiB on the 1 TB SN770, xfs); 3-replica for critical PVCs (databases, stateful apps), 2-replica default. Replica engine ↔ replica engine traffic rides VLAN 25 (2.5GbE storage NIC) via Multus + bridge CNI. Each node carries a Linux bridge `br-storage` (configured per-node in Talos `machine.network`) with `enp6s0` as its only slave; the host's `10.32.25.X/24` IP lives on the bridge. Longhorn's `storage-network` setting points at a NetworkAttachmentDefinition that attaches an `lhnet1` veth from each instance-manager pod into `br-storage`, with the pod IP coming from the Whereabouts pool `.128/28` (see [Storage VLAN specialization](#storage-vlan-specialization)). Bridge sits host and pods on one L2 broadcast domain, which is required so the host's `iscsiadm` can reach the same-node engine's iSCSI target — macvlan and ipvlan L2 both break this with kernel-level host-to-same-host-pod isolation. Cutover runbook at [`docs/runbooks/longhorn-storage-network-cutover.md`](runbooks/longhorn-storage-network-cutover.md).
- **NFS from Synology**: bulk storage via `csi-driver-nfs` (media libraries, *arr content, Nextcloud data, anything large and sequential)
- **Rule of thumb**: Longhorn for default Helm chart PVCs (Postgres, Redis, Grafana); NFS for bulk sequential data

### Disk layout (per node)

The 1 TB NVMe is partitioned by Talos into the standard system partitions plus a dedicated Longhorn user volume (Talos `UserVolumeConfig`):

```
nvme0n1p1   2.2 GB    EFI         (Talos default)
nvme0n1p2   1 MB      META        (Talos default)
nvme0n1p3   105 MB    STATE       (Talos default, holds machine config)
nvme0n1p4   100 GiB   EPHEMERAL   (/var, capped via VolumeConfig)
nvme0n1p5   ~890 GiB  u-longhorn  (/var/mnt/longhorn, xfs, Longhorn defaultDataPath)
```

Capping EPHEMERAL prevents container-image churn and pod logs from competing with Longhorn for space; the dedicated `u-longhorn` partition makes capacity planning explicit. Patches live in `talos/patches/global/volume-ephemeral.yaml` and `user-volume-longhorn.yaml`.

## GitOps / Tooling Stack

- **talhelper** — Talos machine config generation
- **sops + age** — Secrets encryption in git
- **Flux** — GitOps reconciliation
- **Helm + Kustomize** — Workload packaging
- **Cilium** — CNI with kube-proxy replacement, BGP control plane, Gateway API support
- **Traefik** — Gateway API implementation (admin / services / public instances)
- **cert-manager** — Automated TLS via Let's Encrypt DNS-01
- **Longhorn** — Replicated block storage
- **Velero** — Cluster backup to NFS target on Synology

## Repository Structure

```
home-lab/
├── talos/
│   ├── talconfig.yaml          # talhelper input
│   ├── talsecret.sops.yaml     # encrypted secrets
│   └── patches/                # per-node patches
├── kubernetes/
│   ├── flux/                   # flux bootstrap config
│   ├── apps/                   # workloads + cluster infra (cilium, cert-manager, etc.)
│   └── components/             # shared kustomize components
├── network/
│   └── unifi/                  # versioned UniFi-side artifacts (FRR config, firewall intent)
├── proxmox/                    # planned independent PVE inventory + OpenTofu guest IaC
└── docs/
    ├── architecture.md         # this file
    ├── roadmap.md              # forward-looking design (PVE, deferred work)
    ├── runbooks/              # operational procedures
    └── plans/                 # design docs written ahead of changes
```

The full repo layout is in the [README](../README.md). The
bootstrap sequence is documented below under [Bootstrap Sequence](#bootstrap-sequence)
rather than a separate file. `talos/clusterconfig/` (generated by talhelper) is
gitignored.

## Bootstrap Sequence

1. **Network prep**: Configure DHCP reservations for all 6 NICs across the 3 Talos nodes; configure switch ports as trunks carrying VLANs 25 and 30; verify Synology has an interface on VLAN 25; configure UniFi BGP per [`network/unifi/`](../network/unifi/).
2. **Repo + tooling**: Install `talosctl`, `talhelper`, `kubectl`, `flux`, `sops`, `age`, `helm`, `kustomize` locally. Create git repo, generate age key, set up `.sops.yaml`.
3. **Talos config**: Write `talconfig.yaml`, generate secrets with `talhelper gensecret`, encrypt with sops, commit.
4. **Boot nodes**: Flash Talos ISO (from [factory.talos.dev](https://factory.talos.dev) with `iscsi-tools` and `util-linux-tools` extensions for Longhorn). Boot all three nodes from USB.
5. **Apply configs**: `talhelper gencommand apply --extra-flags="--insecure" | bash`
6. **Bootstrap cluster**: `talhelper gencommand bootstrap | bash` then `talhelper gencommand kubeconfig | bash`
7. **Install Cilium**: Via Helm with `kubeProxyReplacement: true` and `bgpControlPlane.enabled: true`; nodes go `Ready` and BGP sessions establish.
8. **Bootstrap Flux**: Point at git repo; from this point everything is GitOps-managed.
9. **Deploy infra**: cert-manager, Gateway API CRDs, Longhorn.
10. **Deploy first app** end-to-end to validate the full loop.

## Key Design Decisions (and why)

- **Bare-metal Talos for production, separate PVE for general virtualization**:
  Kubernetes keeps its immutable declarative host model and PVE remains an
  independently recoverable VM/LXC environment. Neither control plane hosts or
  applies the other.
- **3-node combined CP+worker**: Best hardware utilization at homelab scale; etcd HA with 3 nodes; `allowSchedulingOnControlPlanes: true` is idiomatic.
- **PVE on the EliteDesk 800 G3 nodes (future)**: A general-purpose virtualization
  cluster is more useful than duplicating the production Talos topology. Host
  updates stay deliberate and node-by-node; OpenTofu manages guests without
  making Kubernetes an execution dependency. See the
  [draft plan](plans/20260814-pve-cluster.md).
- **BGP for LB delivery, Traefik for HTTP+auth aggregation**: Different layers. BGP handles packet delivery and ECMP; Traefik aggregates TLS + auth for apps that lack native versions. Cilium Gateway API will displace Traefik when Cilium implements GEP-1494 (external auth filter) — until then Traefik's middleware story is irreducible for auth-less app UIs.
- **Three pool classes (admin / services / shared)**: pool == firewall policy class. Shared exists specifically to let cluster-wide services like DNS take per-IP+port carve-outs from untrusted VLANs without diluting the admin/services posture.
- **Per-service IPs for native-auth household apps**: BGP makes IPs cheap; offloading from Traefik reduces middleware sprawl and lets each app own its own TLS path via cert-manager.
- **Admin gateway is the default for operator surfaces regardless of native auth**: native auth solves the login screen but not consistent IP allowlisting, future SSO, or uniform security headers. Centralize that policy in Traefik admin.
- **VLAN 30 retained as compute-only**: pod egress identity and management-surface blast radius still justify a dedicated Kubernetes compute VLAN even when it holds only nodes + API VIP.
- **API VIPs managed by Talos `vipController`, not Cilium service-LB**: Talos handles VIP failover via GARP at the machine-config layer. Cluster API reachability is therefore independent of Cilium's service-LB and BGP convergence — important during BGP outages.
- **Flat DNS namespace**: Hostname prefixes encode role/environment; DNS hierarchy would duplicate that info and complicate wildcard certs.
- **`.8` for Kubernetes API VIP**: Mnemonic for k8s-prod at `10.32.30.8`; PVE has no cluster VIP and does not consume `10.32.31.8`.
- **Pool slot convention starts at `.1`**: vestigial `.30` boundary from the L2-announcements era has no meaning under BGP.

Deferred and forward-looking design decisions are tracked in [roadmap.md](roadmap.md).
