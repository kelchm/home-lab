# UniFi network config

Versioned artifacts for UniFi-side configuration that pairs with this repo's
Kubernetes manifests. UniFi is not GitOps-managed; these files are the source
of truth and changes are applied manually.

## Files

- `frr.conf` — BGP peering with the Cilium BGP control plane on the prod Talos
  cluster. AS 65000 (UniFi) ↔ AS 65020 (k8s-prod). See file header for details.
- `bgp-test.yaml` — disposable echo Service for the BGP migration synthetic
  test. Applied via `kubectl`, not Flux. See "Synthetic test" below.

The "Firewall rules" and "IDS/IPS signature suppression" sections below distinguish applied state from intent for configuration that lives only in the UniFi UI; no exportable artifact lives in this repo.

## Camera egress and Site Magic applied state

Applied on 2026-10-01 in UniFi OS 5.1.33 / Network 10.6.106 for issue [#349](https://github.com/kelchm/home-lab/issues/349). Cameras (VLAN 5, `10.32.5.0/24`) has `Allow Internet Access` off, `Isolate Network` on, and IPv6 disabled. The custom `Camera Out Test` blanket Cameras → External allow is paused; it previously overrode the generated Internet block despite the network toggle being off.

| Policy | Source | Destination | Applied behavior |
|---|---|---|---|
| `Allow Thingino Cameras to GitHub Updates` | Internal; IPs `10.32.5.30`, `10.32.5.31` | External; domains `github.com`, `raw.githubusercontent.com`, `release-assets.githubusercontent.com`; TCP 443 | Allow; IPv4; always; syslog enabled; above the generated Internet block |
| `Block 10.32.5.0/24 Internet Access` | Internal; `10.32.5.0/24` | External; any | Block; all protocols; active |
| `Camera Out Test` | Internal; network Cameras | External; any | Paused |
| `Camera Access` and its return policy | Main → Cameras; Cameras → Main established/related replies | Internal | Existing local access preserved |

The exception covers `ing-wyze-cam3-47e2` (`10.32.5.30`, MAC `02:ec:09:70:47:e2`) and `ing-wyze-cam2-0304` (`10.32.5.31`, MAC `02:be:2d:75:03:04`). Both run Thingino; their installed firmware was verified through `/etc/os-release` over SSH:

| Camera IP | Firmware profile | Installed build |
|---|---|---|
| `10.32.5.30` | `wyze_cam3_t31x_gc2053_rtl8189ftv` | `stable+b6fb2ff`, built 2025-11-14 |
| `10.32.5.31` | `wyze_cam2_t20x_jxf23_rtl8189ftv` | `stable+0598a56`, built 2026-03-16 |

Thingino's [updater](https://github.com/themactep/thingino-firmware/blob/ciao/package/thingino-sysupgrade/files/sysupgrade), including the installed scripts inspected on both cameras, fetches scripts from `raw.githubusercontent.com` and firmware/checksums from GitHub releases. Camera-origin requests through each matching firmware release URL redirected to `release-assets.githubusercontent.com`. This is a host-and-port exception, not a repository or URL-path restriction: those two cameras may reach other content on the allowed GitHub hosts too. No public NTP, generic HTTPS, or Wyze cloud exception was added. Keep these IPs stable or revise the source match when renumbering the cameras.

Site Magic's local `magic_site_to_site_vpn.enabled` setting was changed from `true` to `false` and read back as disabled. The local VPN UI reports no VPN servers or Site-to-Site VPNs configured. Remote management remains enabled.

Controller API read-back verified the exact enabled flags, IP/domain/port matches, rule indexes (update allow `10003`; generated Internet block `30002`), and preserved Main-access policies. Both Thingino web interfaces and SSH remain reachable from Main; UniFi devices and both Protect cameras remained online after the policy changes. A final read-back after camera tests confirmed Site Magic disabled, the blanket override paused, and the scoped allow and generated deny active.

Camera-origin validation on 2026-10-01 (2026-10-02 UTC) used authenticated SSH, gateway DNS queries, and IPv4 `curl` requests with certificate verification, a four-second connection timeout, and a twelve-second total limit. Downloads were discarded to `/dev/null`; no updater was executed and no firmware was installed.

| Probe from each camera | `10.32.5.30` | `10.32.5.31` |
|---|---|---|
| Resolve `github.com` and `example.com` through `10.32.5.1` | DNS answers received | DNS answers received |
| GET installed updater script from `raw.githubusercontent.com` (`master` for Cam 3; `stable` for Cam 2), and current `ciao` script | HTTP 200 | HTTP 200 |
| GET bytes 0–4095 of the matching latest firmware through GitHub redirects | HTTP 206; 4096 bytes | HTTP 206; 4096 bytes |
| GET matching `.bin.sha256sum` through GitHub redirects | HTTP 200 | HTTP 200 |
| HTTPS to `example.com` and `1.1.1.1` | Connection timeout; curl exit 28 | Connection timeout; curl exit 28 |
| HTTP to `github.com` on TCP 80 | Connection timeout; curl exit 28 | Connection timeout; curl exit 28 |

### Operations and rollback

Apply through the controller:

1. In Settings → Policy Engine → Policy Table, keep `Camera Out Test` paused and maintain the scoped update allow above `Block 10.32.5.0/24 Internet Access`. Preserve `Camera Access` (Main → Cameras) and its established/related return policy. Keep Cameras' `Allow Internet Access` off; any future Internet exception must name the required source, destination, protocol, and port rather than restore blanket egress.
2. Disable the local `magic_site_to_site_vpn` setting. Network 10.6.106 redirects the SD-WAN UI to the cloud Site Manager; its local authenticated API still exposes the enable flag. Read `GET /proxy/network/api/s/default/rest/setting`, select `key: magic_site_to_site_vpn`, and submit that whole object with only `enabled` changed to `false` to `POST /proxy/network/api/s/default/set/setting/magic_site_to_site_vpn`. Use the authenticated browser session and the `x-csrf-token` response header from `GET /api/users/self` for the POST. Preserve the existing key material and other fields; do not log or commit them. Read the enable flag back after saving.
3. Reload the controller and verify the override is paused, the generated `Block 10.32.5.0/24 Internet Access` policy remains active, and Site Magic remains disabled. From each Thingino camera, confirm gateway DNS works, HTTPS requests to the updater script and a release asset succeed, and HTTPS to an unrelated Internet host times out. Confirm Main-initiated camera access still works. Test only reachability/downloads; do not invoke `sysupgrade` or flash firmware during firewall validation.

To withdraw only the update exception, pause `Allow Thingino Cameras to GitHub Updates`; the generated VLAN-wide deny remains in place. Emergency rollback is to resume the paused `Camera Out Test` policy or restore Site Magic's previous enable flag through the same authenticated API, preserving the rest of the object. Resuming the camera policy restores blanket Internet access and should be used only to recover from a demonstrated regression while a narrow exception is prepared.

## Remote Admin applied state

The Tailscale router rollout created an isolated VLAN and replaced the PVE trunk's repeated per-port overrides with one reusable port profile in UniFi Network 10.6.101:

| Item | Applied value |
|---|---|
| Network | `Remote Admin`, VLAN 19, `10.32.19.1/24` |
| Addressing | DHCP disabled; IPv6 disabled; router VMs use `10.32.19.101` and `.102` |
| Zone | Dedicated `Remote Admin` zone |
| Default zone policy | Allow to Gateway and External; block to Internal, VPN, Hotspot, DMZ, and Remote Admin |
| `pve-guest-trunk` native network | None |
| `pve-guest-trunk` tagged networks | Main (10), Remote Admin (19), Workloads (21), Storage (25), IoT (90) only |
| `pve-guest-trunk` applied ports | Lab Switch 13 `pve-sbx-1-trunk`, 14 `pve-sbx-2-trunk`, 15 `pve-sbx-3-trunk` |
| Port features | Infrastructure mode, PoE off, autonegotiation on |

The profile deliberately excludes Default, Cameras, Infra Mgmt, Guest, K8s Prod, K8s Sandbox, and Services. All three links remained up at 2.5 GbE after assignment. The matching PVE `vmbr0` allowlist is `10 19 21 25 90` on every node.

Three custom policies cross the new boundary:

| Policy | Source zone and match | Destination zone and match | Action |
|---|---|---|---|
| `Allow Main to Tailscale Routers` | `Internal`; network `Main` | `Remote Admin`; IPs `10.32.19.101`, `10.32.19.102` | Allow; IPv4; all protocols |
| `Allow Tailscale Routers to Routed LAN` | `Remote Admin`; IPs `10.32.19.101`, `10.32.19.102` | `Internal`; IPs `10.32.1.0/24`, `10.32.10.0/24`, `10.32.20.0/24`, `10.32.30.0/24`, `10.32.130.0/24`, `10.32.140.0/24` | Allow; IPv4; all protocols |
| `Allow NetBird Pilot Router to Services VIP` | `Remote Admin`; IP `10.32.19.103` | `Internal`; IP `10.32.140.1`, port 443 | Allow; IPv4; TCP |

UniFi generated an established/related return policy for each rule. No other Remote Admin → Internal initiation policy is live. Although the policy table describes the Cilium BGP pools as External for ordinary Internal sources, live probes from the custom Remote Admin zone timed out until `10.32.130.0/24` and `10.32.140.0/24` were included in the Internal destination rule; both returned HTTP 404 immediately afterward. Keep those routed prefixes in this exact rule and revalidate the behavior after UniFi upgrades.

The NetBird pilot policy belongs to the evaluation in #625 and is removed with VM 103. From `10.32.19.103`, `10.32.140.1:443` connects, while `10.32.140.1:80`, `10.32.130.1:443`, and `10.32.20.21:8006` time out. Its DNS resource `10.32.30.1` needs no policy because gateway interface addresses fall in the Gateway zone.

The Tailscale routers advertise `10.32.0.0/16`; that routing aggregate is deliberately broader than this authorization rule. A client can therefore install one stable lab route while UniFi continues to decide which destination networks traffic from `.101/.102` may actually enter. Positive tests through the aggregate reached every allowlisted class, while actual hosts in Workloads (`10.32.21.31`) and Storage (`10.32.25.5`) remained blocked. UniFi classifies traffic addressed to any of its own VLAN interface IPs in the Gateway zone, so those interface addresses remain reachable under the zone's gateway allow even when their attached networks are absent from the Internal allowlist.

Positive validation from both router VMs reached the Default and Main gateways, PVE at `10.32.20.21:8006` (HTTP 200), the Kubernetes API at `10.32.30.8:6443` (HTTP 401 without credentials), and the two Traefik VIPs at `10.32.130.1:443` and `10.32.140.1:443` (HTTP 404 without a host match). Before this allow existed, both routers were unable to ping PVE management or connect to its UI.

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
| `Allow Hermes to MetaMCP` | `Internal`; IP `10.32.21.100` | `External`; IP `10.32.130.1` | Allow; TCP 443 |
| `Block Workloads to Protected Networks` | `Internal`; network `Workloads` | `Internal`; networks `Infra Mgmt`, `K8s Prod`, `Storage` | Block; all protocols |
| `Block Workloads to Admin Prod Routed` | `Internal`; network `Workloads` | `External`; IP `10.32.130.0/24` | Block; all protocols |
| `Block K8s Prod to Workloads` | `Internal`; network `K8s Prod` | `Internal`; network `Workloads` | Block; all protocols |

The legacy-named `Allow Hermes to MetaMCP` rule also carries MCPHub traffic through the same admin-gateway IP and remains needed after MetaMCP retirement. It is ordered immediately above `Block Workloads to Admin Prod Routed`; all other Workloads clients remain denied from `admin-prod`. An unauthenticated HTTPS probe from Hermes reached MetaMCP and returned HTTP 401, while the earlier disposable-guest test remained blocked.

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

These rules implement the PVE-specific slice of the network-topology matrix. They do not make the complete phase-2 firewall posture applied: the broader Main, IoT, Guest, Cameras, Spark inference, and BGP-pool restrictions still require their own implementation and negative tests.

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

## Firewall rules

UniFi default inter-VLAN posture is allow, so the BGP LB pool prefixes need explicit denies from untrusted VLANs. The IoT and Guest rules in this section remain unapplied intent; the separately documented Workloads-to-`admin-prod` rule is live. These rules are configured in the UniFi UI, and this section is the source of truth because there is no committable controller artifact.

**Network object:** `bgp-lb-restricted`

| Member          | Notes                                          |
|-----------------|------------------------------------------------|
| `10.32.130.0/24` | `admin-prod` pool                              |
| `10.32.140.0/24` | `services-prod` pool (created in step 9)       |

`shared-prod` (10.32.150.0/24) is intentionally excluded — its tenants use
per-IP+port policy, not a pool-wide deny. Its first allocation is Visionect at
`10.32.150.30`.

**Rules** (Settings → Security → Traffic Rules, or the version-equivalent
LAN-IN section):

| # | Source            | Destination        | Action | Notes                          |
|---|-------------------|--------------------|--------|--------------------------------|
| 1 | IoT (VLAN 90)     | `bgp-lb-restricted` | Drop   | Quieter than reject            |
| 2 | Guest (VLAN 99)   | `bgp-lb-restricted` | Drop   |                                |

VLAN 10 (Main) is intentionally allowed by the default posture and needs no
explicit rule. If/when a more restrictive default-deny posture is adopted
across the network, replace these denies with the corresponding allows from
Main and revisit the per-pool firewall posture in
[`docs/architecture.md`](../../docs/architecture.md#lb-pool-allocation).

### shared-prod tenant rules

Shared-pool policy is explicit per tenant. Keep these Visionect rules ordered
above the final deny for `10.32.150.30`:

| # | Source | Destination | Port | Action | Purpose |
|---|---|---|---|---|---|
| 1 | IoT (VLAN 90) | `10.32.150.30` | TCP 11113 | Allow | Visionect device protocol |
| 2 | Main (VLAN 10) | `10.32.150.30` | TCP 443 | Allow | HTTPS management UI |
| 3 | Internal client networks | `10.32.150.30` | Any | Drop | Fail closed on every other path |

The Cilium policy on the destination pod repeats the same source/port boundary.
The UniFi rules remain required because they prevent disallowed traffic from
reaching the cluster at all.

Validate by running `curl --max-time 2 http://10.32.130.99/` from a device on
each restricted VLAN — should time out or be refused. The synthetic test
below exercises this as gate 4.

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
   Requires the rules in "Firewall rules" above to be applied first.
5. **Failover** — `talosctl -n 10.32.30.11 reboot`. From VLAN 10, run a
   continuous `curl` loop; at most one or two requests should fail before
   ECMP reconverges on the remaining 2 next-hops.
   `vtysh -c 'show ip route 10.32.130.99'` should show 2 next-hops during
   the reboot and 3 again after it returns.

Tear down only when all five pass:

```sh
kubectl delete -f network/unifi/bgp-test.yaml
```
