# NetBird remote access

NetBird is a location-aware pilot tracked in [#625](https://github.com/kelchm/home-lab/issues/625). Its routing peer is VM 103, `netbird-pilot-router`, at `10.32.19.103` on the isolated Remote Admin VLAN. Tailscale remains the fallback for broader lab access. NetBird Cloud and UniFi policy are applied manually; merging this document changes neither.

## Verified baseline

On October 19, 2026, the iPhone was connected to the routing peer through a NetBird relay. The routing peer advertised only `10.32.140.1/32` (household services) and `10.32.30.1/32` (DNS). From that VM, `https://jellyfin.home.kelch.io/health` returned HTTP 200, while `https://grafana.home.kelch.io/api/health` timed out. From Main, Grafana returned HTTP 200 with `database: ok`, and its pod was Running with all three containers Ready.

Grafana resolves to the admin gateway, `10.32.130.1`, which the original pilot excludes. A connected VPN and working split DNS therefore do not establish access to Grafana. The existing [UniFi policy](../../network/unifi/README.md#remote-admin-applied-state) permits the pilot router to reach only `10.32.140.1` on TCP 443.

## Extend access to admin web apps

The admin HTTPS extension is authorized but has not been applied or verified. It adds access to the web apps sharing `gateway-admin`, rather than to Grafana alone. Application authentication continues to apply. Direct management endpoints, other ports and other destination IPs are outside this extension.

1. Authenticate to NetBird Cloud and UniFi with an administrator account. Read back the existing network, resource, policy, posture-check and firewall objects, and retain their configuration privately before changing them.
2. In the existing `pilot-home` NetBird network, add the enabled resource `pilot-admin-https` at `10.32.130.1/32`. Retain the existing routing-peer group and masquerading. Do not change the DNS or household-service resources.
3. Add a policy from `pilot-clients` to that resource for TCP 443 only. Use the same `pilot-exclude-home-main` posture check as the household-service policy: block clients on `10.32.10.0/24`, so home traffic uses the LAN. Keep the unrestricted peer policy separate and the account's default All-to-All policy disabled. Do not attach a broader resource-group rule to the new resource.
4. In UniFi, add `Allow NetBird Pilot Router to Admin HTTPS`: source zone `Remote Admin`, source IP `10.32.19.103`; destination zone `Internal`, destination IP `10.32.130.1`; IPv4 TCP destination port 443; allow. Match the placement and established/related return handling of `Allow NetBird Pilot Router to Services VIP`. Preserve the existing policy and all other zone restrictions.
5. Read both control planes back and verify that the resource and policies match those exact addresses, clients, posture check and port. The admin address must not become an unrestricted LAN route.

NetBird's [Networks API](https://docs.netbird.io/api/resources/networks) and [Policies API](https://docs.netbird.io/api/resources/policies) describe the corresponding objects. Prefer cloning the shape of the live household HTTPS policy with only the name and destination resource changed, after checking its scope. UniFi's routed-prefix classification for this source zone is documented in the applied-state reference; do not widen the destination zone or prefix to work around a failed probe.

## Verify

Reach the routing VM from Main through the existing Tailscale router:

```sh
ssh -J kelchm@10.32.19.101 kelchm@10.32.19.103
```

From the VM, Grafana's `/api/health` must return HTTP 200 with a healthy database, and Jellyfin's `/health` must still return HTTP 200. Use HTTPS with certificate validation. Connection attempts to `10.32.130.1:80`, `10.32.140.1:80`, and `10.32.20.21:8006` must remain blocked. These VM tests verify the UniFi path; they do not prove that a remote client received the NetBird resource or policy.

With the iPhone away from Main and connected through NetBird, open `https://grafana.home.kelch.io` and complete the normal login through `auth.home.kelch.io` (the existing household-services gateway). Confirm that the application loads and can query a dashboard. During the test, a bounded capture on the routing VM provides path evidence:

```sh
sudo timeout 45 tcpdump -ni wt0 -c 100 \
  'host 100.64.41.255 and (host 10.32.130.1 or host 10.32.140.1 or host 10.32.30.1)'
```

The iPhone's NetBird address above is the October 19 value; check it before reuse. An admin HTTPS connection in this capture proves the remote path. Do not treat a successful Main-network request as remote acceptance. Confirm that Hyperion on Main still has no NetBird resource routes, that Grafana uses the physical LAN route, and that native peer access remains connected. The remaining transition, sleep/wake and failure-mode qualification belongs to #625.

## Roll back the admin extension

Disable the new NetBird admin HTTPS policy first, then disable the matching UniFi admin HTTPS policy and its generated return policy. Remove the new resource and policy after confirming that the original household HTTPS and DNS resources remain present and functional. Leave the original services policy, split DNS, peer policy, posture check and Tailscale configuration intact. Verify that remote admin HTTPS is withdrawn while Jellyfin and native peer access continue to work.

For immediate broader lab access, disconnect NetBird on the iPhone and connect Tailscale; only one iOS VPN can run at a time. Switching away from NetBird also interrupts its native peer connections.
