# NetBird remote access

NetBird is Matt's selected replacement for Tailscale. Its routing peer is VM 103, `netbird-pilot-router`, at `10.32.19.103` on the isolated Remote Admin VLAN. Setup and comprehensive testing remain unfinished in [#625](https://github.com/kelchm/home-lab/issues/625). Tailscale was retired on October 25, 2026; the remaining retirement work is tracked in [#1069](https://github.com/kelchm/home-lab/issues/1069). NetBird Cloud and UniFi policy are applied manually; merging this document changes neither. The routing peer's definition, rebuild and enrollment are in [`proxmox/guests/netbird-router`](../../proxmox/guests/netbird-router/README.md).

## Current configuration

The names below are current. Dropping `pilot` from them is an open decision in #625.

### Verified on 2026-10-27

These were read from the routing peer through `qm guest exec`, from Hyperion, and from UniFi's read-only API:

- **Routing peer:** VM 103 runs NetBird 0.79.0, connected to NetBird Cloud management (`api.netbird.io`), and routes `10.32.130.1/32`, `10.32.140.1/32` and `10.32.30.1/32`. Forwarding from `wt0` drops by default. With no client passing the posture check, the forward chain held only its established/related accept.
- **UniFi:** `Allow NetBird Pilot Router to Services VIP` allows `10.32.19.103` → `10.32.140.1` and `Allow NetBird Pilot Router to Admin HTTPS` allows `10.32.19.103` → `10.32.130.1`. Both are TCP 443 with generated established/related returns. The [UniFi reference](../../network/unifi/README.md#remote-admin-applied-state) has the details.
- **Gateway addresses:** the Remote Admin zone's default Gateway allow lets VM 103 reach every UniFi gateway address, including the console on `10.32.30.1:443`. Only NetBird policy stops a client from reaching that port through the DNS resource.
- **Hyperion's DNS:** Hyperion's NetBird resolver forwards `home.kelch.io` to `10.32.30.1`. UniFi answers the same records at `10.32.10.1`, so name resolution at home does not depend on NetBird.

### NetBird Cloud objects as last recorded

The API token at `op://Private/netbird-automation/credential` returned `401 token invalid` on 2026-10-27, so these objects have not been re-verified. They come from the 2026-09-27 setup record and the 2026-10-19 admin extension in #625. Re-verify them with the read-back below before relying on them.

| Kind | Name | Definition |
|---|---|---|
| Account | — | Overlay `100.64.0.0/16`; peer login and inactivity expiration off; lazy connections on; client auto-update off |
| Group | `pilot-clients` | Hyperion and the iPhone |
| Group | `pilot-routers` | `netbird-pilot-router` |
| Group | `pilot-home-resources` | Resource group from the original setup; its members are not recorded |
| Group | `pilot-admin-resources` | `pilot-admin-https` only |
| Posture check | `pilot-exclude-home-main` | Peer network range: block `10.32.10.0/24` |
| Network | `pilot-home` | Routed by group `pilot-routers`, metric 9999, masquerade |
| Resources | Names not recorded | `10.32.140.1/32` (household services) and `10.32.30.1/32` (split DNS) |
| Resource | `pilot-admin-https` | `10.32.130.1/32` |
| Policy | `pilot-mesh` | `pilot-clients` ↔ `pilot-clients`, all protocols, no posture check |
| Policy | `pilot-jellyfin` | `pilot-clients` → household services, TCP 443, with the posture check |
| Policies | `pilot-home-dns-udp`, `pilot-home-dns-tcp` | `pilot-clients` → DNS, port 53, with the posture check |
| Policy | Name not recorded | `pilot-clients` → `pilot-admin-https` as a direct resource, TCP 443, with the posture check |
| Policy | `Default` | All ↔ All, disabled |
| DNS | Nameserver group, name not recorded | `home.kelch.io` → `10.32.30.1:53` for `pilot-clients` |

The record doesn't say whether `pilot-jellyfin` and the DNS policies target the `pilot-home-resources` group or each resource directly. If they target the group, TCP 443 is open to the UniFi console on `10.32.30.1` and port 53 to `10.32.140.1`. Check this first in the next read-back.

## Read back NetBird Cloud

[`scripts/netbird-export.sh`](../../scripts/netbird-export.sh) exports the account, groups, posture checks, policies, DNS, peers, setup keys, service users, networks, resources and routers. It writes them to a new private temporary directory and prints its path. The script makes only `GET` requests and removes setup-key values. It reads the token from `NETBIRD_TOKEN_FILE` if set, otherwise from 1Password.

The export also contains peer addresses and IDs. Compare it against the table above, record the differences and the verified state in #625, and keep the raw files out of Git and issue comments. Run it before and after every NetBird Cloud change.

With an invalid token, every request fails with HTTP 401 and the script exits non-zero. The full export has not yet been run against a valid token.

## Clients and upgrades

Read back on 2026-10-27:

| Peer | Installed from | Version | Upgrade owner and effect |
|---|---|---|---|
| `netbird-pilot-router` | Debian package from `pkgs.netbird.io` `stable`, installed by vendor data | 0.79.0; 0.81.5 available | Operator, by hand through guest exec ([procedure](../../proxmox/guests/netbird-router/README.md#upgrades)). Away clients lose the resources until the daemon reconnects. |
| Hyperion | Homebrew `netbirdio/tap/netbird` (daemon and CLI) and cask `netbirdio/tap/netbird-ui`; LaunchDaemon `netbird` runs `/opt/homebrew/bin/netbird service run` | Running daemon 0.80.0; installed formula 0.81.4; UI 0.80.0; 0.81.5 available | Operator, with `brew upgrade`. The running daemon keeps its old version until `sudo netbird service restart` or a reboot. |
| iPhone | App Store | iOS core 0.75.0 as of 2026-09-27; not re-read | App Store updates |

NetBird's client auto-update setting is off, so no client upgrades itself through NetBird.

Hyperion's daemon plist sets `RunAtLoad: false` and `KeepAlive: true`. On 2026-10-19 the daemon started 16 minutes after boot and 3 seconds before the UI. Whether it connects before anyone logs in is unverified; #625 tests it.

## Verified baseline

On October 19, 2026, the iPhone was connected to the routing peer through a NetBird relay. The routing peer advertised only `10.32.140.1/32` (household services) and `10.32.30.1/32` (DNS). From that VM, `https://jellyfin.home.kelch.io/health` returned HTTP 200, while `https://grafana.home.kelch.io/api/health` timed out. From Main, Grafana returned HTTP 200 with `database: ok`, and its pod was Running with all three containers Ready.

Grafana resolves to the admin gateway, `10.32.130.1`, which the original pilot excluded. A connected VPN and working split DNS therefore did not establish access to Grafana. The [UniFi policy reference](../../network/unifi/README.md#remote-admin-applied-state) records the current allowances.

## Extend access to admin web apps

The admin HTTPS extension was applied on October 19, 2026: NetBird resource `pilot-admin-https` at `10.32.130.1/32` is in its dedicated `pilot-admin-resources` group, with a direct-resource policy from `pilot-clients` for TCP 443 and the existing `pilot-exclude-home-main` posture check. The matching UniFi allowance and paired established/related return rule were created through an administrator browser session and read back with the exact single-IP source and destination. The original services policy was preserved. The October 19 VM checks passed, and Matt's successful remote iPhone acceptance is recorded below.

The extension adds access to every matching HTTPS route on `gateway-admin`, including Grafana and MCPHub's `/mcp` and `/sse` transports. The address and port cannot isolate Grafana from those other routes. Application authentication continues to apply: Grafana uses its native Kanidm login, while MCPHub requires its existing bearer keys. Per-node management endpoints such as Proxmox, other ports and other destination IPs are outside this extension.

1. Authenticate to NetBird Cloud and UniFi with an administrator account. The saved `Unifi Console` account has Network read scope and returns HTTP 403 `No permission` for firewall writes; use an administrator browser session. Read back the existing network, resource, policy, posture-check and firewall objects, and retain their configuration privately before changing them. Reuse matching existing objects; the steps below describe recreating the extension when absent.
2. Create a dedicated resource group, `pilot-admin-resources`, and confirm that no existing policy names it as a destination. In the existing `pilot-home` NetBird network, add the enabled resource `pilot-admin-https` at `10.32.130.1/32`, with only that dedicated group in its `groups` field. Retain the existing routing-peer group and masquerading. Do not change the DNS or household-service resources.
3. Add a policy from `pilot-clients` directly to the new resource (`destinationResource`) for TCP 443 only. Use the same `pilot-exclude-home-main` posture check as the household-service policy: block clients on `10.32.10.0/24`, so home traffic uses the LAN. Keep the unrestricted peer policy separate and the account's default All-to-All policy disabled. Read all policies back to confirm that no other resource-group or direct-resource policy grants access to the new resource.
4. In UniFi, add `Allow NetBird Pilot Router to Admin HTTPS`: source zone `Remote Admin`, source IP `10.32.19.103`; destination zone `Internal`, destination IP `10.32.130.1`; IPv4 TCP destination port 443; allow. Match the placement and established/related return handling of `Allow NetBird Pilot Router to Services VIP`. Privately record the new forward-rule ID and the return-rule ID paired with it. Preserve the existing policy and all other zone restrictions.
5. Read both control planes back and verify that the resource and policies match those exact addresses, clients, posture check and port. Verify the literal `/32` or single-IP destination, rather than inferring it from connection tests. The admin address must not become an unrestricted LAN route.
6. Update the applied-state policy table and probe results in `network/unifi/README.md`, the `.103` access description in `docs/architecture.md`, and this runbook's extension status as each change is applied. Record the outcome and remaining qualification work in #625. Distinguish configuration and VM validation from the remote iPhone acceptance checks below.

NetBird's [Networks API](https://docs.netbird.io/api/resources/networks) and [Policies API](https://docs.netbird.io/api/resources/policies) describe the corresponding objects. Prefer cloning the shape of the live household HTTPS policy with only the name and destination resource changed, after checking its scope. UniFi's routed-prefix classification for this source zone is documented in the applied-state reference; do not widen the destination zone or prefix to work around a failed probe.

## Manage VM 103

VM 103 runs on `pve-sbx-1` (`10.32.20.21`). Manage it through PVE's QEMU guest agent. From an admin device on Main, connect with `ssh kelchm@pve-sbx-1`, then run:

```sh
sudo qm guest exec 103 -- hostname
sudo qm guest exec 103 -- netbird status
```

This management path was verified on October 27: guest exec returned the VM's hostname and NetBird status. Inspect the returned guest `exitcode` and `out-data`/`err-data`; a successful PVE request alone does not establish that the guest command succeeded. Main cannot reach VM 103's SSH directly, and the retired Tailscale routers are no longer a management hop. To rebuild or re-enroll the VM, follow its [guest definition](../../proxmox/guests/netbird-router/README.md).

NetBird does not expose PVE management. Recovery requires a reachable admin session on Main; without one, an away operator must wait for local access to repair the routing peer or its host. Arrange that access before any disruptive test in #625. Native peer access to a host on Main is separate from the routed resources, but has not been qualified as a recovery path.

For later application diagnostics, run the probe inside VM 103 through the same management path:

```sh
sudo qm guest exec 103 -- curl --fail --show-error --max-time 10 https://grafana.home.kelch.io/api/health
```

## Verification and owner acceptance

For future changes, Grafana's `/api/health` from the VM must return HTTP 200 with a healthy database, and Jellyfin's `/health` must still return HTTP 200. Use HTTPS with certificate validation. Connection attempts to `10.32.130.1:80`, `10.32.140.1:80`, and `10.32.20.21:8006` must remain blocked. These VM tests verify the UniFi path; they do not prove that a remote client received the NetBird resource or policy.

The October 19 post-apply VM checks passed: Grafana returned HTTP 200 with `database: ok`, Jellyfin returned HTTP 200, and all three negative connections above timed out. The adjacent-address TCP DNS check below also timed out from the VM while answering from Main. The operator Mac on Main (Hyperion) still reported no NetBird resource routes.

As an adjacent-address check, first confirm that k8s-gateway at `10.32.130.2` answers a TCP DNS query from Main, then confirm that the same query times out from the routing VM. This detects unintended DNS access. It does not prove that TCP 443 is limited to one IP: k8s-gateway has no HTTPS listener, so a failed connection to its port 443 cannot distinguish a correct `/32` rule from an overbroad prefix rule. The exact destination readback remains required.

Owner acceptance recorded October 27, 2026: Matt confirmed **“Yes, login and dashboards work”** on the iPhone over NetBird while away from home. This completes acceptance of the Grafana admin HTTPS extension. It is owner-reported application evidence, separate from the October 19 VM probes. A packet capture is an optional diagnostic tool for later troubleshooting, not a condition on this accepted result.

NetBird setup and comprehensive testing, including home/away transitions, sleep/wake, failure modes and any broader resource access, remain in #625. The accepted Grafana check does not complete that work.

## Roll back the admin extension

Disable the new NetBird admin HTTPS policy first, then disable the new UniFi forward rule and its paired return rule using the IDs recorded during application. Do not select return rules by name alone. Remove the new resource and policy, and the dedicated resource group if unused, after confirming that the original household HTTPS and DNS resources remain present and functional. Leave the original services policy, split DNS, peer policy and posture check intact. Verify that remote admin HTTPS is withdrawn while Jellyfin and native peer access continue to work, and update the same applied-state references with the withdrawal.

VM 103 is the only routed remote path. Retain it and the household HTTPS, DNS and peer access when rolling back this admin-only extension.
