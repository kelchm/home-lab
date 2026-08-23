# Infrastructure Architecture

> Current-state reference for how the cluster is wired and why. Forward-looking
> design (the planned sandbox cluster, deferred work) lives in
> [roadmap.md](roadmap.md).

## Overview

Container-native homelab with two separate environments:

- **Prod**: Bare-metal Talos Kubernetes cluster on 3x HP EliteDesk 705 G4 mini PCs
- **Sandbox** (future): Second Talos cluster on 3x HP EliteDesk 800 G3 mini PCs for experimentation

Goals: container-native workloads, GitOps-driven IaC, clear prod/sandbox separation, shared storage and management infrastructure.

## Hardware

| Role | Hardware | Specs |
|---|---|---|
| Prod cluster nodes (3x) | HP EliteDesk 705 G4 | Ryzen 5 2400GE, 64GB RAM, 1TB NVMe (WD_BLACK SN770), 1GbE + 2.5GbE |
| Sandbox cluster nodes (3x, future) | HP EliteDesk 800 G3 | Intel i5-6500T, 32GB RAM, 1TB NVMe (SN850), 1GbE + 2.5GbE |
| AI compute (2x) | NVIDIA DGX Spark | GB10, 128GB unified memory, 4TB NVMe, 10GbE + ConnectX-7 direct fabric |
| NAS | Synology DS1821+ | Ryzen V1500B, 6x 14TB Exos X16, 32GB RAM, 2x SFP+ + 4x 1GbE |

## Cluster Architecture

**Prod Talos cluster:**

- 3 nodes, all combined control-plane + worker (`allowSchedulingOnControlPlanes: true`)
- HA etcd across all three nodes
- Cilium CNI with kube-proxy replacement; **BGP control plane** advertises Service LB IPs as /32s to UniFi (no MetalLB; L2 announcements retired)
- Longhorn for replicated block storage on NVMe
- NFS from Synology for bulk storage
- Flux for GitOps

