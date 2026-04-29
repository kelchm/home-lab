# BGP-Based Networking Plan (Proposal)

## Status

In progress. This document captures the target architecture for migrating from Cilium L2 announcements to Cilium BGP, including the unified IP convention that becomes possible under BGP. Will be merged into `architecture.md` when executed.

Initial migration scope is `admin-prod` and `services-prod` only. AdGuard is deferred (UniFi gateway is the LAN resolver and forwards the `home.kelch.io` zone to `k8s-gateway`), so `shared-prod` has no immediate tenant and migration step 11 is future work. The pool design is preserved so a later AdGuard or other shared-VLAN service can land cleanly.

## Trigger conditions

Migrate when both:

- Recent Traefik migration (commits `b81f5c4`, `7741b0d`) has settled — no active gateway issues
- OpenObserve provides alerting on Cilium BGP session state ("session down > 60s")

Migrate sooner if a second cluster is imminent (sandbox build within ~2 months) — see [Two-cluster topology](#two-cluster-topology) for why.

## Why BGP

Cilium L2 announcements require LB IPs to live on the same broadcast domain as a node interface. This forces:

- A "Lab Services" VLAN whose only purpose is hosting node subinterfaces no service binds to
- LB pools constrained to fit inside compute VLAN /24s
- IP allocation tightly coupled to VLAN topology

Cilium BGP advertises *allocated Service VIPs as exact /32 routes* — not whole pool CIDRs. Pools function as IPAM domains and firewall-rule scopes, not as routes. UniFi learns one /32 per allocated Service VIP, with ECMP across the nodes currently advertising it. Consequences:

- LB pools become abstract /24s used for IPAM and firewall scoping, not VLAN inhabitants
- The plumbing-only services VLAN can be deleted
- Per-service IPs become trivial — useful for non-HTTP services and apps with native auth
- Real ECMP across nodes (vs. "whichever responds to ARP first")

What BGP does *not* replace: Traefik. BGP handles packet delivery; Traefik handles HTTP multiplexing, TLS termination, and auth wrapping for apps that lack native auth. The two operate at different layers.

What BGP does *not* program: the in-cluster datapath. Cilium's BGP control plane advertises routes outward; pod-to-service forwarding inside the cluster is unchanged from today.

## Service categorization

Every service falls into one of three buckets, and the bucket determines exposure:

| Bucket | Exposure | Examples |
|---|---|---|
| Admin / control-plane HTTP | HTTPRoute on admin Traefik gateway, regardless of whether the app has native auth | Longhorn UI, Grafana, Prometheus, Alertmanager, kubernetes-dashboard, OpenObserve UI, AdGuard UI |
| Household HTTP with mature native auth | Per-service LB IP, own DNS A record, TLS terminated by app or by per-service ingress | Jellyfin, Nextcloud, Home Assistant |
| Non-HTTP | Per-service LB IP, port-scoped firewall rules | AdGuard DNS, MQTT, NTP, game servers |

Decision rule per service:

- **Operator / control-plane surfaces** → admin Traefik gateway by default. Native auth alone is not sufficient to bypass; centralizing here gives consistent IP allowlisting, future SSO, and uniform security headers/middleware.
- **Household-facing apps with mature native auth** → per-service LB IP is fine. Decide TLS termination per app: app-native where clean (it consumes a cert-manager `Certificate` Secret directly), per-service ingress where the app doesn't terminate TLS gracefully.
- **Non-HTTP services** → per-service LB IP, port-scoped firewall rules.

## VLAN table (post-BGP)

| VLAN | Subnet | Purpose |
|---|---|---|
| 1 | 10.32.1.0/24 | UniFi mgmt |
| 5 | 10.32.5.0/24 | Cameras |
| 10 | 10.32.10.0/24 | Main (trusted household) |
| 20 | 10.32.20.0/24 | Lab Infra (mgmt planes for non-Talos tenants) |
| 25 | 10.32.25.0/24 | Lab Storage (NFS/iSCSI) |
| 30 | 10.32.30.0/24 | Lab Prod compute |
| 31 | 10.32.31.0/24 | Lab Sandbox compute (future) |
| 90 | 10.32.90.0/24 | IoT |
| 99 | 10.32.99.0/24 | Guest |

Changes from today: VLAN 40 (Lab Services) deleted. VLAN 30/31 collapse to compute-only (no LB pool window).

## LB pool table

| Pool | Prefix | Reachable from | Notes |
|---|---|---|---|
| `admin-prod` | 10.32.130.0/24 | VLAN 10 admin devices | Operator UIs via Traefik |
| `admin-sandbox` | 10.32.131.0/24 | VLAN 10 admin devices | Sandbox operator UIs |
| `services-prod` | 10.32.140.0/24 | VLAN 10 (Main) | Household-facing apps |
| `services-sandbox` | 10.32.141.0/24 | VLAN 10 (Main) | Optional — only if sandbox exposes apps |
| `shared-prod` | 10.32.150.0/24 | All client VLANs (port-scoped) | DNS, NTP, etc. |
| `shared-sandbox` | 10.32.151.0/24 | (typically not allocated) | Sandbox shouldn't run cluster-wide DNS |

UniFi firewall posture per pool. Note that `shared-*` allows are *per-IP + per-port tuples*, not pool-wide port allows — a service on `10.32.150.53` does not implicitly authorize anything else in `10.32.150.0/24`.

```
VLAN 10 Main  → admin-*                : allow (admin device group only)
VLAN 10 Main  → services-*             : allow
VLAN 10 Main  → shared-*               : allow
VLAN 90 IoT   → admin-*                : deny
VLAN 90 IoT   → services-*             : deny
VLAN 90 IoT   → 10.32.150.53/32  53/udp+tcp : allow
VLAN 90 IoT   → 10.32.150.123/32 123/udp    : allow
VLAN 90 IoT   → 10.32.150.0/24                 : deny
VLAN 99 Guest → same shape as VLAN 90 (consider whether guests should use internal DNS at all vs. a restricted upstream)
```

## Unified IP conventions

### Cluster-identity rule

Cluster identity is encoded by the rightmost varying digit of the network specifier. The host portion's ones digit is always within-cluster instance index.

| IP | Network specifier | Within-cluster index | Reads as |
|---|---|---|---|
| `10.32.30.11` | VLAN 30 (prod compute) | .11 → node 1 | Prod node 1 |
| `10.32.31.12` | VLAN 31 (sandbox compute) | .12 → node 2 | Sandbox node 2 |
| `10.32.25.11` | VLAN 25 (shared storage) | tens=1 → cluster #1 | Prod node 1 storage interface |
| `10.32.25.21` | VLAN 25 (shared storage) | tens=2 → cluster #2 | Sandbox node 1 storage interface |
| `10.32.140.50` | Pool `services-prod` | .50 → service slot 50 | Prod services slot 50 |
| `10.32.141.50` | Pool `services-sandbox` | .50 → service slot 50 | Sandbox services slot 50 |

### /24 skeleton

```
.1         Anchor       Router for VLAN; primary Traefik for pool
.2-.10     Specials     Cross-VLAN device anchors / API VIPs (VLAN);
                        secondary infra services / mnemonic-IP slots (pool)
.11-.19    Primary      Cluster nodes (VLAN); unused (pool)
.20-.29    Expansion    Reserved nodes (VLAN); unused (pool)
.30-.99    Secondary    Unused (VLAN); per-service IPs (pool)
.100-.254  Reserved     DHCP scope where VLAN class permits
```

VLAN /24s populate `.1-.29`. LB pool /24s populate `.1-.10` and `.30-.99`. Same skeleton, complementary regions used. Reserved slot `.8` = API VIP (k8s mnemonic) inherited from prior convention.

### Third-octet partitioning

```
10.32.0-99.X     VLAN subnets (third octet = VLAN ID)
10.32.100-254.X  LB pool prefixes

Within 100-254:
  Hundreds digit  Always 1 (200+ reserved for future expansion)
  Tens digit      Policy class: 3=admin, 4=services, 5=shared, 6-9=future
  Units digit     Cluster index: 0=prod, 1=sandbox, 2-9=future clusters
```

## Concrete population

### `admin-prod` — 10.32.130.0/24

```
.1     traefik-admin            Operator UIs (Longhorn, Grafana, Prometheus, Alertmanager,
                                kubernetes-dashboard, OpenObserve UI, AdGuard UI, …)
                                via HTTPRoute; auth via Traefik middleware where the app
                                lacks native.
.2     k8s-gateway              In-cluster authoritative DNS for home.kelch.io
                                (used by AdGuard as upstream forwarder for the zone).
.3-.10 (reserved infra)
.30-.99 (per-service admin IPs — rare)
```

### `services-prod` — 10.32.140.0/24

```
.1     traefik-services         Household apps without mature native auth via HTTPRoute.
.30-.99 per-service IPs:
  .50  jellyfin                 Native auth, dedicated IP
  .51  nextcloud                Native auth, dedicated IP
  .52  home-assistant           Native auth, dedicated IP
  ...  Non-HTTP household services (MQTT brokers, game servers) also live here.
```

### `shared-prod` — 10.32.150.0/24

```
.30-.99 per-service IPs with port-scoped firewall carve-outs from untrusted VLANs:
  .53  adguard-dns              UDP/TCP 53, reachable from VLAN 10/90/99
  .123 ntp                      (if you ever run a local NTP server)
```

## DNS plan

Flat namespace under `home.kelch.io` preserved. Per-service A records become viable for apps with their own LB IPs.

```
# Compute / cluster identity (unchanged)
k8s-prod.home.kelch.io              10.32.30.8
k8s-prod-{1,2,3}.home.kelch.io      10.32.30.{11,12,13}
sbx-k8s.home.kelch.io               10.32.31.8     (future)

# Wildcard for Traefik-fronted services
*.home.kelch.io                     10.32.140.1    (services-prod primary gateway)

# Admin gateway
traefik-admin.home.kelch.io         10.32.130.1   (debug anchor for gateway IP)
longhorn.home.kelch.io              10.32.130.1    (HTTPRoute)
grafana.home.kelch.io               10.32.130.1    (HTTPRoute)

# Per-service IPs (household apps with native auth)
jellyfin.home.kelch.io              10.32.140.50
nextcloud.home.kelch.io             10.32.140.51
home.home.kelch.io                  10.32.140.52   (Home Assistant)

# Note: grafana / openobserve / longhorn / etc. are admin-pool tenants (above), not per-service IPs

# Cross-cluster duplicates use -sbx suffix
jellyfin-sbx.home.kelch.io          10.32.141.50

# Shared services (port-scoped, informational A records)
dns.home.kelch.io                   10.32.150.53   (AdGuard DNS)
```

Cross-cluster duplicate naming rule: when the same app exists in both clusters, sandbox version takes a `-sbx` suffix. No suffix means prod.

Wildcard cert `*.home.kelch.io` issued via cert-manager DNS-01 in each cluster independently. Both certs valid simultaneously — no coordination needed.

## Two-cluster topology

When sandbox lands as a second Talos cluster:

- Each cluster is its own AS (prod 65020, sandbox 65021), peering with UniFi (65000)
- Each cluster allocates from only its own pool prefixes and advertises allocated VIPs as /32s within those prefixes
- UniFi config: prefix-list filter per neighbor (sandbox cannot advertise into prod's CIDR space), max-prefix limit per neighbor sized to the expected VIP count (e.g., 64 prod, 32 sandbox)
- Storage VLAN 25 shared as today; both clusters present per the unified cluster-identity rule
- API VIPs are managed by the Talos `vipController` inside each compute VLAN — independent of Cilium service-LB and of BGP convergence. Cluster API reachability does not depend on BGP being healthy.
- Each node peers with UniFi over its compute-VLAN interface (1GbE), not its storage interface. Total session count: 3 prod + 3 sandbox = 6 sessions on UniFi.

### `externalTrafficPolicy` defaults

- Default `Cluster` for Traefik gateways and most services — operationally simple, stable reachability across pod rescheduling.
- Override to `Local` only when source-IP preservation matters: per-source rate limiting, geo-IP, log analytics tracking real client IPs, or anything where SNAT to the node IP would mislead the application. Pin replica placement when using `Local`.

Failure isolation: sandbox BGP issues cannot blackhole prod traffic when prefix-list scoping is in place.

## BGP safety controls (mandatory at peer-up)

- Prefix-list filter per neighbor: accept only `/32` routes whose covering prefix matches the cluster's assigned pools (FRR syntax: `accept 10.32.130.0/24 le 32`, etc., then `deny any`)
- Max-prefix limit per neighbor sized to expected VIP count, not to pool count
- BGP MD5 password per session
- OpenObserve alert on session state: "Cilium BGP session down > 60s"
- UniFi FRR config maintained as a versioned artifact (committed alongside `talconfig.yaml`); test config reload behavior in isolation before relying on it during a service cutover

## Migration sequencing

1. ~~Prerequisite: alerting on Cilium BGP session state in OpenObserve~~ — **skipped intentionally**; revisit once BGP is steady-state and OpenObserve is the obvious place for it.
2. ~~Lower TTLs on records that will move (e.g., wildcard, admin gateway, per-service DNS) to 60–120s, at least one prior TTL window before cutover~~ — **already satisfied**: `k8s-gateway` serves the zone with `ttl: 1` and is the only resolver path that points at LAN LB IPs.
3. Configure UniFi BGP: AS, neighbor entries for prod nodes, prefix-list filters (accept `<pool>/24 le 32`)
4. Apply `CiliumBGPClusterConfig` and `CiliumBGPAdvertisement` — peering establishes, no Service VIPs allocated yet from BGP pools
5. Create `CiliumLoadBalancerIPPool` for `admin-prod` (10.32.130.0/24) alongside existing L2 pool
6. **Synthetic test first**: deploy a disposable echo Service at `10.32.130.99`; validate IPAM allocation, /32 advertisement on UniFi, ECMP path selection, allowed-VLAN reachability, denied-VLAN blocking, and node-reboot withdrawal/reconvergence. Tear down only when all checks pass.
7. Move admin gateway Service to `10.32.130.1`, update DNS, validate end-to-end
8. Move `k8s-gateway` DNS to `10.32.130.2`
9. Create `services-prod` pool, move services Traefik to `10.32.140.1`
10. Migrate one low-risk per-service app (e.g., Jellyfin) to a `services-prod` per-service VIP
11. ~~Create `shared-prod` pool, move AdGuard DNS to `10.32.150.53` only after the per-IP+port firewall rules are proven against IoT/Guest VLANs~~ — **deferred**: no current tenant. Pool design preserved for when AdGuard or another shared-VLAN service lands.
12. Once all Services migrated, remove L2 announcement policy
13. Leave VLAN 40 dormant but in place for at least one stable maintenance window (no service depends on it, but the L2 pool stays available as a rollback target)
14. Remove VLAN 40 from switch trunks, delete from UniFi config, remove VLAN 40 subinterfaces from Talos node configs

### Rollback

Two distinct rollback surfaces:

- **Before any DNS record points at a BGP-only VIP** (steps 1–6, and steps 7+ before the DNS update): revert the BGP CRDs / pool changes; user-visible state is unchanged.
- **After DNS cutover for a Service**: revert is *not* automatic. The new VIP is in a pool that L2 announcements cannot serve. Restore the Service to its previous L2 VIP, restore the DNS record, ensure the L2 announcement policy still claims it, and wait for the lowered TTL to expire. The dormant VLAN 40 / L2 pool from step 13 is the rollback target — do not delete it on the same day services move.

## Out of scope (future considerations)

- **Cilium Gateway API replacing Traefik**: defer until Cilium implements GEP-1494 (external auth filter). Until then, Traefik's middleware story is irreducible for auth-less app UIs.
- **Pod CIDR BGP advertisement**: Cilium can advertise pod IPs via BGP for direct LAN reachability. Not needed for current use cases.

## Decisions captured (and why)

- **BGP for LB delivery, Traefik for HTTP+auth aggregation**: BGP handles packet delivery; Traefik aggregates TLS + auth for apps that lack native versions. Different layers.
- **Three pool classes (admin / services / shared)**: pool == firewall policy class. AdGuard DNS forces the third class because it requires per-VLAN port-scoped access.
- **Per-service IPs for native-auth apps**: BGP makes IPs cheap; offloading from Traefik reduces middleware sprawl and lets each app own its own TLS path via cert-manager.
- **Sandbox as second Talos under BGP**: identical operational model, encoding scheme generalizes via cluster-index digit. Trades VM playground capability for uniformity — accept consciously.
- **Pool slot convention starts at .1**: vestigial `.30` boundary from L2 announcements has no meaning under BGP.
- **API VIPs managed by Talos `vipController`, not by Cilium service-LB**: Talos handles VIP failover via GARP at the machine-config layer. Cluster API reachability is therefore independent of Cilium's service-LB and BGP convergence — important during BGP migration and during BGP outages.
- **Admin gateway is the default for operator surfaces regardless of native auth**: native auth solves the login screen but not consistent IP allowlisting, future SSO, or uniform security headers. Centralizing operator UIs behind admin Traefik keeps that policy in one place. Apps with native auth are released to per-service IPs only when they're household-facing.
- **VLAN 30 retained as compute-only**: pod egress identity, sandbox isolation pathway, and mgmt-surface blast radius still justify a dedicated compute VLAN even when it holds only nodes + API VIP.
