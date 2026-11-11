# NetBird remote access

NetBird is Matt's selected replacement for Tailscale. It has two routing peers on the isolated Remote Admin VLAN: VM 103, `netbird-router-1`, at `10.32.19.103` on `pve-sbx-1`, and VM 104, `netbird-router-2`, at `10.32.19.104` on `pve-sbx-2`. [#625](https://github.com/kelchm/home-lab/issues/625) qualified it on 2026-11-08 ([evidence](https://github.com/kelchm/home-lab/issues/625#issuecomment-6729474432)); its remaining checks are left to normal use. NetBird Cloud and UniFi policy are applied manually; merging this document changes neither. The routing peers' definitions, rebuild and enrollment are in [`proxmox/guests/netbird-router`](../../proxmox/guests/netbird-router/README.md) and [`proxmox/guests/netbird-router-2`](../../proxmox/guests/netbird-router-2/README.md).

Tailscale infrastructure retirement completed on October 29, 2026: the router VMs and disks, UniFi policies, route approvals, seven machine registrations and tailnet are gone. The [retirement record](../plans/20260902-tailscale-remote-admin.md#retirement-record-2026-10-29) preserves the archive limits and distinguishes Hyperion's verified local logout from the four personal clients whose local logout remains unverified in [#1069](https://github.com/kelchm/home-lab/issues/1069). NetBird remains the single routed remote path, with the same household HTTPS, admin HTTPS and split-DNS scope; retirement adds no NetBird qualification or host-boot acceptance.

## Current configuration

The `pilot` prefix was dropped on 2026-11-08: first the NetBird labels, then VM 103's name, hostname, snippet and NetBird peer name. NetBird objects are referenced by ID, so the renames changed no access.

### Verified on 2026-10-27

These were read from the routing peer through `qm guest exec`, from Hyperion, and from UniFi's read-only API:

- **Routing peer:** VM 103 runs NetBird 0.79.0, connected to NetBird Cloud management (`api.netbird.io`), and routes `10.32.130.1/32`, `10.32.140.1/32` and `10.32.30.1/32`. Forwarding from `wt0` drops by default. With no client passing the posture check, the forward chain held only its established/related accept.
- **UniFi:** `Allow NetBird Pilot Router to Services VIP` allowed `10.32.19.103` → `10.32.140.1` and `Allow NetBird Pilot Router to Admin HTTPS` allowed `10.32.19.103` → `10.32.130.1`. On 2026-11-08 they were renamed `NetBird to Services Ingress` and `NetBird to Admin Ingress`, with the source and destinations moved into the `NetBird Routers`, `Services Ingress` and `Admin Ingress` Network Lists. Both are TCP 443 with generated established/related returns. The [UniFi reference](../../network/unifi/README.md#remote-admin-applied-state) has the details.
- **Gateway addresses:** the Remote Admin zone's default Gateway allow lets VM 103 reach every UniFi gateway address, including the console on `10.32.30.1:443`. Only NetBird policy stops a client from reaching that port through the DNS resource.
- **Hyperion's DNS:** Hyperion's NetBird resolver forwards `home.kelch.io` to `10.32.30.1`. UniFi answers the same records at `10.32.10.1`, so name resolution at home does not depend on NetBird.

### NetBird Cloud, read back on 2026-11-08

Read with the Auditor token before and after the 2026-11-08 changes; the exports matched apart from names, Persephone's membership and the deletions below:

| Kind | Name | Definition |
|---|---|---|
| Account | — | Overlay `100.64.0.0/16`, with an IPv6 overlay for group `All`; peer login and inactivity expiration off; lazy connections on; client auto-update off; peer approval off |
| Group | `All` | All five peers |
| Group | `clients` | Peers `Hyperion`, `iPhone-kelchm` and `Persephone`; auto-assigned to peers that Matt's user adds |
| Group | `routers` | Peers `netbird-router-1` and `netbird-router-2` |
| Group | `home-resources` | Resources `jellyfin-vip` and `home-dns`; no policy names this group |
| Group | `admin-resources` | Resource `admin-https`; no policy names this group |
| Posture check | `exclude-home-main` | Peer network range: deny `10.32.10.0/24` |
| Network | `home` | Routed by group `routers`, metric 9999, masquerade |
| Resource | `jellyfin-vip` | `10.32.140.1/32` (household services) |
| Resource | `home-dns` | `10.32.30.1/32` (split DNS) |
| Resource | `admin-https` | `10.32.130.1/32` (admin HTTPS) |
| Policy | `mesh` | `clients` ↔ `clients`, all protocols, no posture check |
| Policy | `jellyfin` | `clients` → resource `jellyfin-vip`, TCP 443, with the posture check |
| Policies | `home-dns-udp`, `home-dns-tcp` | `clients` → resource `home-dns`, UDP and TCP 53, with the posture check |
| Policy | `admin-https` | `clients` → resource `admin-https`, TCP 443, with the posture check |
| Policy | `Default` | All ↔ All, disabled |
| DNS | `home-kelch-io` | `home.kelch.io` → `10.32.30.1:53` over UDP for `clients`; not the primary resolver |
| Setup key | `netbird-router-2` | One-off, used once on 2026-11-08; revoked |
| Service user | `netbird-export` | Auditor (read-only); holds the read-back token |

The original `Automation` admin service user, whose token expired on 2026-10-27, and the expired `netbird-pilot-router` setup key were deleted on 2026-11-08; the old token's 1Password item is archived. Dashboard changes need an owner browser session.

The peers are `netbird-router-1` and `netbird-router-2` (Debian 13, NetBird 0.82.1), `Hyperion`, `iPhone-kelchm` and `Persephone`. `Persephone`, Matt's older personal MacBook Pro, joined `clients` on 2026-11-08 so that all his personal machines have the same access, and passed an away check that day. Its stored per-peer login-expiration flag is still set but has no effect while the account-level setting is off.

### GLKVM console, added 2026-11-11

The recovery console at `10.32.20.10` is reachable away from home without the PVE routers: the appliance is its own NetBird peer and routes only its own address. The appliance side is in the [GLKVM runbook](../../devices/glkvm/README.md#netbird). Read back with the Auditor export after creation:

| Kind | Name | Definition |
|---|---|---|
| Group | `kvm` | Peer `kvm-compute-rack` |
| Group | `kvm-resources` | Resource `kvm-console`; no policy names this group |
| Network | `kvm` | Routed by group `kvm`, metric 9999, masquerade |
| Resource | `kvm-console` | `10.32.20.10/32` |
| Policy | `kvm-console` | `clients` → resource `kvm-console`, TCP 443 and 22, with the posture check |
| Setup key | `kvm-compute-rack` | One-off, auto-group `kvm`, used once on 2026-11-11; revoked |

Peer `kvm-compute-rack` (`kvm-compute-rack.netbird.cloud`) runs the firmware's NetBird client with kernel WireGuard; it is in `All` and `kvm`, with login expiration and NetBird SSH off. Away clients resolve `kvm-compute-rack.home.kelch.io` through the `home-dns` resource, which the PVE routers carry; if both are down, browse to `https://10.32.20.10/` instead, which still routes through the appliance itself.

Every enabled resource policy names its resource directly, and no policy names a resource group. NetBird therefore grants clients only TCP 443 to `10.32.140.1` and `10.32.130.1` and port 53 to `10.32.30.1`; nothing grants TCP 443 to the UniFi console on `10.32.30.1`. VM 103 itself can reach that port, so the routing peer's per-port filter is what blocks clients, and #625's I1 check verified that from the iPhone away from home on 2026-11-08. The 0.82.0 routers no longer show NetBird's rules in iptables, so the filter was rechecked from a client after the upgrade: on 2026-11-10 Persephone, away from home, got no answer from `10.32.30.1:443`, `10.32.140.1:80` or `10.32.20.21:8006`, while Jellyfin on `10.32.140.1:443` answered. `home-resources` holds both the household-services and DNS resources, so a policy naming that group would open its ports on both addresses, the console included. Target resources directly.

## Read back NetBird Cloud

[`scripts/netbird-export.sh`](../../scripts/netbird-export.sh) exports the account, groups, posture checks, policies, DNS, peers, setup keys, service users, networks, resources and routers. It writes them to a new private temporary directory and prints its path. The script makes only `GET` requests and removes setup-key values. It reads the token from `NETBIRD_TOKEN_FILE` if set, otherwise from `op://Project - home-lab/netbird-export/credential`. That token belongs to the `netbird-export` service user, whose Auditor role can read every NetBird configuration and change nothing. It was created on 2026-10-28 and expires on 2027-10-28; replace it before then.

The export also contains peer addresses and IDs. Compare it against the table above, record the differences and the verified state in the issue for the change, and keep the raw files out of Git and issue comments. Run it before and after every NetBird Cloud change.

The script exits non-zero if any request fails. Its first full run, on 2026-10-28, succeeded for every endpoint; the table above is from the 2026-11-08 runs. The Auditor token gets HTTP 403 from `users/current` and from other users' token lists, neither of which the script reads.

## Clients and upgrades

Read back on 2026-11-08:

| Peer | Installed from | Version | Upgrade owner and effect |
|---|---|---|---|
| `netbird-router-1`, `netbird-router-2` | Debian package from `pkgs.netbird.io` `stable`; vendor data installs it at first boot | 0.82.1 | Renovate opens a PR for each NetBird release against [`upgrade-routers.sh`](../../proxmox/guests/netbird-router/upgrade-routers.sh). Merging changes no router; running the script upgrades them one at a time, and away clients move to the other router ([procedure](../../proxmox/guests/netbird-router/README.md#upgrades)). |
| Hyperion | Homebrew `netbirdio/tap/netbird` (daemon and CLI) and cask `netbirdio/tap/netbird-ui`; LaunchDaemon `netbird` runs `/opt/homebrew/bin/netbird service run` | 0.81.5, daemon and CLI read back on 2026-11-10 | Operator, with `brew upgrade`. A `netbird-ui` cask upgrade removes and reinstalls the service; an interrupted one on 2026-10-28 left no daemon until the upgrade was rerun. Check `netbird status` after upgrading. |
| Persephone | — | 0.82.0, reported by management | Operator |
| iPhone | App Store | Management reports build `ci-cb7ca8ef` | App Store updates |

NetBird's client auto-update setting is off, so no client upgrades itself through NetBird.

The iPhone app needs two settings. **Connect on demand** is on, with no per-network rules; home versus away is decided by the posture check, not the phone. **Force relay connection** (Settings → Advanced) is off. The iOS app defaults it to on to save battery, and with it on the phone never offers direct connections: every connection went through NetBird's relay, which held Jellyfin to about 6–8 Mbit/s on 2026-11-08, against 25–35 Mbit/s direct after turning it off. Changing it takes a reconnect.

Hyperion's daemon plist sets `RunAtLoad: false` and `KeepAlive: true`. On 2026-10-19 the daemon started 16 minutes after boot and 3 seconds before the UI; whether it connects before anyone logs in is unverified, so check `netbird status` after a reboot. NetBird switched off from the menu bar stays off, with no error: on 2026-11-08 Hyperion was found disconnected since about November 5.

## Verified baseline

On October 19, 2026, the iPhone was connected to the routing peer through a NetBird relay. The routing peer advertised only `10.32.140.1/32` (household services) and `10.32.30.1/32` (DNS). From that VM, `https://jellyfin.home.kelch.io/health` returned HTTP 200, while `https://grafana.home.kelch.io/api/health` timed out. From Main, Grafana returned HTTP 200 with `database: ok`, and its pod was Running with all three containers Ready.

Grafana resolves to the admin gateway, `10.32.130.1`, which the original pilot excluded. A connected VPN and working split DNS therefore did not establish access to Grafana. The [UniFi policy reference](../../network/unifi/README.md#remote-admin-applied-state) records the current allowances.

## Extend access to admin web apps

The admin HTTPS extension was applied on October 19, 2026: NetBird resource `admin-https` at `10.32.130.1/32` is in its dedicated `admin-resources` group, with the direct-resource policy `admin-https` from `clients` for TCP 443 and the existing `exclude-home-main` posture check. The matching UniFi allowance and paired established/related return rule were created through an administrator browser session and read back with the exact single-IP source and destination. The original services policy was preserved. The October 19 VM checks passed, and Matt's successful remote iPhone acceptance is recorded below.

The extension adds access to every matching HTTPS route on `gateway-admin`, including Grafana and MCPHub's `/mcp` and `/sse` transports. The address and port cannot isolate Grafana from those other routes. Application authentication continues to apply: Grafana uses its native Kanidm login, while MCPHub requires its existing bearer keys. Per-node management endpoints such as Proxmox, other ports and other destination IPs are outside this extension.

1. Authenticate to NetBird Cloud and UniFi with an administrator account. The names below are the current ones; on 2026-10-19 they carried a `pilot-` prefix. The saved `Unifi Console` account has Network read scope and returns HTTP 403 `No permission` for firewall writes; use an administrator browser session. Read back the existing network, resource, policy, posture-check and firewall objects, and retain their configuration privately before changing them. Reuse matching existing objects; the steps below describe recreating the extension when absent.
2. Create a dedicated resource group, `admin-resources`, and confirm that no existing policy names it as a destination. In the existing `home` NetBird network, add the enabled resource `admin-https` at `10.32.130.1/32`, with only that dedicated group in its `groups` field. Retain the existing routing-peer group and masquerading. Do not change the DNS or household-service resources.
3. Add a policy from `clients` directly to the new resource (`destinationResource`) for TCP 443 only. Use the same `exclude-home-main` posture check as the household-service policy: block clients on `10.32.10.0/24`, so home traffic uses the LAN. Keep the unrestricted peer policy separate and the account's default All-to-All policy disabled. Read all policies back to confirm that no other resource-group or direct-resource policy grants access to the new resource.
4. In UniFi, add the admin forward (now named `NetBird to Admin Ingress`): source zone `Remote Admin`, source list `NetBird Routers` (then a literal `10.32.19.103`); destination zone `Internal`, destination list `Admin Ingress` (`10.32.130.1`); IPv4 TCP destination port 443; allow. Match the placement and established/related return handling of the services forward (now `NetBird to Services Ingress`). Privately record the new forward-rule ID and the return-rule ID paired with it. Preserve the existing policy and all other zone restrictions.
5. Read both control planes back and verify that the resource and policies match those exact addresses, clients, posture check and port. Verify the literal `/32` or single-IP destination, rather than inferring it from connection tests. The admin address must not become an unrestricted LAN route.
6. Update the applied-state policy table and probe results in `network/unifi/README.md`, the `.103` access description in `docs/architecture.md`, and this runbook's extension status as each change is applied. Record the outcome and remaining qualification work in #625. Distinguish configuration and VM validation from the remote iPhone acceptance checks below.

NetBird's [Networks API](https://docs.netbird.io/api/resources/networks) and [Policies API](https://docs.netbird.io/api/resources/policies) describe the corresponding objects. Prefer cloning the shape of the live household HTTPS policy with only the name and destination resource changed, after checking its scope. UniFi's routed-prefix classification for this source zone is documented in the applied-state reference; do not widen the destination zone or prefix to work around a failed probe.

## Manage the routing peers

VM 103 runs on `pve-sbx-1` (`10.32.20.21`) and VM 104 on `pve-sbx-2` (`10.32.20.22`). Manage each through PVE's QEMU guest agent on its host; for 104, use `pve-sbx-2` and VMID `104` in the commands below. From an admin device on Main, connect with `ssh kelchm@pve-sbx-1`, then run:

```sh
sudo qm guest exec 103 -- hostname
sudo qm guest exec 103 -- netbird status
```

This management path was verified on October 27: guest exec returned the VM's hostname and NetBird status. Inspect the returned guest `exitcode` and `out-data`/`err-data`; a successful PVE request alone does not establish that the guest command succeeded. Main cannot reach VM 103's SSH directly, and the retired Tailscale routers are no longer a management hop. It is included in the nightly `daily-backups` job and has PVE `protection`. To restore, rebuild or re-enroll it, follow its [guest definition](../../proxmox/guests/netbird-router/README.md).

NetBird does not expose PVE management. Recovery requires a reachable admin session on Main; without one, an away operator must wait for local access to repair the routing peer or its host. Arrange that access before any disruptive test. Native peer access to a host on Main is separate from the routed resources, but has not been qualified as a recovery path.

For later application diagnostics, run the probe inside VM 103 through the same management path:

```sh
sudo qm guest exec 103 -- curl --fail --show-error --max-time 10 https://grafana.home.kelch.io/api/health
```

## Router startup dependency

VM 103 saves `vendor=local:snippets/netbird-router-1.yaml` since its 2026-11-08 rename; its root, EFI and cloud-init disks are also local. On 2026-10-25 its host-boot start failed with `storage 'library-pve' is not online`; the owner-authorized `qm start 103` around 18:24:31 UTC was manual recovery, so it does not credit #625/P6. Retired VM 101 showed the same dependency on another node; retired VMs 101/102 are historical evidence, not failover peers.

The [node-local startup installation](../../proxmox/guests/netbird-router/local-startup.md#accepted-installation-2026-10-29) for [#1096](https://github.com/kelchm/home-lab/issues/1096) was separately authorized, completed and verified on 2026-10-29 at 11:56 UTC. Only the saved vendor reference/digest changed; the byte-identical file, generated user/network/meta hashes, hostname, instance ID, enrolled peer, NetBird version and running QEMU process were preserved. The installation left the running cloud-init medium unchanged, with the local vendor pending until a normal start; the 2026-11-11 readbacks confirmed both saved and image vendor references local, with no pending changes. Enabling snippets on `local` retained all previous cluster-wide content flags; the file itself must be provisioned on every chosen migration/restore node. Keep the old NAS snippet for older archives, including the verified post-enrollment backup that predates this installation. Backup completion does not establish identity recovery; restore remains undrilled.

Unattended host startup passed on 2026-11-11 during the owner-authorized clean reboot of `pve-sbx-1`: VM 103 started about 23 seconds after the 01:58:07 UTC host boot through PVE start-all, with unchanged identity, version and cloud-init hashes and Management/Signal connected. VM 200 also started automatically; VM 104 was connected before and after. The journal recorded a natural `library-pve` availability delay after VM 103 had started, without a snippet-storage failure. See [acceptance and its limits](../../proxmox/guests/netbird-router/local-startup.md#accepted-host-boot-2026-11-11) and [#1096](https://github.com/kelchm/home-lab/issues/1096#issuecomment-6784794380). Shared NAS/NFS was not disrupted. Client failover after losing a host has not been timed.

## Second router and qualification

VM 104 was installed and enrolled on 2026-11-08 by its [definition and installation procedure](../../proxmox/guests/netbird-router-2/README.md) (#1097). Both peers are in the `routers` group with metric 9999 and masquerade, `10.32.19.104` is in UniFi's `NetBird Routers` list, and 104's router-side allowed and blocked paths match 103's. Moving a client between routers resets its open TCP connections; a Main client such as Hyperion has no resource routes because of the posture check.

Later that day both routers were upgraded from 0.79.0 to 0.82.0, and VM 103 was renamed and rebooted twice. Each router went down in turn, and the away iPhone and Persephone moved to the other one without help. Failover timing was not measured; the [two-peer qualification proposal](../../proxmox/guests/netbird-router-2/qualification.md) records the bounds a formal test would use.

#625 qualified NetBird on 2026-11-08 and closed by owner decision: the iPhone's five-round home and away switching, its allowed and blocked addresses, T3 over NetBird and Persephone's away check passed, and the remaining checks (sleep and wake, client restarts, Hyperion on a hotspot, measured failover) are left to normal use. VM 103 host-boot startup was subsequently verified on 2026-11-11 under #1096. Reopen it, or file a new issue, if one of them fails.

## Verification and owner acceptance

To tell whether a client's traffic went through a router, use the router's peer list (`netbird status -d` in the VM) and a capture on its `wt0`. Traefik's `ClientHost` is not reliable evidence: `traefik-services` uses `externalTrafficPolicy: Cluster`, so a request can be logged with a cluster node's address instead of the client's or router's. On 2026-11-08 a phone flow through the router was logged as `10.32.30.13`.

For future changes, Grafana's `/api/health` from the VM must return HTTP 200 with a healthy database, and Jellyfin's `/health` must still return HTTP 200. Use HTTPS with certificate validation. Connection attempts to `10.32.130.1:80`, `10.32.140.1:80`, and `10.32.20.21:8006` must remain blocked. These VM tests verify the UniFi path; they do not prove that a remote client received the NetBird resource or policy.

The October 19 post-apply VM checks passed: Grafana returned HTTP 200 with `database: ok`, Jellyfin returned HTTP 200, and all three negative connections above timed out. The adjacent-address TCP DNS check below also timed out from the VM while answering from Main. The operator Mac on Main (Hyperion) still reported no NetBird resource routes.

As an adjacent-address check, first confirm that k8s-gateway at `10.32.130.2` answers a TCP DNS query from Main, then confirm that the same query times out from the routing VM. This detects unintended DNS access. It does not prove that TCP 443 is limited to one IP: k8s-gateway has no HTTPS listener, so a failed connection to its port 443 cannot distinguish a correct `/32` rule from an overbroad prefix rule. The exact destination readback remains required.

Owner acceptance recorded October 27, 2026: Matt confirmed **“Yes, login and dashboards work”** on the iPhone over NetBird while away from home. This completes acceptance of the Grafana admin HTTPS extension. It is owner-reported application evidence, separate from the October 19 VM probes. A packet capture is an optional diagnostic tool for later troubleshooting, not a condition on this accepted result.

Broader resource access, such as SSH to the PVE nodes from away, is not granted; see #625's decision 5 for when to revisit it.

## Roll back the admin extension

Disable the new NetBird admin HTTPS policy first, then disable the new UniFi forward rule and its paired return rule using the IDs recorded during application. Do not select return rules by name alone. Remove the new resource and policy, and the dedicated resource group if unused, after confirming that the original household HTTPS and DNS resources remain present and functional. Leave the original services policy, split DNS, peer policy and posture check intact. Verify that remote admin HTTPS is withdrawn while Jellyfin and native peer access continue to work, and update the same applied-state references with the withdrawal.

VMs 103 and 104 are the only routed remote path. Retain them and the household HTTPS, DNS and peer access when rolling back this admin-only extension.