**Sandbox environment (future):** second Talos cluster sharing the storage and infra VLANs but a distinct compute VLAN and BGP ASN. See [Two-cluster topology](roadmap.md#two-cluster-topology).

## VLAN Layout

| VLAN | Name | Subnet | Purpose |
|---|---|---|---|
| 1 | Default | 10.32.1.0/24 | UniFi management |
| 5 | Cameras | 10.32.5.0/24 | Existing |
| 10 | Main | 10.32.10.0/24 | Trusted household devices |
| 20 | Infra Mgmt | 10.32.20.0/24 | Classic mgmt planes for non-Talos tenants |
| 21 | Workloads | 10.32.21.0/24 | DGX Sparks and general-purpose workload servers |
| 25 | Storage | 10.32.25.0/24 | NFS/iSCSI to Synology |
| 30 | K8s Prod | 10.32.30.0/24 | Prod cluster compute (nodes + API VIP only) |
| 31 | K8s 2 | 10.32.31.0/24 | Second Kubernetes cluster compute (reserved) |
| 90 | IoT | 10.32.90.0/24 | Existing |
| 99 | Guest | 10.32.99.0/24 | Existing |

VLAN 40 (Lab Services) is being retired — it existed only to host node subinterfaces required by Cilium L2 announcements. Under BGP, LB IPs are routed (not bridged) so no node interface on the pool subnet is needed. The legacy L2 pools and node subinterfaces remain in place pending a final maintenance window.

**Firewall principles:**

- K8s 2 ↔ K8s Prod: deny (environments isolated)
- Main → Infra Mgmt: allow from admin devices only
- Main (admin devices) → K8s Prod: allow on 50000/tcp (talosctl) + 6443/tcp (Kube API) — Talos consolidates its mgmt plane onto VLAN 30; mTLS enforces isolation
- K8s 2 → Storage: scoped/limited (prevent sandbox from nuking prod data)
- K8s Prod and K8s 2 → Internet: allow (image pulls, updates)
- Per-pool LB rules: see [LB Pool Allocation](#lb-pool-allocation)

## IP Addressing Convention

### Third-octet partitioning

```
10.32.0-99.X     VLAN subnets (third octet = VLAN ID)
10.32.100-254.X  LB pool prefixes (BGP-advertised)

Within 100-254:
  Hundreds digit  Always 1 (200+ reserved for future expansion)
  Tens digit      Policy class: 3=admin, 4=services, 5=shared, 6-9=future
  Units digit     Cluster index: 0=prod, 1=sandbox, 2-9=future clusters
```

So `10.32.130.0/24` = admin-prod, `10.32.141.0/24` = services-sandbox, `10.32.150.0/24` = shared-prod, etc. Reading any LB pool prefix tells you policy class and which cluster owns it.

### Non-routed machine fabrics

`198.19.240.0/20` is reserved from the RFC 2544 benchmarking block for closed
machine interconnects. The current DGX fabric uses logical subnets A
(`198.19.240.0/24`) and B (`198.19.241.0/24`). At each endpoint, both paths
share the same ConnectX-7 and QSFP cage; end to end, they share the single DAC.
They are not independent physical rails or failure domains. Fabric prefixes
exist only as connected routes on participating endpoints and are never routed,
advertised, published in DNS, or included in gateway firewall/address objects.
The DGX allocation is recorded in the [bring-up runbook](runbooks/dgx-spark-bringup.md#fabric-address-allocation).

### /24 skeletons

Server VLANs (20, 21, 25, 30, 31) share one skeleton:

```
.1         Gateway      Router interface
.2-.10     Specials     Storage providers / service anchors / API VIP (.8)
.11-.99    Identity     Registered-member octets: tens digit = system, ones digit = member
.100-.199  Local        VLAN-local statics, DHCP reservations, storage-pod pools
.200-.239  DHCP         Dynamic scope where the VLAN class permits
.240-.254  Reserved
```

LB pool /24s use their own layout: `.1` primary Traefik anchor, `.2-.10` secondary infra services / mnemonic-IP slots, `.30-.99` per-service IPs.

Reserved sub-slot: `.8` = primary cluster API VIP (k8s mnemonic), used in compute VLANs.

Member numbering is 1-indexed (`k8s-prod-1` = `.11`, not `.10`); `.X0` is never a member address.

### System identity rule

> **Final octet = `<system decade><member index>`, identical on every VLAN the member touches.**

- Decades allocate to registered systems in commissioning order; the ledger is the [Storage VLAN registry](#storage-vlan-registry) below.
- A system is an addressing group — a Kubernetes cluster, a PVE host cluster, or the standalone-workload class — never a substrate. Kubernetes nodes running as PVE VMs address as Kubernetes members, so a cluster can move between PVE and bare metal without renumbering.
- A member's octet is reserved on the storage VLAN even while it has no storage leg, so adding one later never renumbers.
- Outside the rule: API VIPs (`.8`), LB pools (routed service slots; third-octet units digit = cluster, host portion = service slot), storage-pod /28s, non-routed machine-fabric addresses, and single-homed VLAN-local endpoints (guests, DHCP clients, infra appliances).

Examples:

- `k8s-prod-1` (system 1): `10.32.30.11` ↔ `10.32.25.11`
- `pve-lab-1` (system 2, planned): `10.32.20.21` ↔ `10.32.25.21` — trunks VLAN 21 for guests but holds no address there
- `spark-1` (system 3): `10.32.21.31` ↔ `10.32.25.31`
- Second-cluster node 1 (system 4, reserved): `10.32.31.41` ↔ `10.32.25.41`
- A service slot 50 in services-prod: `10.32.140.50`; same slot in services-sandbox: `10.32.141.50`

Ranges are bookkeeping, not boundaries: the storage VLAN is one L2 domain, and enforcement (firewall rules, DSM export ACLs) always names explicit addresses.

### Storage VLAN registry

The storage VLAN bends the skeleton because its primary inhabitants are storage *providers*, and every system in the lab can have a presence. It is also the identity ledger: allocating a decade here is what registers a system. The /24 additionally carries pod-level endpoints for workloads that need a presence on the storage VLAN (Longhorn instance managers via Multus); those use a parallel allocation in the `.128-.191` range.

| Range | Owner |
|---|---|
| `.2-.10` | Storage providers (NAS `.5`, S3 endpoint `.6`, PBS `.7`) |
| `.11-.19` | System 1 — k8s-prod node NICs |
| `.21-.29` | System 2 — pve-lab host NICs (planned) |
| `.31-.39` | System 3 — workload hosts (DGX Sparks; future standalone servers and storage-attached guests) |
| `.41-.49` | System 4 — second Kubernetes cluster node NICs (reserved) |
| `.51-.99` | Future systems, one decade each |
| `.100-.127` | Reserved |
| `.128/28` | k8s-prod storage-pod IPs (16 IPs) |
| `.144/28` | Second-cluster storage-pod IPs (reserved, 16 IPs) |
| `.160/28`, `.176/28` | Next storage-pod pools |
| `.192-.254` | Reserved |

**Reading rule:** the decade decode applies to host/member IPs only. Pod-level endpoints occupy `.128/26` (`.128-.191`), sub-allocated one /28 per pool in order of need — the /28 sequence is independent of system decades, and this table is the source of truth.

**Why CIDR for pods, decimal for hosts?** Host IPs are statically configured per-node and only humans ever read them — decimal alignment pays for itself in readability. Pod IPs are pool-allocated by Whereabouts and read by ACLs (NFS export rules, future firewall rules), both of which think in CIDR. Per-cluster /28 means a single rule scopes to every storage-VLAN pod for that cluster, instead of two /29s or nine /32s. The two address classes have different audiences and earn different schemes.

**Growth:** if the /24 ever runs short, the storage VLAN widens in place — `10.32.24.0/23` contains `10.32.25.0/24`, so every existing address survives a mask change and new capacity arrives as the whole `10.32.24.x` page (a later `/22` adds `.26/.27`). VLAN IDs 24, 26, and 27 stay unassigned to keep that path open.

## Prod Cluster IP Allocation

**K8s Prod VLAN (30) — compute-only:**

```
10.32.30.1        gateway-prod             Router interface
10.32.30.8        k8s-prod                 Kubernetes API VIP (Talos vipController)
10.32.30.11-.13   k8s-prod-{1,2,3}         Cluster nodes (1GbE NIC); also BGP source IPs
10.32.30.14-.19   (reserved, k8s-prod expansion)
```

API VIP is managed by the Talos `vipController` (GARP-based at the machine-config layer), independent of Cilium's service-LB and of BGP convergence. Cluster API reachability does not depend on BGP being healthy.

**Storage VLAN (25):**

```
10.32.25.1         gateway-storage           Router interface
10.32.25.5         nas-storage               Synology (SFP+ interface)
10.32.25.6         s3-storage                (reserved) NAS S3 endpoint
10.32.25.7         pbs-storage               (reserved) PBS data interface
10.32.25.8-.10     (reserved for future storage providers)
10.32.25.11-.13    k8s-prod-{1,2,3}-storage  System 1 node NICs (2.5GbE)
10.32.25.14-.19    (reserved, k8s-prod expansion)
10.32.25.21-.23    pve-lab-{1,2,3}-storage   System 2 host NICs (planned)
10.32.25.24-.29    (reserved, pve-lab expansion)
10.32.25.31-.32    spark-{1,2}-storage       System 3 workload hosts (10GbE tagged leg)
10.32.25.33-.39    (reserved, workload-host expansion)
10.32.25.41-.43    (reserved, second Kubernetes cluster node NICs)
10.32.25.128-.143  k8s-prod storage-pod range (/28; longhorn-im-prod-{1,2,3} float here)
10.32.25.144-.159  (reserved, second-cluster storage-pod range)
```

**Infra Mgmt VLAN (20):**

```
10.32.20.1        gateway-infra            Router interface
10.32.20.5        nas                      Synology admin interface
10.32.20.7        pbs                      (planned) PBS appliance, aligned with pbs-storage
10.32.20.10       glkvm                    GL-RM1PE KVM (currently a DHCP fixed assignment; prefer on-device static — the recovery console should not depend on DHCP)
10.32.20.20       pdm                      (reserved) Proxmox Datacenter Manager
10.32.20.21-.23   pve-lab-{1,2,3}          (planned) PVE UI/API, SSH, Corosync link 0
10.32.20.24-.29   (reserved, pve-lab expansion)
10.32.20.30-.99   (existing static devices — switches, APs; new static infra devices allocate from .100-.199)
10.32.20.110-.119 (UPS/NUT monitor family, 1-indexed; upsmon clients reference these IPs directly)
10.32.20.111      ups-compute-rack         NixOS Pi, compute-rack UPS monitor
10.32.20.112      ups-office               NixOS Pi, office UPS monitor — Starlink/T-Mobile gateways
```

Talos nodes do NOT have IPs on Infra Mgmt. Talos has no classic management plane — `talosctl` and `kubectl` (both mTLS) are the entire management surface, and run over VLAN 30 alongside workload traffic. Network-level isolation is replaced by cryptographic isolation. Infra Mgmt exists for tenants that *do* need a classic mgmt plane.

## LB Pool Allocation

LB pools are **not** VLAN inhabitants — they're routed prefixes. Cilium IPAM hands out IPs from a pool; Cilium BGP advertises each allocated VIP as a /32 to UniFi with the speakers' node IPs as next-hops. A pool's main job is firewall scoping: each pool maps to a policy class.

Three policy classes:

| Class | Pool (prod) | Reachable from | Use cases |
|---|---|---|---|
| **admin** | `admin-prod` (10.32.130.0/24) | VLAN 10 admin devices only | Operator UIs via Traefik admin gateway, k8s-gateway DNS |
| **services** | `services-prod` (10.32.140.0/24) | VLAN 10 (Main) | Household-facing apps (Traefik services gateway, per-service IPs) |
| **shared** | `shared-prod` (10.32.150.0/24) | All client VLANs (per-IP+port) | Cluster-wide shared services like DNS or NTP — not currently allocated |

Sandbox-side pools (`admin-sandbox` 10.32.131.0/24, `services-sandbox` 10.32.141.0/24) follow the same naming under a future second cluster.

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

- **1GbE NIC**: K8s Prod (VLAN 30) — node mgmt, Kube API, pod network, BGP source
- **2.5GbE NIC**: Storage (VLAN 25 access/native) — enslaved to `br-storage`, which owns the host address for NFS/iSCSI to Synology
- Default route via K8s Prod interface only; Storage interface is same-subnet only

(VLAN 40 subinterface remains plumbed pending teardown but binds nothing.)

## DNS Plan

**Domain:** `home.kelch.io` (owned). UniFi gateway forwards the zone to `k8s-gateway` (10.32.130.2) which serves records derived from in-cluster `HTTPRoute` and `Service` resources at TTL=1.

**Structure:** Fully flat — all hostnames directly under `home.kelch.io`. Environment/role info lives in the hostname prefix, not in DNS hierarchy. Keeps wildcard certs simple (Let's Encrypt wildcards only cover one level).

```
# Cluster identity
k8s-prod.home.kelch.io              10.32.30.8
k8s-prod-{1,2,3}.home.kelch.io      10.32.30.{11,12,13}
k8s-prod-{1,2,3}-storage.home.kelch.io  10.32.25.{11,12,13}
sbx-k8s.home.kelch.io               10.32.31.8     (future)

# Workload hosts (pending)
spark-{1,2}.home.kelch.io           10.32.21.{31,32}
spark-{1,2}-storage.home.kelch.io   10.32.25.{31,32}  (pending)

# Shared infrastructure
nas.home.kelch.io                   10.32.20.5
nas-storage.home.kelch.io           10.32.25.5
gateway.home.kelch.io               10.32.1.1

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

# Cross-cluster duplicates use -sbx suffix
jellyfin-sbx.home.kelch.io          10.32.141.50
```

**Naming rules:**

- Hostnames describe what things are, not where they live on the network. VLAN is never encoded.
- Cross-cluster duplicates: sandbox version takes a `-sbx` suffix. No suffix means prod.

**Certs:** cert-manager with DNS-01 challenge → wildcard `*.home.kelch.io`. Each cluster issues independently; both certs valid simultaneously (no coordination needed).

## BGP

- Prod cluster ASN: **65020**. UniFi gateway ASN: **65000**. Future sandbox ASN: **65021**.
- Each Talos node runs a Cilium BGP speaker peering with UniFi over its VLAN 30 interface (3 sessions per cluster).
- Allocated Service VIPs are advertised as exact `/32` routes; UniFi installs ECMP across the speakers currently advertising them.

**Safety controls (mandatory at peer-up):**

- Per-neighbor prefix-list filter: accept only `/32` routes covered by the cluster's own pool prefixes (FRR: `permit <pool>/24 ge 32 le 32`)
- Per-neighbor max-prefix limit sized to expected VIP count (currently 64)
- BGP MD5 password per session
- UniFi FRR config maintained as a versioned artifact in [`network/unifi/frr.conf`](../network/unifi/frr.conf)

**`externalTrafficPolicy` defaults:**

- Default `Cluster` for Traefik gateways and most Services — operationally simple, stable across pod rescheduling.
- Override to `Local` only when source-IP preservation matters (per-source rate limiting, geo-IP, log analytics tracking real client IPs). Pin replica placement when using `Local`.

> **Two-cluster topology** — the addressing, BGP, and storage schemes above are
> deliberately designed to accommodate a future second cluster. How that lands
> (per-cluster ASNs, prefix-list failure isolation, shared storage VLAN) is
> documented in [roadmap.md](roadmap.md#two-cluster-topology).

## Storage Strategy

- **Longhorn on NVMe**: dedicated user volume per node mounted at `/var/mnt/longhorn` (~890 GiB on the 1 TB SN770, xfs); 3-replica for critical PVCs (databases, stateful apps), 2-replica default. Replica engine ↔ replica engine traffic rides VLAN 25 (2.5GbE storage NIC) via Multus + bridge CNI. Each node carries a Linux bridge `br-storage` (configured per-node in Talos `machine.network`) with `enp6s0` as its only slave; the host's `10.32.25.X/24` IP lives on the bridge. Longhorn's `storage-network` setting points at a NetworkAttachmentDefinition that attaches an `lhnet1` veth from each instance-manager pod into `br-storage`, with the pod IP coming from the Whereabouts pool `.128/28` (see [Storage VLAN registry](#storage-vlan-registry)). Bridge sits host and pods on one L2 broadcast domain, which is required so the host's `iscsiadm` can reach the same-node engine's iSCSI target — macvlan and ipvlan L2 both break this with kernel-level host-to-same-host-pod isolation. Cutover runbook at [`docs/runbooks/longhorn-storage-network-cutover.md`](runbooks/longhorn-storage-network-cutover.md).
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
└── docs/
    ├── architecture.md         # this file
    ├── roadmap.md              # forward-looking design (sandbox, deferred work)
    ├── runbooks/              # operational procedures
    └── plans/                 # design docs written ahead of changes
```

The full repo layout is in the [README](../README.md). The
bootstrap sequence is documented below under [Bootstrap Sequence](#bootstrap-sequence)
rather than a separate file. `talos/clusterconfig/` (generated by talhelper) is
gitignored.

## Bootstrap Sequence

1. **Network prep**: Configure DHCP reservations for all 6 NICs across the 3 nodes; apply the `k8s-node` access-30 profile to each 1GbE port and the `storage` access-25 profile to each 2.5GbE port; verify Synology has an interface on VLAN 25; configure UniFi BGP per [`network/unifi/`](../network/unifi/).
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

- **Bare metal Talos over virtualized**: User prefers Talos' modern declarative model; 6 mini PCs is ample hardware for split prod/sandbox; Proxmox UI didn't align with aesthetic preferences.
- **3-node combined CP+worker**: Best hardware utilization at homelab scale; etcd HA with 3 nodes; `allowSchedulingOnControlPlanes: true` is idiomatic.
- **Sandbox as a second Talos cluster (future)**: Identical operational model to prod; the encoding scheme generalizes via the system-identity decade; prefix-list scoping isolates failure domains. Trades VM playground capability for uniformity — accepted consciously.
- **BGP for LB delivery, Traefik for HTTP+auth aggregation**: Different layers. BGP handles packet delivery and ECMP; Traefik aggregates TLS + auth for apps that lack native versions. Cilium Gateway API will displace Traefik when Cilium implements GEP-1494 (external auth filter) — until then Traefik's middleware story is irreducible for auth-less app UIs.
- **Three pool classes (admin / services / shared)**: pool == firewall policy class. Shared exists specifically to let cluster-wide services like DNS take per-IP+port carve-outs from untrusted VLANs without diluting the admin/services posture.
- **Per-service IPs for native-auth household apps**: BGP makes IPs cheap; offloading from Traefik reduces middleware sprawl and lets each app own its own TLS path via cert-manager.
- **Admin gateway is the default for operator surfaces regardless of native auth**: native auth solves the login screen but not consistent IP allowlisting, future SSO, or uniform security headers. Centralize that policy in Traefik admin.
- **VLAN 30 retained as compute-only**: pod egress identity, sandbox isolation pathway, and mgmt-surface blast radius still justify a dedicated compute VLAN even when it holds only nodes + API VIP.
- **API VIPs managed by Talos `vipController`, not Cilium service-LB**: Talos handles VIP failover via GARP at the machine-config layer. Cluster API reachability is therefore independent of Cilium's service-LB and BGP convergence — important during BGP outages.
- **Flat DNS namespace**: Hostname prefixes encode role/environment; DNS hierarchy would duplicate that info and complicate wildcard certs.
- **`.8` for Kubernetes API VIP**: Mnemonic for k8s; consistent across environments (k8s-prod at 10.32.30.8, future sbx-k8s at 10.32.31.8).
- **One final octet per system member, mirrored across VLANs**: firewall rules, DSM export ACLs, and packet captures correlate to a host without an offset table; decade allocation keeps each system's members contiguous for range-based rules.
- **Pool slot convention starts at `.1`**: vestigial `.30` boundary from the L2-announcements era has no meaning under BGP.

Deferred and forward-looking design decisions are tracked in [roadmap.md](roadmap.md).
