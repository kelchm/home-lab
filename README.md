# 🏠 home-lab

My home Kubernetes cluster, managed as code. This repo is the single source of
truth for everything running on it — bare-metal [Talos Linux](https://www.talos.dev/),
reconciled by [Flux](https://fluxcd.io/), with secrets encrypted in git via
[SOPS](https://github.com/getsops/sops).

It's public because there's no reason not to, but it's **100% specific to my
setup** — hostnames, VLANs, IP scheme, and hardware are all real. It started
life as a fork of [onedr0p/cluster-template](https://github.com/onedr0p/cluster-template)
and has diverged substantially since. Borrow freely; nothing here is meant to be
a drop-in template.

## 🧰 What it runs on

| Role | Hardware | Specs |
|---|---|---|
| Cluster nodes (3×) | HP EliteDesk 705 G4 mini | Ryzen 5 2400GE · 64 GB RAM · 1 TB NVMe (WD_BLACK SN770) · 1 GbE + 2.5 GbE |
| NAS / bulk storage | Synology DS1821+ | Ryzen V1500B · 6× 14 TB Exos X16 · 32 GB RAM · 2× SFP+ |

Three combined control-plane + worker nodes (HA etcd, `allowSchedulingOnControlPlanes`).
A second "sandbox" cluster is planned — see the [roadmap](docs/roadmap.md).

## 🧱 The stack

| Layer | Choice |
|---|---|
| OS | Talos Linux (immutable, API-driven) |
| GitOps | Flux — `main` is reconciled live via a GitHub webhook |
| CNI | Cilium with kube-proxy replacement + **BGP control plane** (peers UniFi; no MetalLB) |
| Ingress | Traefik (Gateway API) — split `admin` / `services` / `public` gateways |
| DNS | k8s-gateway (split-horizon for `home.kelch.io`) + Cloudflare (external) |
| Certificates | cert-manager, Let's Encrypt DNS-01 wildcard |
| Block storage | Longhorn on NVMe, replica traffic on a dedicated 2.5 GbE storage VLAN |
| Bulk storage | NFS from the Synology via `csi-driver-nfs` |
| Databases | CloudNative-PG |
| Secrets | SOPS + age |
| Identity / SSO | Kanidm (OIDC) via the kaniop operator |
| Dependency updates | Renovate |

## 📦 What I use it for

Workloads are organised one namespace per concern under [`kubernetes/apps/`](kubernetes/apps):

- **`media`** — the *arr suite (Sonarr, Radarr, Lidarr, Bazarr, Prowlarr),
  download clients (qBittorrent, SABnzbd), helpers (Unpackerr, Recyclarr,
  Flaresolverr) and Jellyfin, all fed off the Synology over NFS with per-app
  NAS identity and NFSv4 ACL isolation.
- **`identity`** — Kanidm as the OIDC provider / user directory, run by the
  kaniop operator.
- **`observability`** — a VictoriaMetrics + VictoriaLogs stack run against a
  Prometheus/Loki pipeline (kube-prometheus-stack, Loki, Alloy) plus
  OpenObserve and Grafana, the subject of an ongoing
  [bake-off](docs/observability-bakeoff.md).
- **`ai`** — MetaMCP as a single MCP gateway (web UI + OIDC via Kanidm, server
  registry in CloudNative-PG Postgres) fronting a set of backend MCP servers:
  Playwright (standard + stealth), Digi-Key, Grafana, Kubernetes, and Flux
  Operator. See the [rollout plan](docs/plans/20260620-metamcp-mcp-rollout.md).
- **`network`**, **`cert-manager`**, **`longhorn-system`**, **`cnpg-system`**,
  **`kube-system`** — the platform plumbing the above sits on.

## 🗺️ Repository layout

```text
home-lab/
├── talos/          # Talos machine config (talhelper input + per-node patches)
├── kubernetes/
│   ├── flux/       # Flux bootstrap / cluster entrypoint
│   ├── apps/       # workloads + cluster infra, one dir per namespace
│   └── components/ # shared Kustomize components (e.g. SOPS secrets)
├── bootstrap/      # Helmfile used to bring up Cilium/Flux before GitOps takes over
├── network/unifi/  # versioned UniFi-side artifacts (FRR/BGP, firewall intent)
├── scripts/        # helper scripts (bootstrap, Synology ACLs)
├── tools/          # one-off benchmarks and sync utilities
└── docs/           # architecture, runbooks, plans, decision records
```

## 📚 Documentation

Start with the [**docs index**](docs/README.md). Highlights:

- [Infrastructure architecture](docs/architecture.md) — network/VLAN design, the
  IP-addressing scheme, BGP, and storage. The reference for *why* things are
  laid out the way they are.
- [Roadmap](docs/roadmap.md) — the planned second cluster and deferred work.
- [Runbooks](docs/runbooks/) — operational procedures (Longhorn restore, Kanidm
  recovery, storage-network cutover, …).
- [Plans](docs/plans/) and decision records ([storage benchmarks](docs/storage-benchmarks.md),
  [observability bake-off](docs/observability-bakeoff.md)).

## 🛠️ Operations

Tooling is pinned with [mise](https://mise.jdx.dev/); common tasks run through
[`Taskfile.yaml`](Taskfile.yaml). A few I reach for often:

```sh
task reconcile            # force Flux to pull the latest git state
task talos:apply-node IP=10.32.30.11 MODE=auto   # push updated Talos config to a node
task talos:upgrade-node IP=10.32.30.11           # upgrade Talos on a node
task talos:upgrade-k8s                           # upgrade Kubernetes
```

Day-to-day Flux/Kubernetes debugging:

```sh
flux get ks -A && flux get hr -A      # are reconciliations healthy?
kubectl -n <namespace> get pods -o wide
kubectl -n <namespace> logs <pod> -f
```

## 🙏 Credits

Built on the shoulders of [onedr0p/cluster-template](https://github.com/onedr0p/cluster-template)
and the wider [Home Operations](https://discord.gg/home-operations) community.
Licensed under [MIT](LICENSE).
