# Homelab Infrastructure Plan

## Overview

Building a container-native homelab with two separate environments:
- **Prod**: Bare-metal Talos Kubernetes cluster on 3x HP EliteDesk 705 G4 mini PCs
- **Sandbox** (future): Proxmox cluster on 3x HP EliteDesk 800 G3 mini PCs for experimentation

Goals: Container-native workloads, GitOps-driven IaC, clear prod/sandbox separation, shared storage and management infrastructure.

## Hardware

| Role | Hardware | Specs |
|------|----------|-------|
| Prod cluster nodes (3x) | HP EliteDesk 705 G4 | Ryzen 5 2400GE, 64GB RAM, 1TB NVMe (WD_BLACK SN770), 1GbE + 2.5GbE |
| Sandbox cluster nodes (3x, future) | HP EliteDesk 800 G3 | Intel i5-6500T, 32GB RAM, 1TB NVMe (SN850), 1GbE + 2.5GbE |
| NAS | Synology DS1821+ | Ryzen V1500B, 6x 14TB Exos X16, 32GB RAM, 2x SFP+ + 4x 1GbE |

## Cluster Architecture

**Prod Talos cluster:**
- 3 nodes, all combined control-plane + worker (`allowSchedulingOnControlPlanes: true`)
- HA etcd across all three nodes
- Cilium for CNI with kube-proxy replacement and L2 announcements (no MetalLB needed)
- Longhorn for replicated block storage on NVMe
- NFS from Synology for bulk storage (media, backups, etc.)
- Flux for GitOps

**Sandbox environment (future):**
- Proxmox cluster on the 800 G3s
- Used for throwaway Talos test clusters, VM experiments, testing upgrades before promoting to prod
- Both environments consume shared Lab Infra and Lab Storage VLANs

## VLAN Layout

| VLAN | Name | Subnet | Purpose |
|------|------|--------|---------|
| 5 | Cameras | 10.32.5.0/24 | Existing |
| 10 | Main | 10.32.10.0/24 | Trusted household devices |
| 20 | Lab Infra | 10.32.20.0/24 | classic mgmt planes (future Proxmox cluster: PVE UI, corosync, SSH) |
| 25 | Lab Storage | 10.32.25.0/24 | NFS/iSCSI traffic to Synology |
| 30 | Lab Prod | 10.32.30.0/24 | Prod cluster compute + node mgmt + internal LB pool |
| 31 | Lab Sandbox | 10.32.31.0/24 | Sandbox compute (reserved for future use) |
| 40 | Lab Services | 10.32.40.0/24 | Household-facing LoadBalancer IPs (DMZ pattern) |
| 90 | IoT | 10.32.90.0/24 | Existing |
| 99 | Guest | 10.32.99.0/24 | Existing |

