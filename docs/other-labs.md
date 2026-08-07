# Other home labs worth studying

This is a curated reading list, not a leaderboard or an endorsement of every choice in these repositories. It records the design question each lab is useful for answering so the list remains valuable after app counts, commit activity, and hardware inventories go stale.

**Last reviewed:** 2026-08-06

## Quick index

| Question | Start with |
|---|---|
| How can Talos and GitOps patterns be structured at greater scale? | [onedr0p/home-ops](https://github.com/onedr0p/home-ops), [nicolerenee/infra](https://github.com/nicolerenee/infra) |
| How can production, utility, test, or edge clusters be separated? | [joryirving/home-ops](https://github.com/joryirving/home-ops), [tyriis/home-ops](https://github.com/tyriis/home-ops) |
| How can Kubernetes and Proxmox live in one repository? | [mitchross/talos-argocd-proxmox](https://github.com/mitchross/talos-argocd-proxmox), [Mafyuh/iac](https://github.com/Mafyuh/iac) |
| How can Kubernetes coexist with NAS, VPS, network, and Docker automation? | [eleboucher/homelab](https://github.com/eleboucher/homelab), [deedee-ops/home-ops](https://github.com/deedee-ops/home-ops), [ishioni/homelab-ops](https://github.com/ishioni/homelab-ops) |
| What do deeper storage, backup, and network designs look like? | [dapperdivers/dapper-cluster](https://github.com/dapperdivers/dapper-cluster), [JJGadgets/Biohazard](https://github.com/JJGadgets/Biohazard) |
| What do mature local AI, agent, and MCP platforms look like? | [rwlove/home-ops](https://github.com/rwlove/home-ops), [joryirving/home-ops](https://github.com/joryirving/home-ops), [nicolerenee/infra](https://github.com/nicolerenee/infra), [vrozaksen/home-ops](https://github.com/vrozaksen/home-ops) |
| What does reproducible bare-metal bootstrap look like? | [khuedoan/homelab](https://github.com/khuedoan/homelab) |

## Kubernetes plus other platforms

"Same repository" can describe two materially different arrangements. Some repositories configure or provision the other platform; others only document a platform on which Kubernetes happens to run. Preserve that distinction when borrowing a pattern.

- **[mitchross/talos-argocd-proxmox](https://github.com/mitchross/talos-argocd-proxmox)** is the clearest end-to-end Proxmox example. It keeps Talos/Omni cluster templates, Proxmox provider configuration and helper scripts, disaster recovery material, and Argo CD-managed workloads together. Study its bootstrap boundary: the machinery that creates a cluster cannot depend on that cluster already being healthy.
- **[Mafyuh/iac](https://github.com/Mafyuh/iac)** takes the broadest whole-estate approach. Kubernetes/Talos/Flux sits beside OpenTofu/Terraform for Proxmox, UniFi, Cloudflare, and other providers, plus Packer, NixOS, Ansible, and Docker. It is a useful counterexample to this repository's intentionally narrower boundary around UniFi and NAS administration.
- **[eleboucher/homelab](https://github.com/eleboucher/homelab)** combines Talos/Kubernetes with Docker workloads on a VPS and OpenTofu-managed MikroTik networking. Look here when deciding whether routing, DNS, DHCP, firewall, and WireGuard intent belongs beside cluster GitOps.
- **[deedee-ops/home-ops](https://github.com/deedee-ops/home-ops)** combines a Talos cluster with NAS/edge and VPS workloads managed through Docker Compose, Ansible, and OpenTofu. Its split is relevant to workloads that should remain available while Kubernetes is down.
- **[ishioni/homelab-ops](https://github.com/ishioni/homelab-ops)** keeps Kubernetes/Talos, Docker workloads on TrueNAS, and Terraform-managed external services in one operations repository. It is a compact reference for putting cluster and NAS-side workloads under one review workflow without pretending they share one control plane.
- **[joryirving/home-ops](https://github.com/joryirving/home-ops)** operates multiple Talos clusters alongside an Ansible/Docker-managed VPS edge. It is a useful model for separating public ingress or recovery services from the main cluster.
- **[Apocrathia/homelab](https://github.com/Apocrathia/homelab)** broadens the repository beyond Kubernetes with Ansible and Fleet policy for endpoint management. Proxmox is part of the hosting context, but the repository is better studied for policy, security, and heterogeneous device management than as a Proxmox-provisioning example.
- **[dapperdivers/dapper-cluster](https://github.com/dapperdivers/dapper-cluster)** is an important boundary case: Talos VMs consume Ceph supplied by Proxmox, but the repository does not appear to own the Proxmox/Ceph control plane. Study it for integration with external infrastructure, not for unified provisioning.

## Cluster topology, storage, and recovery

- **[onedr0p/home-ops](https://github.com/onedr0p/home-ops)** is the closest architectural relative to this lab and a good source for reusable Flux components, Talos conventions, Rook/Ceph, backup tooling, service mesh, and self-hosted automation patterns. Treat it as upstream inspiration rather than a template to mirror wholesale.
- **[nicolerenee/infra](https://github.com/nicolerenee/infra)** is a strong multi-site reference: shared application patterns are applied across home and colocated clusters, alongside Ceph, GPU inference, and unusually deep network observability.
- **[tyriis/home-ops](https://github.com/tyriis/home-ops)** demonstrates a main-versus-utility cluster split and a wide platform surface, including identity/secrets, development services, home automation, AI, and security tooling.
- **[joryirving/home-ops](https://github.com/joryirving/home-ops)** is worth studying for explicit main, utility, and test cluster roles, plus storage recovery and out-of-cluster edge services.
- **[dapperdivers/dapper-cluster](https://github.com/dapperdivers/dapper-cluster)** documents an external Ceph design, dedicated high-speed storage networking, multiple physical locations, GPUs, and Multus/IoT networking particularly well.
- **[JJGadgets/Biohazard](https://github.com/JJGadgets/Biohazard)** is a useful focused reference for KubeVirt, Rook/Ceph, policy enforcement, and layered backup experiments.
- **[vrozaksen/home-ops](https://github.com/vrozaksen/home-ops)** offers another compact Talos/Ceph design with multi-gigabit networking, GPU workloads, and separately managed network infrastructure.

## AI, agents, and MCP infrastructure

These repositories are ahead in different dimensions rather than on one simple scale. Compare how they divide model serving, tool registration, gateway policy, agent execution, and observability with this lab's MetaMCP-centered design.

- **[rwlove/home-ops](https://github.com/rwlove/home-ops)** has the most complete MCP fleet operating model in this group. It combines declarative MCP server registrations and a shared gateway with per-tool and service-mesh metrics, recording rules, alerts, dashboards, and explicit design work around tool authorization and context-budget control. It also runs vLLM and in-cluster agent workloads. Study `kubernetes/apps/mcp-system`, `docs/src/mcp_observability.md`, `docs/src/mcp_tool_authz_design.md`, and `docs/src/ai_architecture.md`; this is the strongest comparison for operating and governing a large MCP fleet.
- **[joryirving/home-ops](https://github.com/joryirving/home-ops)** uses the ToolHive operator to represent servers as `MCPServer` resources, group them with `MCPGroup`, and enable Prometheus telemetry through `MCPTelemetryConfig`. It also bridges MCP endpoints into Open WebUI-compatible tool servers. Study `kubernetes/apps/base/llm/toolhive` and `docs/src/notes/tool-servers.md`; this is the clearest alternative to MetaMCP's partially UI-managed registry and onboarding flow.
- **[vrozaksen/home-ops](https://github.com/vrozaksen/home-ops)** is another useful ToolHive implementation, combining managed and remote MCP servers with an `MCPGroup` and an explicit OIDC configuration. Study `kubernetes/apps/ai/mcp` and `kubernetes/apps/ai/toolhive`; it is particularly relevant when evaluating whether server lifecycle, aggregation, and client identity should all become Kubernetes resources.
- **[nicolerenee/infra](https://github.com/nicolerenee/infra)** is the strongest inference reference rather than the broadest MCP fleet. It runs single- and multi-node vLLM workloads on DGX Spark nodes, using `LeaderWorkerSet`, GPU resource claims, RDMA networking, model storage, health probes, and metrics. Study `kubernetes/apps/inference` and `docs/compute/dgx-spark.md`; this is the place to look if local model serving becomes part of this lab rather than remaining a client-side concern.

## Bootstrap and documentation

- **[khuedoan/homelab](https://github.com/khuedoan/homelab)** approaches the lab as a reproducible bare-metal framework, with PXE/bootstrap automation and a strong emphasis on rebuilding from scratch. It is useful when evaluating how much of this lab's pre-Flux bootstrap should become declarative.
- **[thaynes43/haynes-ops](https://github.com/thaynes43/haynes-ops)** is worth reading for its main/edge split and the documentation and agent runbooks that surround the manifests.
