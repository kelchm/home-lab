# Homelab Infrastructure Plan

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
| NAS | Synology DS1821+ | Ryzen V1500B, 6x 14TB Exos X16, 32GB RAM, 2x SFP+ + 4x 1GbE |

## Cluster Architecture

**Prod Talos cluster:**

- 3 nodes, all combined control-plane + worker (`allowSchedulingOnControlPlanes: true`)
- HA etcd across all three nodes
- Cilium CNI with kube-proxy replacement; **BGP control plane** advertises Service LB IPs as /32s to UniFi (no MetalLB; L2 announcements retired)
- Longhorn for replicated block storage on NVMe
- NFS from Synology for bulk storage
- Flux for GitOps

**Sandbox environment (future):** second Talos cluster sharing the storage and infra VLANs but a distinct compute VLAN and BGP ASN. See [Two-cluster topology](#two-cluster-topology).

## VLAN Layout

| VLAN | Name | Subnet | Purpose |
|---|---|---|---|
| 1 | Default | 10.32.1.0/24 | UniFi management |
| 5 | Cameras | 10.32.5.0/24 | Existing |
| 10 | Main | 10.32.10.0/24 | Trusted household devices |
| 20 | Lab Infra | 10.32.20.0/24 | Classic mgmt planes for non-Talos tenants |
| 25 | Lab Storage | 10.32.25.0/24 | NFS/iSCSI to Synology |
| 30 | Lab Prod | 10.32.30.0/24 | Prod cluster compute (nodes + API VIP only) |
| 31 | Lab Sandbox | 10.32.31.0/24 | Sandbox cluster compute (future) |
| 90 | IoT | 10.32.90.0/24 | Existing |
| 99 | Guest | 10.32.99.0/24 | Existing |

VLAN 40 (Lab Services) is being retired — it existed only to host node subinterfaces required by Cilium L2 announcements. Under BGP, LB IPs are routed (not bridged) so no node interface on the pool subnet is needed. The legacy L2 pools and node subinterfaces remain in place pending a final maintenance window.

**Firewall principles:**

- Lab Sandbox ↔ Lab Prod: deny (environments isolated)
- Main → Lab Infra: allow from admin devices only
- Main (admin devices) → Lab Prod: allow on 50000/tcp (talosctl) + 6443/tcp (Kube API) — Talos consolidates its mgmt plane onto VLAN 30; mTLS enforces isolation
- Lab Sandbox → Lab Storage: scoped/limited (prevent sandbox from nuking prod data)
- Lab Prod and Lab Sandbox → Internet: allow (image pulls, updates)
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
> - Compute VLAN (cluster-LB or hypervisor class): tens digit always `1` — primary nodes range
> - Storage VLAN (shared-resource class): tens digit = cluster's storage decade (cluster #1 → `.1X`, #2 → `.2X`, etc.)
> - LB pool: third-octet units digit identifies the cluster (prod=0, sandbox=1, …); host portion is service-slot

Examples:

- Prod k8s node 1 (cluster #1): `10.32.30.11` ↔ `10.32.25.11`
- Future sandbox node 1 (cluster #2): `10.32.31.11` ↔ `10.32.25.21` (+10 offset)
- A service slot 50 in services-prod: `10.32.140.50`; same slot in services-sandbox: `10.32.141.50`

### Storage VLAN specialization

Storage VLAN bends the skeleton because primary inhabitants are storage *providers*, not cluster nodes, and many clusters can have a presence. The /24 also carries pod-level endpoints for workloads that need a presence on the storage VLAN (Longhorn instance managers via Multus); those use a parallel allocation in the `.1XX` range with the same per-cluster decade structure.

| Range | Purpose |
|---|---|
| `.2-.10` | Storage providers (NAS units, MinIO, backup appliances) |
| `.11-.19` | Cluster #1 host NICs (prod) |
| `.20-.29` | Cluster #2 host NICs (future sandbox) |
| `.30-.99` | Clusters #3-#9 host NICs (one decade each) |
| `.100-.109` | Reserved (mirror of provider range) |
| `.110-.119` | Cluster #1 storage-pod IPs (prod) |
| `.120-.129` | Cluster #2 storage-pod IPs (future sandbox) |
| `.130-.199` | Clusters #3-#9 storage-pod IPs (one decade each) |
| `.200-.254` | Reserved |

**Reading rule:** hundreds digit `1` indicates a pod-level endpoint; tens digit still encodes the cluster. Units digit mirrors the host scheme so a pod IP sits at host-IP+100 — `.111` is the storage-pod on the node whose host NIC is `.11`. This composes cleanly for sandbox: cluster #2 host NICs at `.21-.29`, cluster #2 storage-pods at `.121-.129`.

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
10.32.25.21-.29    (reserved for cluster #2 host NICs — future sandbox)
10.32.25.30-.99    (reserved for clusters #3-#9 host NICs)
10.32.25.111-.113  longhorn-im-prod-{1,2,3}  Cluster #1 Longhorn IM pods (Multus on VLAN 25)
10.32.25.114-.119  (reserved for cluster #1 storage-pod expansion)
10.32.25.121-.129  (reserved for cluster #2 storage-pod IPs)
10.32.25.130-.199  (reserved for clusters #3-#9 storage-pod IPs)
```

**Lab Infra VLAN (20):**

```
10.32.20.1        gateway-infra            Router interface
10.32.20.5        nas                      Synology admin interface
10.32.20.10-.19   (reserved for future hypervisor mgmt IPs)
10.32.20.20       pikvm                    (future)
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
k8s-prod-{1,2,3}-storage.h.k.io     10.32.25.{11,12,13}
sbx-k8s.home.kelch.io               10.32.31.8     (future)

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

## Two-cluster topology

When sandbox lands as a second Talos cluster:

- Each cluster is its own AS (prod 65020, sandbox 65021), peering with UniFi (65000)
- Each cluster allocates from only its own pool prefixes; advertises VIPs as `/32`s within those prefixes
- UniFi prefix-list filter per neighbor isolates failure domains: sandbox cannot advertise into prod's CIDR space
- Storage VLAN 25 shared; both clusters present per the cluster-identity rule
- API VIPs continue to be Talos-managed (vipController), independent of BGP
- Total BGP sessions on UniFi: 6 (3 per cluster)

Failure isolation: sandbox BGP issues cannot blackhole prod traffic when prefix-list scoping is in place.

## Storage Strategy

- **Longhorn on NVMe**: dedicated user volume per node mounted at `/var/mnt/longhorn` (~890 GiB on the 1 TB SN770, xfs); 3-replica for critical PVCs (databases, stateful apps), 2-replica default. Replica engine ↔ replica engine traffic rides VLAN 25 (2.5GbE storage NIC) via Multus + bridge CNI. Each node carries a Linux bridge `br-storage` (configured per-node in Talos `machine.network`) with `enp6s0` as its only slave; the host's `10.32.25.X/24` IP lives on the bridge. Longhorn's `storage-network` setting points at a NetworkAttachmentDefinition that attaches an `lhnet1` veth from each instance-manager pod into `br-storage`, with the pod IP coming from the Whereabouts pool `.111-.119` (see [Storage VLAN specialization](#storage-vlan-specialization)). Bridge sits host and pods on one L2 broadcast domain, which is required so the host's `iscsiadm` can reach the same-node engine's iSCSI target — macvlan and ipvlan L2 both break this with kernel-level host-to-same-host-pod isolation. Cutover runbook at [`docs/runbooks/longhorn-storage-network-cutover.md`](runbooks/longhorn-storage-network-cutover.md).
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
    └── bootstrap.md            # rebuild runbook
```

`clusterconfig/` (generated by talhelper) is gitignored.

## Bootstrap Sequence

1. **Network prep**: Configure DHCP reservations for all 6 NICs across the 3 nodes; configure switch ports as trunks carrying VLANs 25 and 30; verify Synology has an interface on VLAN 25; configure UniFi BGP per [`network/unifi/`](../network/unifi/).
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
- **Sandbox as a second Talos cluster (future)**: Identical operational model to prod; the encoding scheme generalizes via cluster-index digit; prefix-list scoping isolates failure domains. Trades VM playground capability for uniformity — accepted consciously.
- **BGP for LB delivery, Traefik for HTTP+auth aggregation**: Different layers. BGP handles packet delivery and ECMP; Traefik aggregates TLS + auth for apps that lack native versions. Cilium Gateway API will displace Traefik when Cilium implements GEP-1494 (external auth filter) — until then Traefik's middleware story is irreducible for auth-less app UIs.
- **Three pool classes (admin / services / shared)**: pool == firewall policy class. Shared exists specifically to let cluster-wide services like DNS take per-IP+port carve-outs from untrusted VLANs without diluting the admin/services posture.
- **Per-service IPs for native-auth household apps**: BGP makes IPs cheap; offloading from Traefik reduces middleware sprawl and lets each app own its own TLS path via cert-manager.
- **Admin gateway is the default for operator surfaces regardless of native auth**: native auth solves the login screen but not consistent IP allowlisting, future SSO, or uniform security headers. Centralize that policy in Traefik admin.
- **VLAN 30 retained as compute-only**: pod egress identity, sandbox isolation pathway, and mgmt-surface blast radius still justify a dedicated compute VLAN even when it holds only nodes + API VIP.
- **API VIPs managed by Talos `vipController`, not Cilium service-LB**: Talos handles VIP failover via GARP at the machine-config layer. Cluster API reachability is therefore independent of Cilium's service-LB and BGP convergence — important during BGP outages.
- **Flat DNS namespace**: Hostname prefixes encode role/environment; DNS hierarchy would duplicate that info and complicate wildcard certs.
- **`.8` for Kubernetes API VIP**: Mnemonic for k8s; consistent across environments (k8s-prod at 10.32.30.8, future sbx-k8s at 10.32.31.8).
- **Pool slot convention starts at `.1`**: vestigial `.30` boundary from the L2-announcements era has no meaning under BGP.

## Out of scope (future considerations)

- **Cilium Gateway API replacing Traefik**: defer until Cilium implements GEP-1494 (external auth filter).
- **Pod CIDR BGP advertisement**: Cilium can advertise pod IPs via BGP for direct LAN reachability. Not needed for current use cases.
- **OpenObserve alerting on BGP session state**: deferred during BGP migration; revisit once OpenObserve is the obvious place for it.
