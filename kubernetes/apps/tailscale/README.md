# Tailscale operator

Remote access into the home LAN via the [Tailscale Kubernetes operator]
running a **subnet router** that advertises the LAN/VLAN/LB ranges into a
Tailscale tailnet. This complements the Cloudflare tunnel (which only publishes
the public `*.${SECRET_DOMAIN}` web surface) by reaching the LAN-only admin /
services gateways (`*.home.kelch.io`) and physical LAN devices when off-network.

Design, route rationale, and caveats: `docs/plans/20260622-tailscale-operator.md`.

[Tailscale Kubernetes operator]: https://tailscale.com/kb/1236/kubernetes-operator

## What's GitOps-managed here

- `operator/` — `HelmRepository` + `HelmRelease` (`tailscale-operator`, pinned) and
  the `operator-oauth` SOPS secret. `wait: true` so the Connector's `dependsOn` gates
  on the operator's CRDs being installed.
- `connector/` — the `Connector` CR (`lan-subnet-router`) advertising the routes below.
  Split into its own Flux Kustomization (`dependsOn: tailscale-operator`) for CRD
  ordering, the same way `cilium-bgp` depends on `cilium`.

## Advertised routes

| CIDR | What it reaches |
|------|-----------------|
| `10.32.1.0/24`   | VLAN 1 — UniFi controller / gateway admin |
| `10.32.10.0/23`  | VLAN 10 — Main: trusted clients / admin devices (widened past the /24 so a client's connected route wins; see [Client policy](#client-policy-accept-routes)) |
| `10.32.20.0/24`  | VLAN 20 — Lab Infra: NAS, switches, APs |
| `10.32.30.0/24`  | VLAN 30 — Lab Prod: `talosctl`, kube-API VIP, nodes |
| `10.32.130.0/24` | admin-prod LB — Longhorn/Grafana/o11y + k8s-gateway DNS |
| `10.32.140.0/24` | services-prod LB — household apps + `*.home.kelch.io` |

Change the set by editing `connector/app/connector.yaml` **and** the
`autoApprovers.routes` ACL below — they must stay in sync or new routes wait on a
manual approval click.

## Tailnet-side config (manual, not GitOps)

Tailscale's control plane is not GitOps-managed; these are configured in the admin
console / tailnet policy file and are the source of truth. **The deploy is inert
until these exist** — the operator can't authenticate and routes won't auto-approve.

### 1. OAuth client

[Admin console → Settings → OAuth clients] → Generate. Scopes:

- **Devices → Core**: write
- **Keys → Auth Keys**: write

Tag the client `tag:k8s-operator`. Put the resulting `client_id` / `client_secret`
into the SOPS secret:

```sh
sops --decrypt --in-place kubernetes/apps/tailscale/operator/app/secret.sops.yaml
# fill client_id / client_secret
sops --encrypt --in-place kubernetes/apps/tailscale/operator/app/secret.sops.yaml
```

[Admin console → Settings → OAuth clients]: https://login.tailscale.com/admin/settings/oauth

### 2. ACL — tag owners + route auto-approval

In the tailnet policy file (Access controls):

```jsonc
{
  "tagOwners": {
    "tag:k8s-operator": [],
    "tag:k8s": ["tag:k8s-operator"],
  },
  "autoApprovers": {
    "routes": {
      "10.32.1.0/24":   ["tag:k8s"],
      "10.32.10.0/23":  ["tag:k8s"],
      "10.32.20.0/24":  ["tag:k8s"],
      "10.32.30.0/24":  ["tag:k8s"],
      "10.32.130.0/24": ["tag:k8s"],
      "10.32.140.0/24": ["tag:k8s"],
    },
  },
}
```

`autoApprovers` means the subnet router's routes are approved without a manual click.
If the tailnet ACL has been narrowed from the default allow-all, also add a grant
permitting client devices to reach these CIDRs.

### 3. Split-DNS for `home.kelch.io`

`*.home.kelch.io` is an **internal-only** zone — it never resolves on public DNS, so
accepting subnet routes gives layer-3 reachability but not name resolution. Point the
tailnet at the home resolver for that domain:

[Admin console → DNS] → Nameservers → Add nameserver → Custom:

- Nameserver: **`10.32.30.1`** (UniFi gateway — answers static infra records and
  conditionally forwards app records to k8s-gateway `10.32.130.2`)
- Restrict to domain: **`home.kelch.io`**

`10.32.30.0/24` is in the advertised set, so the nameserver itself is reachable over
the tailnet.

[Admin console → DNS]: https://login.tailscale.com/admin/dns

### 4. Enroll client devices

Install Tailscale on the device and sign in to the tailnet. Whether to accept the advertised routes depends on the node class — see [Client policy](#client-policy-accept-routes). MagicDNS picks up the split-DNS entry automatically on every enrolled device.

## Client policy (accept-routes)

Subnet routes exist for devices that are **not** on the advertised prefixes. A device sitting on VLAN 10 already reaches every VLAN through `10.32.10.1` (the gateway inter-VLAN routes), so accepting routes there buys nothing — and accepting a route identical to the connected prefix installs a duplicate `10.32.10.0/24` on the Tailscale interface that breaks Magicsock direct paths between same-LAN peers (direct ↔ DERP flapping, dead long-lived TCP). This is a [documented Tailscale failure mode]; the documented fix is advertising the prefix wider than the LAN (`/23` above) so the OS's connected `/24` wins by longest-prefix match. Tailscale documents that behavior for macOS and Windows; iOS is asserted nowhere and is verified empirically here (see Validation). A Linux client would **not** be covered — Linux installs Tailscale routes via policy routing that outranks the main table, so an accept-routes Linux device on VLAN 10 needs `ip rule add to 10.32.10.0/24 priority 2500 lookup main` (reapplied at boot) or accept-routes off. No such client exists today.

| Node class | Accept routes ("Use Tailscale subnets") | How the LAN is reached |
|------------|------------------------------------------|------------------------|
| Always-home (hyperion, talaria) | **Off** (`tailscale set --accept-routes=false`) | Connected LAN + inter-VLAN via `10.32.10.1`; tailnet peers via `100.x` / MagicDNS |
| Travels (iphone, laptops) | **On**, left on at home too | Away: everything via the subnet router. Home: VLAN 10 direct over Wi-Fi (connected `/24` beats the advertised `/23`); other VLANs hairpin through the subnet router |
| Subnet router (`lan-subnet-router`) | Never accepts its own routes | n/a |

Limitations, stated plainly:

- Accept-routes is **all prefixes or none** per node; Tailscale has no per-prefix accept and no SSID-conditional subnet toggle. The `/23` widening is what makes leaving it on safe for devices that come home.
- iOS **VPN On Demand** ("Except On" the home SSID) would disconnect the tunnel entirely at home — that also kills `100.x` reachability (T3 pairing, MagicDNS), so it is not used here. Travel devices keep the tunnel up everywhere.
- A travel device at home still reaches non-connected VLANs (`10.32.30.x`, the `130`/`140` LB pools) via the subnet-router hairpin rather than the physical gateway. Only a full at-home disconnect would change that; accepted as the lesser cost.
- On a foreign network that itself uses `10.32.10.0/24` (or `10.32.11.0/24`), the connected prefix outranks the advertised `/23` — the same LPM that protects the home case — so home VLAN 10 is unreachable until leaving that network. Inherent to any RFC 1918 collision, and marginally wider now that the `/23` spans two `/24`s.

[documented Tailscale failure mode]: https://tailscale.com/docs/reference/troubleshooting/network-configuration/lan-traffic-overlapping-subnets

## Validation

After `client_id`/`client_secret` are filled and Flux has reconciled:

```sh
kubectl -n tailscale get pods                       # operator-* + subnet-router pod Ready
kubectl get connector lan-subnet-router -o wide     # SubnetRoutes lists the 6 CIDRs; ConnectorReady
```

Admin console → Machines: `tailscale-operator` and `lan-subnet-router` present, routes **approved** (auto) — in particular `10.32.10.0/23` must show approved, not pending (the `autoApprovers` entry must read `/23`; an entry only approves advertisements at or below its own width). On an accept-routes device sitting on VLAN 10: the route table shows the connected `10.32.10.0/24` on the physical interface and only the `/23` on the Tailscale interface, and `tailscale status` holds a `direct <LAN-IP>:41641` path to a same-LAN peer. From a remote enrolled device with `--accept-routes`:

```sh
ping 10.32.20.5                                      # a LAN host (NAS)
curl -sk https://<something>.home.kelch.io           # gateway VIP via split-DNS
kubectl --server https://10.32.30.8:6443 get nodes   # kube-API over the tailnet
```

## Caveats

- **Chicken-and-egg.** This ingress runs inside the cluster it exposes — if the cluster
  (or the node hosting the subnet-router pod) is down, remote access is down too and
  recovery needs on-LAN access. Break-glass WireGuard on the UniFi gateway is the
  independent-ingress mitigation and is intentionally out of scope.
- **UniFi firewall still applies.** Forwarded traffic egresses a Talos node SNAT'd to
  its VLAN 30 IP; advertising a route in Tailscale does not bypass UniFi LAN-IN rules.
  See the plan doc for the default-deny follow-up.
