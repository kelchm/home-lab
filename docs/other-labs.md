# Other home labs worth studying

This is a curated reading list, not a leaderboard or an endorsement of every choice in these repositories. It records the design question each lab is useful for answering so the list remains valuable after app counts, commit activity, and hardware inventories go stale.

**Last reviewed:** 2026-08-14 for Proxmox references; 2026-08-06 for the remaining entries

## Quick index

| Question | Start with |
|---|---|
| How can Talos and GitOps patterns be structured at greater scale? | [onedr0p/home-ops](https://github.com/onedr0p/home-ops), [nicolerenee/infra](https://github.com/nicolerenee/infra) |
| How can production, utility, test, or edge clusters be separated? | [joryirving/home-ops](https://github.com/joryirving/home-ops), [tyriis/home-ops](https://github.com/tyriis/home-ops) |
| What does management of the Proxmox hosts and cluster look like? | [jrtashjian/homelab-proxmox-cluster](https://github.com/jrtashjian/homelab-proxmox-cluster), [dbrennand/home-ops](https://github.com/dbrennand/home-ops) |
| How can Proxmox guest IaC and Kubernetes live in one repository? | [Apocrathia/homelab](https://github.com/Apocrathia/homelab), [Mafyuh/iac](https://github.com/Mafyuh/iac), [mitchross/talos-argocd-proxmox](https://github.com/mitchross/talos-argocd-proxmox) |
| How can Kubernetes coexist with NAS, VPS, network, and Docker automation? | [eleboucher/homelab](https://github.com/eleboucher/homelab), [deedee-ops/home-ops](https://github.com/deedee-ops/home-ops), [ishioni/homelab-ops](https://github.com/ishioni/homelab-ops) |
| What do deeper storage, backup, and network designs look like? | [dapperdivers/dapper-cluster](https://github.com/dapperdivers/dapper-cluster), [JJGadgets/Biohazard](https://github.com/JJGadgets/Biohazard) |
| What do mature local AI, agent, and MCP platforms look like? | [rwlove/home-ops](https://github.com/rwlove/home-ops), [joryirving/home-ops](https://github.com/joryirving/home-ops), [nicolerenee/infra](https://github.com/nicolerenee/infra), [vrozaksen/home-ops](https://github.com/vrozaksen/home-ops) |
| What does reproducible bare-metal bootstrap look like? | [khuedoan/homelab](https://github.com/khuedoan/homelab) |

## Proxmox ownership and repository boundaries

Proxmox automation has three distinct layers: installing and joining the PVE hosts, configuring the PVE cluster and host OS, and provisioning guests. Most repositories described as "Proxmox IaC" cover only the last layer. Keep that distinction explicit when borrowing a pattern.

- **[jrtashjian/homelab-proxmox-cluster](https://github.com/jrtashjian/homelab-proxmox-cluster)** is the clearest PVE-control-plane reference and deliberately uses a dedicated repository. Ansible owns host-level kernel and PCI configuration; Terraform owns cluster-visible configuration including repositories, VLANs and firewall policy, storage and backup jobs, metrics, certificates, identity, and guest templates. Its [bootstrap boundary](https://github.com/jrtashjian/homelab-proxmox-cluster#initial-proxmox-host-setup) is equally important: ISO installation, ZFS pool creation, initial cluster formation, and the first network bridges remain explicit manual prerequisites instead of being hidden behind nominally declarative tooling.
- **[dbrennand/home-ops](https://github.com/dbrennand/home-ops)** is a compact whole-estate example with a useful execution boundary. It uses [Ansible for PVE and PBS host-side storage](https://github.com/dbrennand/home-ops/tree/main/ansible/playbooks) and [local OpenTofu for guest VMs](https://github.com/dbrennand/home-ops/blob/main/docs/infrastructure/opentofu.md), with state in Backblaze rather than inside the Kubernetes environment. Study the separation between source-code colocation and independent execution, while treating direct edits to `/etc/pve/storage.cfg` as an implementation choice rather than a default to copy.
- **[Apocrathia/homelab](https://github.com/Apocrathia/homelab)** now has the strongest same-repository code and state layout in this group. Its [OpenTofu/Terragrunt tree](https://github.com/Apocrathia/homelab/blob/main/terraform/README.md) separates reusable providers and modules from per-VM deployments and per-deployment state; the general VM module also lets Proxmox HA move a guest without Terraform fighting placement. It still manages guests rather than the PVE cluster, and its [apply pipeline](https://github.com/Apocrathia/homelab/blob/main/.gitlab/tofu.gitlab-ci.yml) runs after merge using runners and 1Password Connect inside the Kubernetes cluster. Borrow the layout, not that dependency, when PVE must remain operable during a Kubernetes outage.
- **[Mafyuh/iac](https://github.com/Mafyuh/iac)** takes the broadest whole-estate monorepo approach. Kubernetes/Talos/Flux sits beside OpenTofu/Terraform for Proxmox, UniFi, Cloudflare, and other providers, plus Packer, NixOS, Ansible, and Docker. Its [Proxmox Terraform resource](https://github.com/Mafyuh/iac/blob/main/kubernetes/cluster/tofu-controller/tofu/proxmox.yaml) is reconciled by tofu-controller inside Kubernetes with `approvePlan: auto`; that is convenient convergence, but it couples PVE changes and recovery to Kubernetes availability. Treat it as the clearest counterexample when designing an independent PVE control path.
- **[mitchross/talos-argocd-proxmox](https://github.com/mitchross/talos-argocd-proxmox)** provisions Talos guests through Omni machine classes and the Omni Proxmox infrastructure provider. Proxmox itself is an existing prerequisite, while class and cluster-template snapshots are applied to Omni before it creates or deletes VMs. Study it for specialized Talos guest lifecycle and bootstrap ordering, not for PVE host or cluster configuration.
- **[dapperdivers/dapper-cluster](https://github.com/dapperdivers/dapper-cluster)** is the clean external-infrastructure boundary: Talos VMs consume Ceph supplied by Proxmox, but the repository does not own the Proxmox/Ceph control plane. Study it when the desired relationship is integration without lifecycle ownership.

The repository boundary and the failure boundary do not have to match. Keeping PVE code beside Kubernetes is compatible with operational independence only when the PVE runner, state backend, credentials, apply trigger, and recovery instructions remain usable with Kubernetes completely unavailable. For this lab, that favors a self-contained top-level `proxmox/` tree, separate state and secret material, plan-only PR automation, and an explicit manual apply from an independent management host. A later repository split remains mechanical if ownership, access, or release cadence eventually diverges.

## Kubernetes plus other platforms

"Same repository" can describe two materially different arrangements. Some repositories configure or provision the other platform; others only document a platform on which Kubernetes happens to run. Preserve that distinction when borrowing a pattern.

- **[eleboucher/homelab](https://github.com/eleboucher/homelab)** combines Talos/Kubernetes with Docker workloads on a VPS and OpenTofu-managed MikroTik networking. Look here when deciding whether routing, DNS, DHCP, firewall, and WireGuard intent belongs beside cluster GitOps.
- **[deedee-ops/home-ops](https://github.com/deedee-ops/home-ops)** combines a Talos cluster with NAS/edge and VPS workloads managed through Docker Compose, Ansible, and OpenTofu. Its split is relevant to workloads that should remain available while Kubernetes is down.
- **[ishioni/homelab-ops](https://github.com/ishioni/homelab-ops)** keeps Kubernetes/Talos, Docker workloads on TrueNAS, and Terraform-managed external services in one operations repository. It is a compact reference for putting cluster and NAS-side workloads under one review workflow without pretending they share one control plane.
- **[joryirving/home-ops](https://github.com/joryirving/home-ops)** operates multiple Talos clusters alongside an Ansible/Docker-managed VPS edge. It is a useful model for separating public ingress or recovery services from the main cluster.

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
