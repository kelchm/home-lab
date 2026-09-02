# Tailscale remote-admin architecture

**Status:** Implemented — 2026-09-02; the PVE router pair is carrying the approved routes, split DNS and bidirectional failover are validated, and the Kubernetes Connector routes are withdrawn. Its Flux resources are removed by this change and will be pruned after merge.

## Outcome

Remote administration survives a Kubernetes outage and covers devices that cannot run a native Tailscale client. Native clients remain the preferred path for laptops, phones, workstations, and capable servers. Two ordinary PVE VMs provide the subnet-router path for everything else.

The routers do not live in Kubernetes because Kubernetes is one of the systems remote access must recover. They are not PVE HA guests because application-level route failover is simpler and preserves failure-domain separation: one VM on `pve-sbx-2`, one on `pve-sbx-3`, both advertising an identical route set.

## Network boundary

VLAN 19 `Remote Admin` (`10.32.19.0/24`) is a narrow ingress trust boundary, not a general-purpose server VLAN:

| Address | Tenant |
|---|---|
| `10.32.19.1` | UniFi gateway and DNS forwarder |
| `10.32.19.101` | `tailscale-router-1`, VMID 101 on `pve-sbx-2` |
| `10.32.19.102` | `tailscale-router-2`, VMID 102 on `pve-sbx-3` |

The `.101/.102` final octets deliberately match the PVE VMIDs, making an address in a log immediately traceable to its guest. DHCP and IPv6 are disabled. The PVE hosts trunk VLAN 19 through `vmbr0` but hold no address on it.

The dedicated UniFi zone allows Gateway and External egress for package updates and Tailscale coordination while blocking every other zone transition by default. `Allow Main to Tailscale Routers` permits Main to reach only `.101/.102`. `Allow Tailscale Routers to Routed LAN` permits only those two source IPs to the six approved prefixes below. Both policies have generated established/related return rules; there is no general Remote Admin → Internal allow.

## Approved route set

| Prefix | Remote purpose |
|---|---|
| `10.32.1.0/24` | UniFi controller and Default-network infrastructure |
| `10.32.10.0/24` | trusted Main devices that cannot run a native client |
| `10.32.20.0/24` | PVE, NAS, switches, APs, and other management endpoints |
| `10.32.30.0/24` | Talos nodes, Kubernetes API VIP, and the LAN DNS resolver |
| `10.32.130.0/24` | `admin-prod` BGP pool and `k8s-gateway` |
| `10.32.140.0/24` | `services-prod` BGP pool and household application gateway |

Excluded on purpose: Cameras, Remote Admin itself, Workloads, Storage, the future second Kubernetes cluster, IoT, Guest, sandbox LB pools, `shared-prod`, and all unallocated space. Do not replace the list with `10.32.0.0/16` or a synthetic supernet such as `10.32.10.0/23`; additions require a named use case, Tailnet policy change, matching UniFi destination entry, and positive plus adjacent-negative tests.

UniFi Network 10.6.101 presents the BGP pools as External for ordinary Internal sources, but live probes from the custom Remote Admin zone timed out until `10.32.130.0/24` and `10.32.140.0/24` were included in the Internal destination rule. That observed forwarding behavior is recorded in [`network/unifi/README.md`](../../network/unifi/README.md) and must be revalidated after controller upgrades.

## Tailnet policy

Both nodes are tagged `tag:remote-admin-router`, have non-expiring keys, keep subnet-route SNAT enabled, do not accept peer routes, and do not consume Tailnet DNS themselves. The Tailnet auto-approves the six prefixes only for that tag. The existing allow-all grant is unchanged; narrowing client-to-subnet authorization is a separate policy project and must not be mixed into router availability work.

The relevant live policy fragment is:

