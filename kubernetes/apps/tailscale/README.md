# Retired Tailscale Kubernetes subnet router

The Kubernetes-hosted `lan-subnet-router` and its operator were superseded on 2026-09-02 by the independent PVE router pair documented in [`docs/plans/20260902-tailscale-remote-admin.md`](../../../docs/plans/20260902-tailscale-remote-admin.md). Its six route approvals were withdrawn after the new pair passed route, DNS, and two-way failover tests.

This directory intentionally contains no Flux resources. Merging this change lets the parent `cluster-apps` Kustomization prune the child Connector and operator Kustomizations, which remove the Connector, operator resources, repository, and OAuth secret. The `tailscale` namespace is protected by `kustomize.toolkit.fluxcd.io/prune: disabled` and remains after that cleanup. Once Flux confirms that its managed resources are gone, delete the empty namespace manually, remove the stale `lan-subnet-router` and `tailscale-operator` machines, delete the unused OAuth client, and remove `tag:k8s` plus `tag:k8s-operator` from the Tailnet policy.

The historical design and its limitations remain in [`docs/plans/20260622-tailscale-operator.md`](../../../docs/plans/20260622-tailscale-operator.md).
