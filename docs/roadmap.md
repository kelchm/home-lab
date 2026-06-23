# Roadmap

Forward-looking design and deferred work. Current-state reference lives in
[architecture.md](architecture.md); much of the addressing, BGP, and storage
design there is deliberately shaped to make the items below land cleanly.

## Sandbox cluster

A second Talos cluster for experimentation, on 3× HP EliteDesk 800 G3 mini PCs
(Intel i5-6500T, 32 GB RAM, 1 TB NVMe). It shares the storage and infra VLANs
with prod but takes a distinct compute VLAN (31) and BGP ASN (65021).

Why a second cluster rather than a VM playground: it keeps an identical
operational model to prod, the addressing scheme already generalises via the
cluster-index digit, and prefix-list scoping isolates failure domains. The
trade-off — losing a general-purpose VM sandbox in favour of uniformity — was
made consciously.

### Two-cluster topology

When sandbox lands as a second Talos cluster:

- Each cluster is its own AS (prod 65020, sandbox 65021), peering with UniFi (65000)
- Each cluster allocates from only its own pool prefixes; advertises VIPs as `/32`s within those prefixes
- UniFi prefix-list filter per neighbor isolates failure domains: sandbox cannot advertise into prod's CIDR space
- Storage VLAN 25 shared; both clusters present per the cluster-identity rule
- API VIPs continue to be Talos-managed (vipController), independent of BGP
- Total BGP sessions on UniFi: 6 (3 per cluster)

Failure isolation: sandbox BGP issues cannot blackhole prod traffic when prefix-list scoping is in place.

The IP-addressing convention ([architecture.md](architecture.md#ip-addressing-convention))
already reserves sandbox's slots throughout — compute VLAN 31, the `.144/28`
storage-pod range, `admin-sandbox` / `services-sandbox` LB pools, and the `-sbx`
DNS suffix — so standing it up is largely a matter of filling in reserved space.

## Out of scope (future considerations)

- **Cilium Gateway API replacing Traefik**: defer until Cilium implements GEP-1494 (external auth filter). Until then Traefik's middleware story is irreducible for auth-less app UIs.
- **Pod CIDR BGP advertisement**: Cilium can advertise pod IPs via BGP for direct LAN reachability. Not needed for current use cases.
- **OpenObserve alerting on BGP session state**: deferred during the BGP migration; revisit once OpenObserve is the obvious place for it.
