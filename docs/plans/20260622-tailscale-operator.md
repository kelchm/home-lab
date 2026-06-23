# Tailscale operator — remote LAN access plan

## Context

Off-LAN, the only thing reachable today is the public `*.${SECRET_DOMAIN}` web
surface published through the Cloudflare tunnel (`kubernetes/apps/network/cloudflare-tunnel/`).
Everything that rides the **admin** and **services** Traefik gateways — Longhorn,
Grafana, OpenObserve, the household apps, the whole `*.home.kelch.io` zone — plus
raw LAN devices (UniFi, NAS, switches, the Talos nodes / `talosctl` / kube-API) is
LAN-only by design. There is no way to reach any of it when away from home.

This plan closes that gap with the **Tailscale Kubernetes operator** running a
**subnet router** (`Connector` CR) that advertises the home LAN/VLAN/LB ranges into
a Tailscale tailnet. A device enrolled in the tailnet, with the advertised routes
accepted, reaches LAN IPs directly; a tailnet split-DNS entry makes `*.home.kelch.io`
resolve so the gateways work by hostname. Managed control plane (Tailscale SaaS),
WireGuard data plane. GitOps-managed exactly like every other app here.

Decisions already fixed (not relitigated): Tailscale operator (not Headscale/NetBird/
WARP); break-glass WireGuard on the UniFi gateway is **out of scope**; the cluster
path is the only ingress this plan delivers.

## Inputs confirmed with the user (2026-06-22)

- **Advertised routes** — the granular set plus the UniFi management VLAN:
  | CIDR | What it reaches |
  |------|-----------------|
  | `10.32.1.0/24`   | VLAN 1 — UniFi controller/gateway admin (`gateway.home.kelch.io` = `10.32.1.1`) |
  | `10.32.10.0/24`  | VLAN 10 — Main: trusted clients / admin devices |
  | `10.32.20.0/24`  | VLAN 20 — Lab Infra: NAS admin (`10.32.20.5`), switches, APs |
  | `10.32.30.0/24`  | VLAN 30 — Lab Prod: `talosctl` (`:50000`), kube-API VIP (`10.32.30.8:6443`), nodes |
  | `10.32.130.0/24` | admin-prod LB pool — Longhorn/Grafana/o11y via Traefik admin + k8s-gateway DNS (`.2`) |
  | `10.32.140.0/24` | services-prod LB pool — household apps + wildcard `*.home.kelch.io` → `10.32.140.1` |

  Excluded on purpose: VLAN 99 Guest, VLAN 5 Cameras, VLAN 25 Storage, VLAN 90 IoT,
  and all future/unallocated ranges (VLAN 31, sandbox pools `10.32.131/141`, shared-prod
  `10.32.150`). Every CIDR is drawn from `docs/architecture.md`; none invented.
- **Scope** — subnet router only. No direct per-Service Tailscale exposure and no
  kube-apiserver proxy this round (both are easy follow-ups: a `tailscale`-class
  Ingress / `tailscale.com/expose` annotation, or `apiServerProxyConfig.mode`).

## Approach

### Namespace

New dedicated `tailscale` namespace (`kubernetes/apps/tailscale/`). The root
`cluster-apps` Flux Kustomization (`kubernetes/flux/cluster/ks.yaml`) walks
`./kubernetes/apps` and auto-discovers the directory — no aggregator edit needed.
The namespace `kustomization.yaml` pulls `components: [../../components/sops]` and
lists each app's `ks.yaml`, mirroring `kubernetes/apps/network/`.

### Chart source + version

`tailscale-operator` is published only to Tailscale's classic Helm index, so we use
the `HelmRepository` pattern (same as Traefik), not OCI:

- `HelmRepository` → `https://pkgs.tailscale.com/helmcharts`
- `HelmRelease` chart `tailscale-operator`, pinned **`1.98.4`** (appVersion `v1.98.4`,
  current latest as of 2026-06-22).