```jsonc
{
  "tagOwners": {
    "tag:remote-admin-router": [],
  },
  "autoApprovers": {
    "routes": {
      "10.32.1.0/24":   ["tag:remote-admin-router"],
      "10.32.10.0/24":  ["tag:remote-admin-router"],
      "10.32.20.0/24":  ["tag:remote-admin-router"],
      "10.32.30.0/24":  ["tag:remote-admin-router"],
      "10.32.130.0/24": ["tag:remote-admin-router"],
      "10.32.140.0/24": ["tag:remote-admin-router"],
    },
  },
}
```

Both routers must advertise the exact same prefix list. Tailscale groups only exact prefix matches for high-availability failover; a broader route is not a fallback for a narrower one. Neither router may enable `--accept-routes`, because a standby accepting the primary's LAN routes can send locally reachable traffic back through its peer.

## Client and DNS policy

Subnet routes are for destinations that cannot run a native client. Always-home clients on Main keep `accept-routes=false`: their physical gateway already reaches the LAN, and accepting the advertised `10.32.10.0/24` can interfere with same-LAN peer paths. Travel clients enable subnet routes while away; they should be tested on the home SSID before leaving route acceptance enabled there. A foreign network using the same RFC 1918 prefix can shadow a home route and must be handled as a client/network exception rather than by advertising unrelated address space.

The Tailnet's restricted nameserver for `home.kelch.io` remains the UniFi resolver at `10.32.30.1`. It answers static infrastructure records and forwards application names to `k8s-gateway`. DNS depends on the `10.32.30.0/24` route and is part of every failover drill.

## Host design

Both guests use Debian 13, 1 vCPU, 1 GiB RAM, 8 GiB local storage, VirtIO networking, QEMU guest agent, and the official Tailscale package repository. IPv4 and IPv6 forwarding are enabled, though only IPv4 LAN routes are advertised. Linux UDP GRO forwarding tuning is persisted at boot. No auth material, route approvals, ACLs, or advertised routes live in cloud-init or Git.

Subnet-route SNAT stays enabled. LAN devices therefore see the stable VLAN 19 router IP rather than a changing remote Tailnet client address; identity and authorization remain in the Tailnet policy, while UniFi authorizes the router pair as a bounded ingress tier.

## Acceptance record

On 2026-09-02, an enrolled Main client temporarily enabled route acceptance. Its connected `10.32.10.0/24` remained on the physical interface while the other five prefixes used the Tailscale interface. Through `tailscale-router-1`, PVE returned HTTP 200 at `10.32.20.21:8006`, the unauthenticated Kubernetes API returned HTTP 401 at `10.32.30.8:6443`, both Traefik VIPs returned the expected HTTP 404 without a host match, and `home.kelch.io` records resolved correctly.

Stopping `tailscaled` on router 1 moved all six primary routes to router 2 in about 10 seconds, after which every probe and DNS still passed. After restoring router 1, stopping router 2 moved all six routes back in about 8 seconds and the probes again passed. Both daemons were restored, router 1 was primary, router 2 was standby, and the test client returned to `accept-routes=false`.

The former Kubernetes `lan-subnet-router` still advertises the six prefixes until Flux prunes it, but none are approved and `tag:k8s` is no longer an auto-approver. It is not a failover candidate.

## Operations and rollback

Normal checks are `systemctl is-active tailscaled tailscale-gro-forwarding`, `tailscale status`, the six advertised routes in `tailscale debug prefs`, and the Tailscale admin console showing both tagged nodes connected with one owning `PrimaryRoutes`. PVE backups cover both guests, but a router restore must retain the VMID/IP mapping and should reauthenticate rather than copy a live Tailscale state directory between machines.

Before the Kubernetes resources are pruned, rollback is to withdraw the PVE advertisements and manually reapprove the old Connector routes. After pruning, revert the Git change and restore the old Tailnet auto-approver before reconciling Flux. If the new UniFi path misbehaves, pause `Allow Tailscale Routers to Routed LAN`; the zone immediately returns to egress-only isolation.

After Flux confirms deletion of the old Connector and operator, remove the stale `lan-subnet-router` and `tailscale-operator` machines from the Tailnet, delete the unused OAuth client, and remove the legacy Kubernetes tag-owner definitions from the policy.
