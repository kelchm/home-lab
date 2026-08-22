# Documentation

Reference, runbooks, and design records for the home-lab cluster. For a
high-level overview start with the [repo README](../README.md).

## Reference

Current-state, authoritative docs.

- [**architecture.md**](architecture.md) — infrastructure architecture: network
  / VLAN layout, the IP-addressing convention, LB pools, BGP, DNS, and storage
  strategy. The source of truth for how the cluster is wired and why.
- [**repository-validation.md**](repository-validation.md) — CI event semantics,
  concurrency, aggregate-check invariants, and the boundary between validation
  and Flux deployment.
- [**roadmap.md**](roadmap.md) — forward-looking design: the planned PVE
  virtualization cluster and deferred work.

## Further reading

- [**other-labs.md**](other-labs.md) — curated external home labs grouped by the architectural question each one helps answer.

## Runbooks

Step-by-step operational procedures — reach for these when something needs doing
or fixing.

| Runbook | Use when |
|---|---|
| [longhorn-backup-restore](runbooks/longhorn-backup-restore.md) | Restoring a PV from a Longhorn backup; DR drills. |
| [longhorn-storage-network-cutover](runbooks/longhorn-storage-network-cutover.md) | Moving Longhorn replica traffic onto the dedicated storage VLAN. |
| [kanidm-restore](runbooks/kanidm-restore.md) | Recovering Kanidm from DB corruption or PVC loss. |
| [kanidm-kaniop-cutover](runbooks/kanidm-kaniop-cutover.md) | Migrating Kanidm from the hand-rolled StatefulSet to the kaniop operator. |
| [kanidm-oauth2-client-drift](runbooks/kanidm-oauth2-client-drift.md) | Recovering a KanidmOAuth2Client when its K8s secret drifts. |
| [arr-suite-bootstrap](runbooks/arr-suite-bootstrap.md) | The *arr suite's NAS identity, ACL layout, and NFS export — current state. |
| [seerr-bootstrap](runbooks/seerr-bootstrap.md) | Wiring Seerr to Jellyfin/Radarr/Sonarr, household request policy, and break-glass admin (the non-GitOps UI steps). |
| [multus-conf-absent-recovery](runbooks/multus-conf-absent-recovery.md) | Recovering pods stuck on an absent Multus network config. |
| [metamcp-bootstrap](runbooks/metamcp-bootstrap.md) | Initialising the MetaMCP gateway and onboarding backend MCP servers (the non-GitOps registry steps). |
| [bambuddy-bootstrap](runbooks/bambuddy-bootstrap.md) | Bringing up Bambuddy, validating printer containment and OIDC, and exercising backup recovery. |

## Plans

Design docs written ahead of a change. Each carries a status header; once
executed, the current state lives in the corresponding runbook.

- [20260508-arr-suite-setup](plans/20260508-arr-suite-setup.md) — the original media-stack deployment plan.
- [20260509-kaniop-migration](plans/20260509-kaniop-migration.md) — Kanidm → kaniop operator pivot.
- [20260513-arr-hardlink-rework](plans/20260513-arr-hardlink-rework.md) — single share-root mount + NFSv4 ACL isolation.
- [20260620-metamcp-mcp-rollout](plans/20260620-metamcp-mcp-rollout.md) — MetaMCP gateway + curated backend MCP server rollout.
- [20260620-nas-out-of-cluster-workloads](plans/20260620-nas-out-of-cluster-workloads.md) — Synology-hosted S3 backup target and Git-driven deployment model for workloads outside Kubernetes.
- [20260622-sequenced-dependency-upgrades](plans/20260622-sequenced-dependency-upgrades.md) — ordered major-version upgrade pass for k8s-prod (Longhorn → Talos → k8s → Gateway API/Traefik → kaniop).
- [20260703-observability-rework](plans/20260703-observability-rework.md) — conclude the observability bakeoff, converge on VictoriaMetrics/VictoriaLogs, and restore useful alerting and estate coverage.
- [20260814-pve-cluster](plans/20260814-pve-cluster.md) — draft design for the independent three-node PVE cluster: hardware, IPs, networks, storage, backup, HA, updates, IaC boundaries, and rollout gates.
- [20260818-sn770-zfs-qualification-results](plans/20260818-sn770-zfs-qualification-results.md) — destructive SN770 reproduction matrix, evidence index, findings, and remaining qualification work supporting the PVE storage decision.

## Decision records & benchmarks

- [storage-benchmarks](storage-benchmarks.md) — Longhorn fio results, pre/post storage-network cutover.
- [observability-bakeoff](observability-bakeoff.md) — VictoriaMetrics/Logs vs. Prometheus/Loki evaluation.

## Related docs elsewhere in the repo

- [network/unifi/README.md](../network/unifi/README.md) — UniFi-side BGP/FRR, firewall, and IDS/IPS suppression intent (UniFi isn't GitOps-managed).
- [AGENTS.md](../AGENTS.md) — conventions for working in this repo (branch vs. direct-to-main).