The chart templates its CRDs (`installCRDs: true`, applied as normal resources rather
than via Helm's `crds/` mechanism so they upgrade cleanly). The global HelmRelease
defaults (`crds: CreateReplace`, retry/remediation) injected by the root KS patch
apply automatically and are not repeated per release.

### CRD-before-CR ordering (two Flux Kustomizations)

The `Connector` CR needs the `connectors.tailscale.com` CRD that the operator's
HelmRelease installs. This is the same shape as cilium ⇄ cilium-bgp in this repo, so
it uses the same idiom:

- `operator/ks.yaml` — installs the chart; **`wait: true`** so the HelmRelease must be
  rolled out (CRDs present) before dependents proceed.
- `connector/ks.yaml` — `dependsOn: [tailscale-operator]`, `wait: false`; applies the
  `Connector`.

Neither needs `postBuild.substituteFrom` — these manifests reference no
`${...}` variables (cilium-bgp omits it for the same reason). `decryption.provider:
sops` is injected into both by the root KS patch, so the OAuth secret decrypts.

### OAuth secret (bring-your-own)

The chart only creates `operator-oauth` when `oauth.clientId` is set; left empty, the
operator Deployment mounts a pre-existing Secret named `operator-oauth` (keys
`client_id` / `client_secret`) at `/oauth`. So we ship our own
`operator/app/secret.sops.yaml`:

- `kind: Secret`, `metadata.name: operator-oauth`, `stringData.client_id` /
  `stringData.client_secret`.
- Committed **SOPS-encrypted** with **placeholder** values and a header comment naming
  the tailnet steps + the `sops --decrypt`/edit/`--encrypt` round-trip (the
  `kubernetes/apps/media/arr-api-keys.sops.yaml` convention). The user fills real
  values before the operator can authenticate.

### Subnet router (`Connector`)

`connector/app/connector.yaml` — `tailscale.com/v1alpha1` `Connector` (cluster-scoped):

- `spec.subnetRouter.advertiseRoutes`: the six CIDRs above.
- `spec.tags: ["tag:k8s"]` (a Connector ignores `proxyConfig.defaultTags`, so the tag
  is set on the CR; the operator must own `tag:k8s`).
- `spec.hostname: lan-subnet-router`.

The operator renders this into a StatefulSet (the subnet-router proxy pod) in the
`tailscale` namespace, running `tailscaled` with `NET_ADMIN` + a TUN device — that pod's
securityContext is managed by the operator, not by our HelmRelease values.

### Operator hardening (what the chart allows)

`operatorConfig`: resource requests/limits, `capabilities: drop: [ALL]`,
`allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`, and the default
`defaultTags: [tag:k8s-operator]` stated explicitly. The operator runs `tailscaled`
in **userspace/netstack** mode (state in a Kube Secret, no TUN), so dropping all caps
is safe.

`runAsNonRoot` / `readOnlyRootFilesystem` are deliberately **not** forced: the embedded
`tailscaled` needs a writable root FS for its socket/runtime state and the chart exposes
no `extraVolumes` hook to back a read-only root with a tmpfs; the container user is set
upstream. Caps-drop + no-priv-esc + seccomp are the high-value controls and are applied.
Revisit non-root via a runtime check (operator pod Ready) or a `ProxyClass` once the
image's user is confirmed against the cluster.

## Tailnet-side config (manual, not GitOps)

Documented in `kubernetes/apps/tailscale/README.md`. Inert without these — the operator
cannot authenticate and routes will not auto-approve:

1. **OAuth client** (admin console → Settings → OAuth clients): scopes **Devices Core
   = write** and **Auth Keys = write**, tagged `tag:k8s-operator`. Put `client_id` /
   `client_secret` into the SOPS secret.
2. **ACL `tagOwners`**: `tag:k8s-operator: []` and `tag:k8s: ["tag:k8s-operator"]`.
3. **ACL `autoApprovers.routes`**: each advertised CIDR → `["tag:k8s"]`, so the subnet
   routes are approved automatically instead of waiting on a manual click in the admin
   console.
4. **Split-DNS** (admin console → DNS → Nameservers → custom, restricted to
   `home.kelch.io`): nameserver **`10.32.30.1`** (UniFi gateway — answers static infra
   records directly and conditionally forwards app records to k8s-gateway `10.32.130.2`).
   `10.32.30.0/24` is in the advertised set, so the nameserver is itself reachable. This
   is what makes `https://<x>.home.kelch.io` resolve off-LAN; the zone is internal-only
   and never public, so accepting routes alone is not enough.
5. **Client devices**: enroll, then accept subnet routes (`tailscale up
   --accept-routes`, or the GUI toggle). If the tailnet ACL is locked down from the
   default allow-all, add a grant permitting client devices to reach the advertised CIDRs.

## Validation

Pre-merge (no cluster needed):

- `kustomize build` of `kubernetes/apps/tailscale/operator/app`,
  `.../connector/app`, and the namespace overlay `kubernetes/apps/tailscale` all render.
- `kubeconform` (repo pins `0.8.0`) over the rendered output, skipping the CRD-backed
  `Connector` kind (no schema until the operator installs it).
- `sops --decrypt` round-trips `operator/app/secret.sops.yaml` (encrypted, placeholders).

Post-deploy runtime checks:

- `kubectl -n tailscale get pods` — `operator-*` Ready; subnet-router StatefulSet pod Ready.
- `kubectl get connector lan-subnet-router -o wide` — `SubnetRoutes` lists the six CIDRs;
  `ConnectorReady` true.
- Admin console → Machines — `tailscale-operator` and `lan-subnet-router` present;
  routes show **approved** (auto, via `autoApprovers`).
- From a remote enrolled device with `--accept-routes`: reach a LAN host IP (e.g.
  `ping 10.32.20.5`), the kube-API (`talosctl`/`kubectl` against `10.32.30.8`), and load
  `https://<something>.home.kelch.io` by hostname (exercises split-DNS + a gateway VIP).

## Caveats

- **Chicken-and-egg.** This remote-access path lives inside the very cluster it exposes.
  If the cluster control plane, Cilium, or the node hosting the subnet-router pod is down,
  Tailscale ingress is down too — recovery then requires on-LAN access. Break-glass
  WireGuard on the UniFi gateway (an independent ingress) is the mitigation and is
  **out of scope** per the fixed decision. Initial setup (OAuth client, ACLs, approving
  the operator device) is done from on-LAN on first deploy, which is fine.
- **Forwarded-traffic source IP + UniFi firewall.** Traffic the subnet router forwards to
  the LAN egresses a Talos node and is SNAT'd by Cilium to the node's VLAN 30 IP
  (`10.32.30.11/.12/.13`); the admin-prod / services-prod VIPs are cluster-local and stay
  in-cluster. UniFi's current default-allow inter-VLAN posture permits VLAN 30 → the
  advertised VLANs, and the existing IoT/Guest → `bgp-lb-restricted` drops don't affect
  VLAN 30. If a default-deny posture is later adopted (anticipated in
  `network/unifi/README.md`), add explicit allows from the node IPs / VLAN 30 to the
  advertised destinations. Advertising a route in Tailscale does not bypass UniFi LAN-IN
  rules.
