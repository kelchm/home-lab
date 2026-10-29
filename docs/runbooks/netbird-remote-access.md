# NetBird remote access

NetBird is Matt's selected replacement for Tailscale. Its routing peer is VM 103, `netbird-pilot-router`, at `10.32.19.103` on the isolated Remote Admin VLAN. Setup and comprehensive testing remain unfinished in [#625](https://github.com/kelchm/home-lab/issues/625). Tailscale was retired on October 25, 2026; the remaining retirement work is tracked in [#1069](https://github.com/kelchm/home-lab/issues/1069). NetBird Cloud and UniFi policy are applied manually; merging this document changes neither. The routing peer's definition, rebuild and enrollment are in [`proxmox/guests/netbird-router`](../../proxmox/guests/netbird-router/README.md).

## Current configuration

The names below are current. Dropping `pilot` is approved in stages in #625: friendly labels first, with the VM hostname and snippet held until the router upgrade after the R1 baseline. The names below remain the verified current names until that first stage is applied.

### Verified on 2026-10-27

These were read from the routing peer through `qm guest exec`, from Hyperion, and from UniFi's read-only API:

- **Routing peer:** VM 103 runs NetBird 0.79.0, connected to NetBird Cloud management (`api.netbird.io`), and routes `10.32.130.1/32`, `10.32.140.1/32` and `10.32.30.1/32`. Forwarding from `wt0` drops by default. With no client passing the posture check, the forward chain held only its established/related accept.
- **UniFi:** `Allow NetBird Pilot Router to Services VIP` allows `10.32.19.103` → `10.32.140.1` and `Allow NetBird Pilot Router to Admin HTTPS` allows `10.32.19.103` → `10.32.130.1`. Both are TCP 443 with generated established/related returns. The [UniFi reference](../../network/unifi/README.md#remote-admin-applied-state) has the details.
- **Gateway addresses:** the Remote Admin zone's default Gateway allow lets VM 103 reach every UniFi gateway address, including the console on `10.32.30.1:443`. Only NetBird policy stops a client from reaching that port through the DNS resource.
- **Hyperion's DNS:** Hyperion's NetBird resolver forwards `home.kelch.io` to `10.32.30.1`. UniFi answers the same records at `10.32.10.1`, so name resolution at home does not depend on NetBird.

### NetBird Cloud, read back on 2026-10-28

Read with [`scripts/netbird-export.sh`](../../scripts/netbird-export.sh):

| Kind | Name | Definition |
|---|---|---|
| Account | — | Overlay `100.64.0.0/16`, with an IPv6 overlay for group `All`; peer login and inactivity expiration off; lazy connections on; client auto-update off; peer approval off |
| Group | `All` | All four peers |
| Group | `pilot-clients` | Peers `Hyperion` and `iPhone-kelchm` |
| Group | `pilot-routers` | Peer `netbird-pilot-router` |
| Group | `pilot-home-resources` | Resources `pilot-jellyfin-vip` and `pilot-home-dns`; no policy names this group |
| Group | `pilot-admin-resources` | Resource `pilot-admin-https`; no policy names this group |
| Posture check | `pilot-exclude-home-main` | Peer network range: deny `10.32.10.0/24` |
| Network | `pilot-home` | Routed by group `pilot-routers`, metric 9999, masquerade |
| Resource | `pilot-jellyfin-vip` | `10.32.140.1/32` (household services) |
| Resource | `pilot-home-dns` | `10.32.30.1/32` (split DNS) |
| Resource | `pilot-admin-https` | `10.32.130.1/32` (admin HTTPS) |
| Policy | `pilot-mesh` | `pilot-clients` ↔ `pilot-clients`, all protocols, no posture check |
| Policy | `pilot-jellyfin` | `pilot-clients` → resource `pilot-jellyfin-vip`, TCP 443, with the posture check |
| Policies | `pilot-home-dns-udp`, `pilot-home-dns-tcp` | `pilot-clients` → resource `pilot-home-dns`, UDP and TCP 53, with the posture check |
| Policy | `pilot-admin-https` | `pilot-clients` → resource `pilot-admin-https`, TCP 443, with the posture check |
| Policy | `Default` | All ↔ All, disabled |
| DNS | `pilot-home-kelch-io` | `home.kelch.io` → `10.32.30.1:53` over UDP for `pilot-clients`; not the primary resolver |
| Setup key | `netbird-pilot-router` | One-off, used once on 2026-09-27; expired |
| Service user | `netbird-export` | Auditor (read-only); holds the read-back token |
| Service user | `Automation` | Admin, from the original setup; its token expired on 2026-10-27 |

The peers are `netbird-pilot-router` (Debian 13, NetBird 0.79.0), `Hyperion`, `iPhone-kelchm` and `Persephone`. `Persephone` is Matt’s older personal MacBook Pro, running NetBird 0.80.0 and enrolled through a user login on 2026-10-13. The October 28 export shows it in `All` only, with login expiration enabled, so it currently has no enabled-policy access. This is the existing configuration, not the intended restriction: Matt wants his personal machines to have the same access. #625 tracks adding Persephone to `pilot-clients` and disabling its login expiration to match the iPhone and Hyperion, plus a clients auto-group on their shared NetBird user for future personal-device enrollments. These changes are unapplied; auto-group assignment is not retroactive. Preserve other memberships and verify the group association survives the stage-1 rename to `clients`, then perform one away spot-check on Persephone rather than repeat the full transition matrix.

Every enabled resource policy names its resource directly, and no policy names a resource group. NetBird therefore grants clients only TCP 443 to `10.32.140.1` and `10.32.130.1` and port 53 to `10.32.30.1`; nothing grants TCP 443 to the UniFi console on `10.32.30.1`. VM 103 itself can reach that port, so the routing peer's per-port filter is what blocks clients, and #625's I1 check verifies that from a client away from home. `pilot-home-resources` holds both the household-services and DNS resources, so a policy naming that group would open its ports on both addresses, the console included. Target resources directly.

## Read back NetBird Cloud

[`scripts/netbird-export.sh`](../../scripts/netbird-export.sh) exports the account, groups, posture checks, policies, DNS, peers, setup keys, service users, networks, resources and routers. It writes them to a new private temporary directory and prints its path. The script makes only `GET` requests and removes setup-key values. It reads the token from `NETBIRD_TOKEN_FILE` if set, otherwise from `op://Project - home-lab/netbird-export/credential`. That token belongs to the `netbird-export` service user, whose Auditor role can read every NetBird configuration and change nothing. It was created on 2026-10-28 and expires on 2027-10-28; replace it before then.

The export also contains peer addresses and IDs. Compare it against the table above, record the differences and the verified state in #625, and keep the raw files out of Git and issue comments. Run it before and after every NetBird Cloud change.

The script exits non-zero if any request fails. Its first full run, on 2026-10-28, succeeded for every endpoint and produced the table above. The Auditor token gets HTTP 403 from `users/current` and from other users' token lists, neither of which the script reads.

## Clients and upgrades

Read back on 2026-10-27; Hyperion and the iPhone again on 2026-10-28:

| Peer | Installed from | Version | Upgrade owner and effect |
|---|---|---|---|
| `netbird-pilot-router` | Debian package from `pkgs.netbird.io` `stable`, installed by vendor data | 0.79.0; 0.81.5 available | Operator, by hand through guest exec ([procedure](../../proxmox/guests/netbird-router/README.md#upgrades)). Away clients lose the resources until the daemon reconnects. |
| Hyperion | Homebrew `netbirdio/tap/netbird` (daemon and CLI) and cask `netbirdio/tap/netbird-ui`; LaunchDaemon `netbird` runs `/opt/homebrew/bin/netbird service run` | Running daemon 0.80.0; installed formula 0.81.5, installed 2026-10-28; UI 0.80.0 | Operator, with `brew upgrade`. The running daemon keeps its old version until `sudo netbird service restart` or a reboot. |
| iPhone | App Store | Management reports build `ci-cb7ca8ef` on iOS 27.0.1; iOS core 0.75.0 as of 2026-09-27 | App Store updates |

NetBird's client auto-update setting is off, so no client upgrades itself through NetBird.

Hyperion's daemon plist sets `RunAtLoad: false` and `KeepAlive: true`. On 2026-10-19 the daemon started 16 minutes after boot and 3 seconds before the UI. Whether it connects before anyone logs in is unverified; #625 tests it.

## Verified baseline

On October 19, 2026, the iPhone was connected to the routing peer through a NetBird relay. The routing peer advertised only `10.32.140.1/32` (household services) and `10.32.30.1/32` (DNS). From that VM, `https://jellyfin.home.kelch.io/health` returned HTTP 200, while `https://grafana.home.kelch.io/api/health` timed out. From Main, Grafana returned HTTP 200 with `database: ok`, and its pod was Running with all three containers Ready.

Grafana resolves to the admin gateway, `10.32.130.1`, which the original pilot excluded. A connected VPN and working split DNS therefore did not establish access to Grafana. The [UniFi policy reference](../../network/unifi/README.md#remote-admin-applied-state) records the current allowances.

## Extend access to admin web apps

The admin HTTPS extension was applied on October 19, 2026: NetBird resource `pilot-admin-https` at `10.32.130.1/32` is in its dedicated `pilot-admin-resources` group, with the direct-resource policy `pilot-admin-https` from `pilot-clients` for TCP 443 and the existing `pilot-exclude-home-main` posture check. The matching UniFi allowance and paired established/related return rule were created through an administrator browser session and read back with the exact single-IP source and destination. The original services policy was preserved. The October 19 VM checks passed, and Matt's successful remote iPhone acceptance is recorded below.

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

This management path was verified on October 27: guest exec returned the VM's hostname and NetBird status. Inspect the returned guest `exitcode` and `out-data`/`err-data`; a successful PVE request alone does not establish that the guest command succeeded. Main cannot reach VM 103's SSH directly, and the retired Tailscale routers are no longer a management hop. It is included in the nightly `daily-backups` job and has PVE `protection`. To restore, rebuild or re-enroll it, follow its [guest definition](../../proxmox/guests/netbird-router/README.md).

NetBird does not expose PVE management. Recovery requires a reachable admin session on Main; without one, an away operator must wait for local access to repair the routing peer or its host. Arrange that access before any disruptive test in #625. Native peer access to a host on Main is separate from the routed resources, but has not been qualified as a recovery path.

For later application diagnostics, run the probe inside VM 103 through the same management path:

```sh
sudo qm guest exec 103 -- curl --fail --show-error --max-time 10 https://grafana.home.kelch.io/api/health
```

## Router startup dependency

VM 103 still uses `vendor=library-pve:snippets/netbird-pilot-router.yaml`, although its root, EFI and cloud-init disks are local. On 2026-10-25 its host-boot start failed with `storage 'library-pve' is not online`; the owner-authorized `qm start 103` around 18:24:31 UTC was manual recovery, so it does not credit #625/P6. Retired VM 101 showed the same dependency on another node; retired VMs 101/102 are historical evidence, not failover peers.

The [node-local startup procedure](../../proxmox/guests/netbird-router/local-startup.md) prepares installation, verification and rollback for [#1096](https://github.com/kelchm/home-lab/issues/1096). **It is not installed.** Matt approved its design; the agreed order is this repair, a second peer (#1097), then upgrades. The repair keeps the vendor bytes, hostname, enrolled identity, installed version and policy intact. Enabling snippets on `local` changes cluster-wide storage capabilities; the file itself must be provisioned on every chosen migration/restore node. Keep the old NAS snippet for older archives.

Installation needs separate authorization and no immediate reboot. Autostart acceptance waits for the next separately authorized planned host boot and its unattended-start evidence. Do not reboot solely for acceptance or fault shared NFS/NAS to test this repair. #1096 stays open and #625/P6 stays uncredited until that acceptance is recorded.

## Verification and owner acceptance

For future changes, Grafana's `/api/health` from the VM must return HTTP 200 with a healthy database, and Jellyfin's `/health` must still return HTTP 200. Use HTTPS with certificate validation. Connection attempts to `10.32.130.1:80`, `10.32.140.1:80`, and `10.32.20.21:8006` must remain blocked. These VM tests verify the UniFi path; they do not prove that a remote client received the NetBird resource or policy.

The October 19 post-apply VM checks passed: Grafana returned HTTP 200 with `database: ok`, Jellyfin returned HTTP 200, and all three negative connections above timed out. The adjacent-address TCP DNS check below also timed out from the VM while answering from Main. The operator Mac on Main (Hyperion) still reported no NetBird resource routes.

As an adjacent-address check, first confirm that k8s-gateway at `10.32.130.2` answers a TCP DNS query from Main, then confirm that the same query times out from the routing VM. This detects unintended DNS access. It does not prove that TCP 443 is limited to one IP: k8s-gateway has no HTTPS listener, so a failed connection to its port 443 cannot distinguish a correct `/32` rule from an overbroad prefix rule. The exact destination readback remains required.

Owner acceptance recorded October 27, 2026: Matt confirmed **“Yes, login and dashboards work”** on the iPhone over NetBird while away from home. This completes acceptance of the Grafana admin HTTPS extension. It is owner-reported application evidence, separate from the October 19 VM probes. A packet capture is an optional diagnostic tool for later troubleshooting, not a condition on this accepted result.

NetBird setup and comprehensive testing, including home/away transitions, sleep/wake, failure modes and any broader resource access, remain in #625. The accepted Grafana check does not complete that work.

## Roll back the admin extension

Disable the new NetBird admin HTTPS policy first, then disable the new UniFi forward rule and its paired return rule using the IDs recorded during application. Do not select return rules by name alone. Remove the new resource and policy, and the dedicated resource group if unused, after confirming that the original household HTTPS and DNS resources remain present and functional. Leave the original services policy, split DNS, peer policy and posture check intact. Verify that remote admin HTTPS is withdrawn while Jellyfin and native peer access continue to work, and update the same applied-state references with the withdrawal.

VM 103 is the only routed remote path. Retain it and the household HTTPS, DNS and peer access when rolling back this admin-only extension.
