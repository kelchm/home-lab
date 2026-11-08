# UniFi network config

Versioned artifacts for UniFi-side configuration that pairs with this repo's
Kubernetes manifests. UniFi is not GitOps-managed; these files are the source
of truth and changes are applied manually.

## Files

- `frr.conf` — BGP peering with the Cilium BGP control plane on the prod Talos
  cluster. AS 65000 (UniFi) ↔ AS 65020 (k8s-prod). See file header for details.
- `bgp-test.yaml` — disposable echo Service for the BGP migration synthetic
  test. Applied via `kubectl`, not Flux. See "Synthetic test" below.

The "Firewall zones applied state" and "IDS/IPS signature suppression" sections below distinguish applied state from intent for configuration that lives only in the UniFi UI; no exportable artifact lives in this repo.

The [local NTP runbook](../../docs/runbooks/local-ntp.md) covers the applied Pi NTP firewall exception and DHCP option 42 advertisements on seven networks. All three Talos nodes on VLAN 30 have demonstrated fresh Pi synchronization; DHCP-client adoption after natural renewal, client failover and WAN-outage acceptance remain pending in [#1031](https://github.com/kelchm/home-lab/issues/1031).

## Remote Admin applied state

The Tailscale router rollout created an isolated VLAN and replaced the PVE trunk's repeated per-port overrides with one reusable port profile in UniFi Network 10.6.101:

| Item | Applied value |
|---|---|
| Network | `Remote Admin`, VLAN 19, `10.32.19.1/24` |
| Addressing | DHCP disabled; IPv6 disabled; NetBird routing peer `10.32.19.103`; `.101` and `.102` unused since the Tailscale routers were destroyed |
| Zone | Dedicated `Remote Admin` zone |
| Default zone policy | Allow to Gateway and External; block to Internal, VPN, Hotspot, DMZ, and Remote Admin |
| `pve-guest-trunk` native network | None |
| `pve-guest-trunk` tagged networks | Main (10), Remote Admin (19), Workloads (21), Storage (25), IoT (90) only |
| `pve-guest-trunk` applied ports | Lab Switch 13 `pve-sbx-1-trunk`, 14 `pve-sbx-2-trunk`, 15 `pve-sbx-3-trunk` |
| Port features | Infrastructure mode, PoE off, autonegotiation on |

The profile deliberately excludes Default, Cameras, Infra Mgmt, Guest, K8s Prod, K8s Sandbox, and Services. All three links remained up at 2.5 GbE after assignment. The matching PVE `vmbr0` allowlist is `10 19 21 25 90` on every node.

Two custom forward policies cross the boundary:

| Policy | Source zone and match | Destination zone and match | Action |
|---|---|---|---|
| `Allow NetBird Router to Services HTTPS` | `Remote Admin`; IP `10.32.19.103` | `Internal`; IP `10.32.140.1`, port 443 | Allow; IPv4; TCP |
| `Allow NetBird Router to Admin HTTPS` | `Remote Admin`; IP `10.32.19.103` | `Internal`; IP `10.32.130.1`, port 443 | Allow; IPv4; TCP |

On 2026-10-29, `Allow Main to Tailscale Routers` and `Allow Tailscale Routers to Routed LAN` were deleted with their owned generated returns under [#1069](https://github.com/kelchm/home-lab/issues/1069). Fresh reloads in Network 10.6.109 showed policy counts 114 → 112 → 110. Both NetBird forwards and their established/related returns, VLAN 19 and the Remote Admin zone were preserved. Generated return IDs can renumber or be reused; verification matched fresh parent relationships, names and policy semantics rather than historical ID absence. This was console readback, with no traffic test or complete configuration export. Main → Remote Admin is now blocked, and the two NetBird policies above are the only Remote Admin → Internal initiation exceptions.

The NetBird policies belong to the ongoing setup and comprehensive testing in #625. Matt selected NetBird to replace Tailscale entirely. VM 103 is now the only routed remote path, so do not remove it or these policies without a replacement. The admin HTTPS extension was applied on October 19, 2026. The October 19 checks from `10.32.19.103` showed that Grafana's `/api/health` on `10.32.130.1:443` returned HTTP 200 with `database: ok`, and Jellyfin's `/health` on `10.32.140.1:443` returned HTTP 200. Connections to `10.32.130.1:80`, `10.32.140.1:80`, `10.32.20.21:8006`, and k8s-gateway at `10.32.130.2:53` timed out; the last endpoint answered TCP DNS from Main. These probes verified the router-to-LAN path. Owner acceptance recorded October 27: Matt confirmed that Grafana login and dashboards work on the iPhone over NetBird away from home. This accepts the admin HTTPS extension; the wider work in #625 remains unfinished. Its DNS resource `10.32.30.1` needs no policy because gateway interface addresses fall in the Gateway zone. The [NetBird runbook](../../docs/runbooks/netbird-remote-access.md) records the resource policy, remote acceptance checks and rollback.

While they ran, the Tailscale routers advertised `10.32.0.0/16`; that routing aggregate was deliberately broader than their six-prefix authorization rule, preserved in the [historical design](../../docs/plans/20260902-tailscale-remote-admin.md#aggregate-route-and-enforcement-set). A client could therefore install one stable lab route while UniFi continued to decide which destination networks traffic from `.101/.102` could actually enter. Positive tests through the aggregate reached every allowlisted class, while actual hosts in Workloads (`10.32.21.31`) and Storage (`10.32.25.5`) remained blocked. Although the policy table described the Cilium BGP pools as External for ordinary Internal sources, probes from the custom Remote Admin zone timed out until `10.32.130.0/24` and `10.32.140.0/24` were included in that former Internal destination rule; both returned HTTP 404 immediately afterward. Revalidate this zone behavior after UniFi upgrades. UniFi classifies traffic addressed to any of its own VLAN interface IPs in the Gateway zone, so those interface addresses remain reachable under the zone's gateway allow even when their attached networks are absent from the Internal allowlist.

On 2026-09-02, positive validation from both router VMs reached the Default and Main gateways, PVE at `10.32.20.21:8006` (HTTP 200), the Kubernetes API at `10.32.30.8:6443` (HTTP 401 without credentials), and the two Traefik VIPs at `10.32.130.1:443` and `10.32.140.1:443` (HTTP 404 without a host match). Before this allow existed, both routers were unable to ping PVE management or connect to its UI.

## DGX Spark applied state

The Spark commissioning session created VLAN 21 `Workloads` and the
`spark-trunk` port profile, then applied that profile to enabled Core
Aggregation ports 7 and 8:

| Item | Applied value |
|---|---|
| Workloads gateway | `10.32.21.1/24` |
| Workloads DHCP | `10.32.21.200-.239` |
| `spark-trunk` native network | VLAN 21 |
| `spark-trunk` tagged networks | VLAN 25 only |
| Port features | Autonegotiation and flow control enabled; EEE disabled |
| Port 7 | `spark-1`, 10GbE |
| Port 8 | `spark-2`, 10GbE |

The original Spark commissioning phase did not create Workloads firewall rules. The PVE commissioning session added the three applied containment rules documented below on 2026-08-27, and the Hermes commissioning session added the narrow MetaMCP exception on 2026-08-28; the rest of the topology matrix remains deferred. See the [DGX Spark bring-up runbook](../../docs/runbooks/dgx-spark-bringup.md) for the full host configuration, physical port map, test record, and remaining gates.

UniFi local DNS also contains a Host (A) record for `hermes.home.kelch.io` at `10.32.21.100`. It was added on 2026-08-27 and verified through the gateway resolvers on the Default, Main, Workloads, and K8s Prod networks.

## Workloads containment applied state

The following policies remain live in UniFi Network 10.6.101 and ordered above the default `Allow Return Traffic` and `Allow All Traffic` policies:

| Policy | Source zone and match | Destination zone and match | Action |
|---|---|---|---|
| `Allow Hermes to MCPHub` | `Internal`; IP `10.32.21.100` | `External`; IP `10.32.130.1` | Allow; TCP 443 |
| `Block Workloads to Protected Networks` | `Internal`; network `Workloads` | `Internal`; networks `Infra Mgmt`, `K8s Prod`, `Storage` | Block; all protocols |
| `Block Workloads to Admin Prod` | `Internal`; network `Workloads` | `External`; IP `10.32.130.0/24` | Block; all protocols |
| `Block K8s Prod to Workloads` | `Internal`; network `K8s Prod` | `Internal`; network `Workloads` | Block; all protocols |

`Allow Hermes to MCPHub` (formerly `Allow Hermes to MetaMCP`) carries MCPHub traffic through the admin-gateway IP. It is ordered immediately above `Block Workloads to Admin Prod`; all other Workloads clients remain denied from `admin-prod`. An unauthenticated HTTPS probe from Hermes reached MetaMCP and returned HTTP 401, while the earlier disposable-guest test remained blocked.

`admin-prod` is a Cilium BGP-routed prefix, not a UniFi network. A live negative test proved that UniFi classifies this routed destination through the `External` zone: an `Internal` destination rule did not block Traefik at `10.32.130.1`, while the otherwise identical `External` rule did. This classification is controller behavior, not a statement that the service is Internet-hosted. Retest it after controller upgrades or routing changes.

Validation from disposable PVE guest `10.32.21.201` produced the following matrix before the guest was destroyed:

| Probe | Result |
|---|---|
| PVE management `10.32.20.21` TCP 22/8006 | Blocked |
| PVE storage `10.32.25.21` TCP 22 | Blocked |
| Athena `10.32.25.5` TCP 2049 | Blocked |
| Talos API `10.32.30.11` TCP 50000 | Blocked |
| `admin-prod` Traefik `10.32.130.1` TCP 443 | Blocked |
| `services-prod` Traefik `10.32.140.1` TCP 443 | Open |
| Local DNS for `jellyfin.home.kelch.io` | Resolved to `10.32.140.1` |
| Internet HTTP | HTTP 200 |
| `k8s-prod` pod to guest TCP 22 | Blocked |
| Main admin workstation to guest TCP 22 | Open |

These rules implement the PVE-specific slice of the network-topology matrix. They do not make the complete phase-2 firewall posture applied. The Untrusted zone and Guest containment are documented under [Firewall zones applied state](#firewall-zones-applied-state); the Main admin-device restriction, the Infrastructure zone and the Spark inference restrictions remain to be implemented.

## Applying `frr.conf`

The config targets FRR, which UniFi gateways (UDM Pro / UDM SE / UXG-series)
ship with. Two paths to apply:

1. **UniFi Network UI (preferred where supported)** — Settings → Routing → BGP.
   Paste the FRR config; the controller reconciles it onto the gateway.
2. **Direct on the gateway** — SSH to the gateway, edit `/etc/frr/frr.conf`,
   `vtysh -c 'configure terminal' -c 'copy running-config startup-config'`.
   Note that UniFi may overwrite manual edits during controller pushes; (1) is
   strongly preferred.

Before pasting, replace `${BGP_PASSWORD}` with the plaintext MD5 password.
Retrieve it with:

```sh
sops --decrypt \
  kubernetes/apps/kube-system/cilium-bgp/app/bgp-secret.sops.yaml \
  | yq '.stringData.password'
```

After applying, validate:

```
show ip bgp summary
show ip bgp neighbors 10.32.30.11
show ip route bgp
```

Sessions should reach `Established` once Cilium is reconciled with matching
peer/auth config. No prefixes are advertised until a `CiliumLoadBalancerIPPool`
matching `admin-prod`, `services-prod`, or `shared-prod` exists and a Service
allocates from it.

## Firewall zones applied state

Rules are configured manually in the UniFi UI; this section records the applied state because there is no committable controller artifact. The target model and conventions are in [`docs/architecture.md`](../../docs/architecture.md#firewall-zones). Progress is tracked in [#348](https://github.com/kelchm/home-lab/issues/348).

Applied on 2026-11-07 in UniFi Network 10.6.109, with temporary test interfaces used for positive and negative checks before and after each change:

| Zone | Networks |
|---|---|
| Internal | Default, Main, Infra Mgmt, Workloads, Storage, K8s Prod, K8s Sandbox, W1700K Temp (28), Guest |
| Untrusted (custom) | Cameras, IoT |
| DMZ | Services (40) |
| Remote Admin (custom) | Remote Admin |

Internal-to-Internal still ends in Allow All. Untrusted blocks routed traffic to Internal, between its own members, and to every other local zone by default; Internet access stays allowed except for Cameras. The zone was created as `IoT` and renamed `Untrusted` the same day; rules reference zones by ID, so the rename changed no policy.

Blocks added in this migration match new connections only and log, so each blocked flow appears in the UniFi flow logs in VictoriaLogs with the rule name in `flow.policies`. Allowed flows show only `CONNTRACK`.

### Untrusted zone

| Policy | Source | Destination | Action |
|---|---|---|---|
| `Allow Main to Untrusted` | Internal; IP `10.32.10.0/24` | Untrusted; any | Allow; generated return |
| `Allow Home Assistant to Thermostats` | Internal; IP `10.32.1.244` | Untrusted; IPs `10.32.90.30-.33`, TCP 80 | Allow; generated return |
| `Allow Bambuddy to Printers` | Internal; IPs `10.32.30.11-.13` | Untrusted; IPs `10.32.90.50`, `.51`, TCP 8883, 990, 50000-50100, 322, 6000, 2024-2026 | Allow; generated return |
| `Allow Untrusted to Local NTP` | Untrusted; any | Internal; IPs `10.32.20.111`, `.112`, UDP 123 | Allow; generated return |
| `Allow IoT to Home Assistant` | Untrusted; IP `10.32.90.0/24` | Internal; IP `10.32.1.244`, TCP 8123 | Allow; generated return |
| `Allow IoT to Visionect` | Untrusted; IP `10.32.90.0/24` | Internal; IP `10.32.150.30`, TCP 11113 | Allow; generated return |
| `Allow Legacy Display to Hyperion` | Untrusted; IP `10.32.90.179` | Internal; IP `10.32.10.244`, TCP 11113, 11199 | Allow; generated return. Temporary: one display still points at a legacy endpoint on a workstation, using an unreserved address. |
| `Allow IoT to Visionect Routed` | Untrusted; IP `10.32.90.0/24` | External; IP `10.32.150.30`, TCP 11113 | Allow |
| `Block Untrusted to Service Pools` | Untrusted; any | External; IPs `10.32.130.0/24`, `10.32.140.0/24`, `10.32.150.0/24` | Block; new only; logged |

Visionect is permitted in both the Internal and External destination cells because the routed address has matched both, depending on the path. Cilium still restricts Bambuddy's pod-level access; the gateway sees the node addresses.

Gateway access from Untrusted, in order: `Allow Untrusted to Gateway DNS` (TCP/UDP 53), `Allow Untrusted to Gateway DHCP NTP mDNS` (UDP 67, 123, 5353), `Allow Untrusted to Gateway ICMP`, `Allow Cameras to Protect` (`10.32.5.0/24`, TCP 6666, 7442, 7444, 7550), `Allow Untrusted to Gateway mDNS Proxy` (UDP source port 5353, any destination port), then `Block Untrusted to Gateway` (new only; logged). The mDNS response permit is needed because the gateway's mDNS proxy queries by multicast, so device answers sent by unicast to the proxy's port do not match connection tracking as replies.

### Cameras

Cameras (VLAN 5, `10.32.5.0/24`) is in Untrusted with `Allow Internet Access` off, `Isolate Network` on, and IPv6 disabled. UniFi regenerated the Internet block in the Untrusted-to-External cell when the network moved. Internet egress is blocked except for Thingino firmware downloads from the Wyze Cam 3 at `10.32.5.30` and Cam 2 at `10.32.5.31`:

| Policy | Source | Destination | State |
|---|---|---|---|
| `Allow Thingino Cameras to GitHub` | Untrusted; `10.32.5.30`, `10.32.5.31` | External; `github.com`, `raw.githubusercontent.com`, `release-assets.githubusercontent.com`; TCP 443 | Active; logged; above the Internet block |

The update exception permits any HTTPS content on those GitHub hosts. Keep the two camera IPs stable or update the source matches when renumbering them. The paused legacy `Camera Out Test` rule, which would have restored blanket Internet access if resumed, was deleted on 2026-11-08. Pause the update allow to withdraw just the exception. Main reaches the cameras through `Allow Main to Untrusted`; ordinary IoT devices cannot reach them, because the zone blocks traffic between its members.

After the 2026-11-07 move, both UniFi cameras stayed connected to Protect through `10.32.5.1` and kept recording, and Protect returned snapshots. Both Thingino cameras were offline, so their update exception was verified by readback rather than a live download. The earlier acceptance results and firmware snapshots are recorded in [#349](https://github.com/kelchm/home-lab/issues/349).

### Guest containment

Guest is still in Internal, because household devices on it depend on local services until they move to IoT. These rules restrict it in the meantime:

| Policy | Source | Destination | Action |
|---|---|---|---|
| `Block Guest to Lab Networks` | Internal; network Guest | Internal; networks Infra Mgmt, Workloads, Storage, K8s Prod, K8s Sandbox | Block; new only; logged |
| `Block Guest to Legacy Services` | Internal; network Guest | DMZ; network Services | Block; new only; logged |
| `Block Guest to Admin Prod` | Internal; network Guest | External; IP `10.32.130.0/24` | Block; new only; logged |

Gateway access from Guest follows the same pattern as Untrusted: `Allow Guest to Gateway DNS`, `Allow Guest to Gateway DHCP NTP mDNS`, `Allow Guest to Gateway ICMP` and `Allow Guest to Gateway mDNS Proxy`, then `Block Guest to Gateway` (new only; logged).

Guest still reaches Main, Default (including Home Assistant) and `services-prod`. Closing those paths waits for the household devices to move off Guest.

### Local NTP

`Allow Workloads to Local NTP` is enabled for Internal → Internal traffic from the Workloads network to `10.32.20.111/32` and `10.32.20.112/32`, IPv4 UDP destination 123, source port Any, All states/Always. It sits above `Block Workloads to Protected Networks`. Cameras and IoT use `Allow Untrusted to Local NTP` instead. Storage was removed from the source on 2026-11-08: no Storage host had used it in the previous week, and Storage-attached hosts take time over their ordinary interface. Other Internal networks reach the Pis through Allow All. Auto Allow Return Traffic is off. See [#1031](https://github.com/kelchm/home-lab/issues/1031) for the original applied-state evidence.

### Other Internal rules

| Policy | Source | Destination | State |
|---|---|---|---|
| `Allow Main to Visionect Admin` | Internal; IP `10.32.10.0/24` | Internal; IP `10.32.150.30`, TCP 443 | Active; generated return |
| `Block Internal to Visionect` | Internal; any | Internal; IP `10.32.150.30` | Active |

The Workloads containment rules are listed [above](#workloads-containment-applied-state).

### BGP load-balancer pools

The pools are routed prefixes, not UniFi networks. From Internal and Untrusted sources UniFi classifies them as External, so pool denies go in the External destination cell ahead of the Internet allow. Applied pool rules:

| Source | Destination | Policy |
|---|---|---|
| Workloads | `admin-prod` (`10.32.130.0/24`), except Hermes → `10.32.130.1` TCP 443 | `Block Workloads to Admin Prod` |
| Untrusted | `admin-prod`, `services-prod`, `shared-prod`, except Visionect | `Block Untrusted to Service Pools` |
| Guest | `admin-prod` | `Block Guest to Admin Prod` |

Main reaches every pool, and Guest reaches `services-prod`, until the admin-device restriction and the Guest move are applied. Per-pool intent is in [`docs/architecture.md`](../../docs/architecture.md#firewall-posture-per-pool).

#### shared-prod tenant rules

Shared-pool policy is explicit per tenant. Visionect at `10.32.150.30` accepts its device protocol (TCP 11113) from IoT and its management UI (TCP 443) from Main. `Block Internal to Visionect` denies every other Internal source. The Cilium policy on the destination pod repeats the same source and port boundary; the UniFi rules stop disallowed traffic before it reaches the cluster.

## VPN

Site Magic is disabled (`magic_site_to_site_vpn.enabled: false`) for this single-site deployment. No UniFi VPN servers or Site-to-Site VPNs are configured; remote management remains enabled.

Network 10.6.106 redirects the SD-WAN UI to Site Manager. To change the local enable flag, use the authenticated controller API: read `GET /proxy/network/api/s/default/rest/setting`, select `key: magic_site_to_site_vpn`, and post that complete object with only `enabled` changed to `POST /proxy/network/api/s/default/set/setting/magic_site_to_site_vpn`. Use the `x-csrf-token` response header from `GET /api/users/self`, preserve all other fields, and read the flag back after saving. The object contains private key material; do not log or commit it.

## IDS/IPS signature suppression

Threat Management runs in **Notify** mode, so a suppression costs alerting only. Suppressions are added from an alert's action menu (System Log → Security → open an event → `Suppress Signature`), not from `Detection Exclusions`, which takes an IP/network/subnet and can't reference a signature.

| Signature | SID | Scope | Rationale |
|---|---|---|---|
| `ET SCAN Potential SSH Scan OUTBOUND` | 2003068 | Outgoing / Subnet `140.82.112.0/20` | Routine git-over-SSH trips the rule's 5-SYN-to-port-22-per-120s threshold — ~74 false positives/day, ~99% of Security log volume. |

`140.82.112.0/20` is GitHub's published `git` range (`api.github.com/meta`), which also lists `192.30.252.0/22`, `185.199.108.0/22`, and `143.55.64.0/20` — add those as further Subnet rows if detections reappear. Scoping beats `Target: Any`, which would suppress the signature for every host and destination.

Non-obvious behavior, confirmed by reading `ips_suppression` back from `GET .../rest/setting` after applying:

- `Type: Subnet` accepts CIDR, so a netblock is one row rather than one per address.
- `Traffic Direction` serializes to `src` / `dest` / `both` (Suricata `by_src` / `by_dst` / `by_either`). `Outgoing` writes `direction: "dest"` — not the `incoming`/`outgoing` enum region blocking uses.
- `POST .../set/setting/ips_suppression` is a whole-object set; a hand-built POST that drops `whitelist` clobbers rather than merges. Prefer the UI.

Verified 2026-08-16 on UniFi OS 5.1.26 / Network 10.5.67 — signature silenced, Intrusion Prevention still enabled. To re-check, cross the threshold from a host behind the gateway and confirm no new `Threat Detected` entry appears:

```sh
seq 6 | xargs -I{} ssh -T -o BatchMode=yes git@github.com
```

## Synthetic test (`bgp-test.yaml`)

Step 6 of the BGP migration. Applied manually, not via Flux, so teardown is
trivial. Pins a Service to `10.32.130.99` from the `admin-prod` pool and
exercises the full BGP advertisement / firewall / failover path before any
production cutover.

Apply:

```sh
kubectl apply -f network/unifi/bgp-test.yaml
```

Validate (each step gates the next):

1. **IPAM allocation** — `kubectl -n bgp-test get svc echo` shows
   `EXTERNAL-IP=10.32.130.99`.
2. **BGP advertisement** — on the gateway:
   `vtysh -c 'show ip route 10.32.130.99'` lists 3 ECMP next-hops
   (`10.32.30.11`, `.12`, `.13`).
3. **Allowed-VLAN reachability** — from VLAN 10 (Main):
   `curl -s http://10.32.130.99/` returns the echo JSON.
4. **Denied-VLAN blocking** — from VLAN 90 (IoT) / VLAN 99 (Guest):
   `curl --max-time 2 http://10.32.130.99/` should fail (timeout / refused).
   The pool denies in [BGP load-balancer pools](#bgp-load-balancer-pools) cover both networks.
5. **Failover** — never with a bare `talosctl reboot`. The [Talos rollout procedure](../../.agents/skills/talos-rollout/SKILL.md) has no guarded plain reboot yet, so run this test during a node roll with `task talos:upgrade-node`, which gates on etcd, storage and network health. From VLAN 10, probe about once a second with a bounded, timestamped request, such as `while :; do printf '%s %s\n' "$(date +%T)" "$(curl -fs -o /dev/null --max-time 2 http://10.32.130.99/ && echo ok || echo fail)"; sleep 1; done`. `vtysh -c 'show ip route 10.32.130.99'` should show 2 next-hops while the node is down and 3 again after it returns. With BGP graceful restart off (see `kubernetes/apps/kube-system/cilium-bgp/app/bgp.yaml`), the gateway should drop the node as soon as its session closes, or once the 9-second hold time expires if it dies first. The test passes when no run of failures lasts longer than the hold time, either on the way down or when the node rejoins. Cilium can advertise a load-balancer IP before that node's datapath serves it, so the rejoin is not assumed clean. Neither is measured yet; with graceful restart on, a client hashed to the rebooting node lost 82–85 seconds ([#222](https://github.com/kelchm/home-lab/issues/222#issuecomment-6007919796)).

Tear down only when all five pass:

```sh
kubectl delete -f network/unifi/bgp-test.yaml
```