**Firewall principles:**
- Lab Services → Main: **deny** (DMZ pattern — compromised service can't pivot to household)
- Lab Sandbox ↔ Lab Prod: **deny** (environments isolated)
- Main → Lab Services: allow (household reaches exposed services)
- Main → Lab Infra: allow from admin devices only
- Main (admin devices) → Lab Prod: allow on 50000/tcp (talosctl) + 6443/tcp (Kube API) — Talos consolidates its mgmt plane onto VLAN 30; mTLS enforces isolation
- Lab Sandbox → Lab Storage: scoped/limited (prevent sandbox from nuking prod data)
- Lab Prod and Lab Sandbox → Internet: allow (image pulls, updates)

## IP Addressing Convention

Within each /24:
- `.1` — Gateway
- `.2-.9` — Network infrastructure / reserved
- `.11-.49` — Static assignments (nodes/servers)
- `.50-.99` — Reserved for future static
- `.100-.199` — DHCP pool (where applicable)
- `.200-.250` — LoadBalancer / VIP pool

**Special convention:** Kubernetes API VIP lives at `.8` in each environment's VLAN (cheeky reference to k8s). Node numbering is 1-indexed (`k8s-prod-1 = .11`, not `.10`).

## Prod Cluster IP Allocation

**Lab Prod VLAN (30, 10.32.30.0/24) — node management and internal services:**

```
10.32.30.1        gateway-prod             Gateway router interface
10.32.30.8        k8s-prod                 Kubernetes API VIP (floats across CP nodes)
10.32.30.11       k8s-prod-1               Node 1 (705 G4 #1, 1GbE NIC)
10.32.30.12       k8s-prod-2               Node 2 (705 G4 #2, 1GbE NIC)
10.32.30.13       k8s-prod-3               Node 3 (705 G4 #3, 1GbE NIC)
10.32.30.14-.29   (reserved for future Talos nodes)
10.32.30.30-.49   (reserved for future non-Talos infra)
10.32.30.200-.250 Cilium internal LB pool (admin-facing: Grafana, ArgoCD/Flux UI, Longhorn UI, Hubble)
```

**Lab Storage VLAN (25, 10.32.25.0/24) — Talos secondary interfaces for NFS/iSCSI:**

```
10.32.25.1        gateway-storage          Gateway router interface
10.32.25.5        nas-storage              Synology (SFP+ interface)
10.32.25.11       k8s-prod-1-storage       Node 1 (2.5GbE NIC)
10.32.25.12       k8s-prod-2-storage       Node 2 (2.5GbE NIC)
10.32.25.13       k8s-prod-3-storage       Node 3 (2.5GbE NIC)
10.32.25.14-.29   (reserved for future Talos nodes)
```

Note: Last-octet alignment across Prod and Storage VLANs (`.11 = k8s-prod-1` on both) for at-a-glance identification.

**Lab Services VLAN (40, 10.32.40.0/24) — household-facing services:**

```
10.32.40.1        gateway-services         Gateway router interface
10.32.40.10       ingress                  Ingress controller LB IP (all services via Host header)
10.32.40.11       k8s-prod-1-services      Node 1 VLAN 40 subinterface
10.32.40.12       k8s-prod-2-services      Node 2 VLAN 40 subinterface
10.32.40.13       k8s-prod-3-services      Node 3 VLAN 40 subinterface
10.32.40.14-.29   (reserved for future Talos nodes)
10.32.40.30-.99   (reserved for additional LB IPs if needed)
```

Cilium advertises the LB pool (`.10`+) via L2 announcements. Nodes hold IPs on VLAN 40 because Cilium's native-routing LB delivery needs a connected route on the announcement interface — without one, inbound traffic to LB IPs isn't intercepted before the kernel routing decision and gets forwarded back out the default route. Node IPs on VLAN 40 are plumbing; no service binds to them.

**Lab Infra VLAN (20, 10.32.20.0/24) — shared across environments:**

```
10.32.20.1        gateway-infra            Gateway router interface
10.32.20.5        nas                      Synology admin interface
10.32.20.10-.19   (reserved for future Proxmox cluster mgmt IPs)
10.32.20.20       pikvm                    (future, when added)
10.32.20.30-.39   (reserved for switches, APs)
```

Talos nodes do NOT have IPs on Lab Infra. Talos is designed without a classic management plane — no corosync, no side-channel heartbeat, no separate admin UI, no SSH. `talosctl` and `kubectl` (both mTLS) are the entire management surface, and they run over VLAN 30 alongside workload traffic. Network-level isolation is replaced by cryptographic isolation. Lab Infra exists for tenants that *do* need a classic mgmt plane — the future Proxmox sandbox, hardware admin interfaces, etc.

## Network Interface Assignments (per 705 G4 node)

- **1GbE NIC**: Lab Prod (VLAN 30, untagged or tagged) — node management, Kubernetes API, pod network; Lab Services (VLAN 40, tagged) — for Cilium L2 announcements, no IP assigned
- **2.5GbE NIC**: Lab Storage (VLAN 25, tagged) — NFS/iSCSI to Synology
- Default route via Lab Prod interface only; Storage interface is same-subnet only

## DNS Plan

**Domain:** `home.kelch.io` (owned)

**Structure:** Fully flat — all hostnames directly under `home.kelch.io`. Environment/role info lives in the hostname prefix, not in DNS hierarchy. This keeps wildcard certs simple (one level deep is all Let's Encrypt wildcards cover).

```
# Prod cluster nodes
k8s-prod-1.home.kelch.io            10.32.30.11
k8s-prod-2.home.kelch.io            10.32.30.12
k8s-prod-3.home.kelch.io            10.32.30.13
k8s-prod.home.kelch.io              10.32.30.8

# Prod storage interfaces
k8s-prod-1-storage.home.kelch.io    10.32.25.11
k8s-prod-2-storage.home.kelch.io    10.32.25.12
k8s-prod-3-storage.home.kelch.io    10.32.25.13

# Shared infrastructure
nas.home.kelch.io                   10.32.20.5
nas-storage.home.kelch.io           10.32.25.5
gateway.home.kelch.io               10.32.1.1

# Future sandbox (same pattern)
sbx-pve-01.home.kelch.io            10.32.31.11
sbx-k8s.home.kelch.io               10.32.31.8

# Wildcard for household services (via ingress controller)
*.home.kelch.io                     10.32.40.10
```

**Naming rule:** Hostnames describe what things are, not where they live on the network. VLAN is never encoded in hostnames.

**Certs:** cert-manager with DNS-01 challenge via DNS provider API → wildcard cert for `*.home.kelch.io` covers all services automatically.

## Storage Strategy

- **Longhorn on NVMe**: ~400GB allocated per node; 3-replica for critical PVCs (databases, stateful apps), 2-replica default
- **NFS from Synology**: Bulk storage via `csi-driver-nfs` (media libraries, Jellyfin/*arr content, Nextcloud data, anything large and sequential)
- **Rule of thumb**: Longhorn for default Helm chart PVCs (Postgres, Redis, Grafana); NFS for bulk sequential data

## GitOps / Tooling Stack

- **talhelper** — Talos machine config generation and management
- **sops + age** — Secrets encryption in git
- **Flux** — GitOps reconciliation
- **Helm + Kustomize** — Workload packaging
- **Cilium** — CNI with kube-proxy replacement, L2 announcements for LoadBalancer IPs, Gateway API support
- **cert-manager** — Automated TLS via Let's Encrypt DNS-01
- **Longhorn** — Replicated block storage
- **Velero** — Cluster backup to NFS target on Synology

## Repository Structure

```
homelab/
├── talos/
│   ├── talconfig.yaml          # talhelper input (committed)
│   ├── talsecret.sops.yaml     # encrypted secrets (committed)
│   └── patches/                # per-node patches
├── kubernetes/
│   ├── flux/                   # flux bootstrap config
│   ├── apps/                   # workloads
│   └── infrastructure/         # cluster-level (cilium, cert-manager, longhorn, etc.)
└── docs/
    └── bootstrap.md            # rebuild runbook
```

`clusterconfig/` (generated by talhelper) is gitignored.

## Bootstrap Sequence

1. **Network prep**: Configure DHCP reservations for all 6 NICs across the 3 nodes; configure switch ports as trunks carrying VLANs 25, 30, 40; verify Synology has an interface on VLAN 25.
2. **Repo + tooling**: Install `talosctl`, `talhelper`, `kubectl`, `flux`, `sops`, `age`, `helm`, `kustomize` locally. Create git repo, generate age key, set up `.sops.yaml`.
3. **Talos config**: Write `talconfig.yaml`, generate secrets with `talhelper gensecret`, encrypt with sops, commit.
4. **Boot nodes**: Flash Talos ISO (from [factory.talos.dev](https://factory.talos.dev) with `iscsi-tools` and `util-linux-tools` extensions for Longhorn). Boot all three nodes from USB.
5. **Apply configs**: `talhelper gencommand apply --extra-flags="--insecure" | bash`
6. **Bootstrap cluster**: `talhelper gencommand bootstrap | bash` then `talhelper gencommand kubeconfig | bash`
7. **Install Cilium**: Via Helm, with kube-proxy replacement and L2 announcement configuration; nodes go `Ready`.
8. **Bootstrap Flux**: Point at git repo; from this point everything is GitOps-managed.
9. **Deploy infra**: cert-manager, ingress/Gateway API, Longhorn, external-dns.
10. **Deploy first app** end-to-end to validate the full loop.

## Key Design Decisions (and why)

- **Bare metal Talos over virtualized**: User prefers Talos' modern declarative model; 6 mini PCs is ample hardware for split prod/sandbox; Proxmox UI didn't align with aesthetic preferences.
- **3-node combined CP+worker**: Best hardware utilization at homelab scale; etcd HA with 3 nodes; `allowSchedulingOnControlPlanes: true` is idiomatic for this topology.
- **Separate Proxmox sandbox on 800 G3s (future)**: Gives real staging environment for testing upgrades; hardware asymmetry (Ryzen vs older Intel, 64GB vs 32GB) maps naturally to prod-vs-lab tiering; Proxmox is used only where it shines (VM experimentation) without polluting primary workflow.
- **Shared Lab Infra and Lab Storage VLANs**: One NAS, one set of infra admin interfaces — duplicating these per-environment would be forcing a boundary that doesn't match physical reality.
- **Dedicated Services VLAN (DMZ pattern)**: Exposed services isolated from trusted Main network; compromised service can't pivot into household devices.
- **Flat DNS namespace**: Hostname prefixes encode role/environment; DNS hierarchy would duplicate that info and complicate wildcard certs.
- **`.8` for Kubernetes VIP**: Mnemonic for k8s; consistent across environments (k8s-prod at 10.32.30.8, future sbx-k8s at 10.32.31.8).
