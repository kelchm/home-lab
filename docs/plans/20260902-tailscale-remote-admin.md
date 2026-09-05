# Tailscale remote-admin architecture

**Status:** Active — 2026-09-03; the PVE router pair, aggregate route, UniFi authorization, split DNS, and bidirectional failover were implemented and validated on 2026-09-02 and the Kubernetes Connector was removed. The [client location policy](#client-location-policy) is pending rollout.

## Outcome

Remote administration survives a Kubernetes outage and covers devices that cannot run a native Tailscale client. Native clients remain the preferred path for laptops, phones, workstations, and capable servers. Two ordinary PVE VMs provide the subnet-router path for everything else.

The routers do not live in Kubernetes because Kubernetes is one of the systems remote access must recover. They are not PVE HA guests because application-level route failover is simpler and preserves failure-domain separation: one VM on `pve-sbx-2`, one on `pve-sbx-3`, both advertising an identical route set.

A client on the home network is not connected to Tailscale. Remote access is for when the operator is away; at home, every device uses the ordinary UniFi path and the routers carry nothing.

## Network boundary

VLAN 19 `Remote Admin` (`10.32.19.0/24`) is a narrow ingress trust boundary, not a general-purpose server VLAN:

| Address | Tenant |
|---|---|
| `10.32.19.1` | UniFi gateway and DNS forwarder |
| `10.32.19.101` | `tailscale-router-1`, VMID 101 on `pve-sbx-2` |
| `10.32.19.102` | `tailscale-router-2`, VMID 102 on `pve-sbx-3` |

The `.101/.102` final octets deliberately match the PVE VMIDs, making an address in a log immediately traceable to its guest. DHCP and IPv6 are disabled. The PVE hosts trunk VLAN 19 through `vmbr0` but hold no address on it.

The dedicated UniFi zone allows Gateway and External egress for package updates and Tailscale coordination while blocking every other zone transition by default. `Allow Main to Tailscale Routers` permits Main to reach only `.101/.102`. `Allow Tailscale Routers to Routed LAN` permits only those two source IPs to the six enforcement prefixes below. Both policies have generated established/related return rules; there is no general Remote Admin → Internal allow.

## Aggregate route and enforcement set

Both routers advertise one route: `10.32.0.0/16`. The aggregate describes the lab's whole addressing domain, so remote reachability is a single HA route that never changes when destinations are added or removed. It is not a mechanism for at-home behavior: only a client's directly connected `/24` is more specific than it, so a connected at-home client would send every other home VLAN into the routers. That is why at-home clients are disconnected.

Advertising reachability is separate from authorizing it. The UniFi rule allows the router pair to initiate traffic only to these six destination prefixes:

| Firewall-permitted prefix | Remote purpose |
|---|---|
| `10.32.1.0/24` | UniFi controller and Default-network infrastructure |
| `10.32.10.0/24` | trusted Main devices that cannot run a native client |
| `10.32.20.0/24` | PVE, NAS, switches, APs, and other management endpoints |
| `10.32.30.0/24` | Talos nodes, Kubernetes API VIP, and the LAN DNS resolver |
| `10.32.130.0/24` | `admin-prod` BGP pool and `k8s-gateway` |
| `10.32.140.0/24` | `services-prod` BGP pool and household application gateway |

Excluded from the UniFi allowlist on purpose: Cameras, Remote Admin itself, Workloads, Storage, the future second Kubernetes cluster, IoT, Guest, sandbox LB pools, `shared-prod`, and all unallocated space. Packets for those addresses can enter the aggregate Tailscale route but cannot cross from the router VMs into the destination network. Additions require a named use case, matching UniFi destination entry, and positive plus adjacent-negative tests; the Tailscale advertisement does not change.

UniFi Network 10.6.101 presents the BGP pools as External for ordinary Internal sources, but live probes from the custom Remote Admin zone timed out until `10.32.130.0/24` and `10.32.140.0/24` were included in the Internal destination rule. That observed forwarding behavior is recorded in [`network/unifi/README.md`](../../network/unifi/README.md) and must be revalidated after controller upgrades.

## Tailnet policy

Both nodes are tagged `tag:remote-admin-router`, have non-expiring keys, keep subnet-route SNAT enabled, do not accept peer routes, and do not consume Tailnet DNS themselves. The Tailnet auto-approves `10.32.0.0/16` only for that tag. The existing allow-all grant is unchanged; narrowing client-to-subnet authorization is a separate policy project and must not be mixed into router availability work.

The relevant live policy fragment is:

```jsonc
{
  "tagOwners": {
    "tag:remote-admin-router": [],
  },
  "autoApprovers": {
    "routes": {
      "10.32.0.0/16": ["tag:remote-admin-router"],
    },
  },
}
```

Both routers must advertise the exact same `/16`. [Tailscale groups only exact prefix matches for high-availability failover](https://tailscale.com/kb/1115/subnet-failover/); a broader route is not a fallback for a narrower one. Neither router may enable `--accept-routes`, because the non-selected router accepting the selected router's LAN route can send locally reachable traffic back through its peer.

## Client location policy

Subnet routes are for destinations that cannot run a native client, and they are installed on a client only while it is away from home. A client at home has a connected route for its own VLAN and a default route through UniFi for everything else; any advertised Tailscale prefix beats that default route, so a connected at-home client would hairpin admin VIPs through the routers and lose IoT, Guest, and every other excluded VLAN entirely. iOS accepts subnet routes unconditionally. No advertisement shape avoids this: a `/32` still beats the default route and a `/24` collides with the connected `/24`.

Location awareness therefore comes from the operating system's VPN On Demand rules, which Tailscale exposes on iOS 1.48+ and macOS 1.60+, not from route precedence, DHCP-injected routes, or per-destination route inventories. Nothing on the tailnet other than the two routers is reachable only through Tailscale, so disconnecting at home loses no access. The client population is the operator's iPhone and Mac; no other platform is in scope, and family access to a service such as Seerr is deferred until that service has its own independently grantable identity.

**iPhone:** Wi-Fi rule Except On every home SSID; Cellular rule Always. A phone that falls back to cellular while at home behaves as a remote client, which is correct.

**Mac:** Wi-Fi rule Except On every home SSID; Ethernet rule Never; the "connect when a `*.ts.net` name is used" option off; `accept-routes` on. Client 1.102.3 has two traps. With any interface on Do Nothing the app installs an unconditional `*.ts.net` EvaluateConnection rule ahead of the SSID rules, and macOS stops at the first matching rule, so Except On never fires; the option is only visible in the Manage sheet while an interface is on Do Nothing, and the app rewrites the installed rules only when the sheet is saved. And while On Demand holds the session down at home, any `tailscale` CLI command asks macOS to start the tunnel, On Demand rejects it, and the CLI retries about three times a second, leaking one `utun` interface per attempt until the CLI process is killed; on 2026-09-03 two CLI invocations produced 1018 start attempts and 806 leaked interfaces, after which the network extension failed every start with `TunnelError error 0` until reboot, and with On Demand enabled every application that touched the network re-triggered a failing start. Only a reboot clears the leaked interfaces and the wedged extension. Change preferences through the menu bar app or from a network where On Demand permits the connection. Verify with `scutil --nc show Tailscale` that the first rule is the Wi-Fi SSID Disconnect, and judge state with `scutil --nc status Tailscale` and `route -n get 10.32.99.141` rather than the menu bar.

Rollout:

1. On each device, confirm no other VPN profile has On Demand enabled, then apply the rules above and, on the Mac, check the installed rule order.
2. From cellular or foreign Wi-Fi: Tailscale connects on its own; `sonarr.home.kelch.io`, `seerr.home.kelch.io`, and the PVE UI load; `home.kelch.io` names resolve.
3. At home: joining home Wi-Fi disconnects Tailscale within a few seconds; AirPlay to the basement speaker connects; `jellyfin.home.kelch.io` plays; neither router sees traffic from the device.
4. Toggle Wi-Fi off and on at home to confirm On Demand reconnects on cellular and disconnects again on Wi-Fi.

Acceptance: on home Wi-Fi the device reports Tailscale disconnected, AirPlay connects, and Jellyfin direct-plays with no packets on either router; on cellular or foreign Wi-Fi every remote-admin destination works with its ordinary `*.home.kelch.io` URL; the transition needs no user action. Rollback is setting the Wi-Fi rule back to Do Nothing on the device, which touches nothing server-side.

A foreign network using a more-specific overlapping RFC 1918 prefix can shadow the corresponding home addresses and must be handled as a client/network exception.

## DNS

The Tailnet's restricted nameserver for `home.kelch.io` is the UniFi resolver at `10.32.30.1`. It answers static infrastructure records and forwards application names to `k8s-gateway`. DNS depends on the `10.32.30.0/24` route and is part of every failover drill. An at-home client is disconnected and uses the LAN resolver directly.

## Host design

Both guests use Debian 13, 1 vCPU, 1 GiB RAM, 8 GiB local storage, VirtIO networking, QEMU guest agent, and the official Tailscale package repository. IPv4 and IPv6 forwarding are enabled, though only IPv4 LAN routes are advertised. Linux UDP GRO forwarding tuning is persisted at boot. No auth material, route approvals, ACLs, or advertised routes live in cloud-init or Git.

Subnet-route SNAT stays enabled. LAN devices therefore see the stable VLAN 19 router IP rather than a changing remote Tailnet client address; identity and authorization remain in the Tailnet policy, while UniFi authorizes the router pair as a bounded ingress tier.

## Acceptance record

On 2026-09-02, an enrolled Main client temporarily enabled route acceptance. Its connected `10.32.10.0/24` remained on the physical interface while destinations elsewhere in `10.32.0.0/16` used the Tailscale interface. PVE returned HTTP 200 at `10.32.20.21:8006`, the unauthenticated Kubernetes API returned HTTP 401 at `10.32.30.8:6443`, both Traefik VIPs returned the expected HTTP 404 without a host match, and `home.kelch.io` records resolved correctly. Actual hosts in Workloads and Storage also used the Tailscale interface but were blocked by UniFi, confirming that the broad route did not widen the six-prefix firewall authorization.

Stopping `tailscaled` on the selected router moved the `/16` to its peer in about 16 seconds, after which every probe and DNS still passed. Repeating the drill in the other direction produced the same result. This validated daemon and route-selection failover; guest power, PVE host, and physical VLAN trunk failures were not exercised. Both daemons were restored and the test client returned to `accept-routes=false`. Selection can remain on the surviving router after its peer returns, so the VMs are equivalent candidates rather than permanently assigned primary and standby roles.

On 2026-09-03, an at-home iPhone with Tailscale connected could not reach the AirPlay speaker at `10.32.99.141` on Guest: the `/16` captured the flow and UniFi dropped it. The 2026-09-02 drill had recorded the same behavior for Workloads and Storage hosts as a negative-authorization pass without testing an excluded destination that an at-home client legitimately needs. The client location policy is the response; its rollout and acceptance are pending.

## Operations and rollback

Normal checks are `systemctl is-active tailscaled tailscale-gro-forwarding`, `tailscale status`, `10.32.0.0/16` in `tailscale debug prefs`, and the Tailscale admin console showing both tagged nodes connected with one owning that `PrimaryRoutes` entry. PVE backups cover both guests, but a router restore must retain the VMID/IP mapping and should reauthenticate rather than copy a live Tailscale state directory between machines.

Server-side rollback is to revert the Git change and restore the old Tailnet auto-approver before reconciling Flux. If the new UniFi path misbehaves, pause `Allow Tailscale Routers to Routed LAN`; the zone immediately returns to egress-only isolation.

After Flux confirms deletion of the old Connector and operator, remove the stale `lan-subnet-router` and `tailscale-operator` machines from the Tailnet, delete the unused OAuth client, and remove the legacy Kubernetes tag-owner definitions from the policy.

## Follow-ups tracked separately

- Native Tailscale on GLKVM and PVE, which gives recovery access that does not depend on the router VMs. This first requires replacing the Tailnet allow-all policy with grants.
- Move the basement speaker from Guest to IoT with a reservation and fix the ULA `/64` currently shared across VLANs. Test AirPlay with Tailscale disconnected before attributing any remaining failure to routing.
- Family access to Seerr, when wanted, through a Tailscale Service or operator ingress rather than a subnet route.
