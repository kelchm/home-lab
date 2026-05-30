# Documentation

Reference, runbooks, and design records for the home-lab cluster. For a
high-level overview start with the [repo README](../README.md).

## Reference

Current-state, authoritative docs.

- [**architecture.md**](architecture.md) — infrastructure architecture: network
  / VLAN layout, the IP-addressing convention, LB pools, BGP, DNS, and storage
  strategy. The source of truth for how the cluster is wired and why.
- [**roadmap.md**](roadmap.md) — forward-looking design: the planned second
  ("sandbox") cluster, two-cluster topology, and deferred work.

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
| [multus-conf-absent-recovery](runbooks/multus-conf-absent-recovery.md) | Recovering pods stuck on an absent Multus network config. |
| [mcpjungle-bootstrap](runbooks/mcpjungle-bootstrap.md) | Initialising the mcpjungle MCP gateway and registering servers. |

## Plans

Design docs written ahead of a change. Each carries a status header; once
executed, the current state lives in the corresponding runbook.

- [20260508-arr-suite-setup](plans/20260508-arr-suite-setup.md) — the original media-stack deployment plan.
- [20260509-kaniop-migration](plans/20260509-kaniop-migration.md) — Kanidm → kaniop operator pivot.
- [20260513-arr-hardlink-rework](plans/20260513-arr-hardlink-rework.md) — single share-root mount + NFSv4 ACL isolation.

## Decision records & benchmarks

- [storage-benchmarks](storage-benchmarks.md) — Longhorn fio results, pre/post storage-network cutover.
- [observability-bakeoff](observability-bakeoff.md) — VictoriaMetrics/Logs vs. Prometheus/Loki evaluation.

## Related docs elsewhere in the repo

- [network/unifi/README.md](../network/unifi/README.md) — UniFi-side BGP/FRR and firewall intent (UniFi isn't GitOps-managed).
- [tools/mcpjungle-sync/README.md](../tools/mcpjungle-sync/README.md) — MCP client/server sync utility.
- [AGENTS.md](../AGENTS.md) — conventions for working in this repo (branch vs. direct-to-main).
