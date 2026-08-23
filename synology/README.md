# Synology-hosted workloads

Compose projects that run directly on the Synology NAS, outside Kubernetes.
They are versioned here but applied explicitly; Flux does not reconcile them.

| Project | Purpose | Deployment |
|---|---|---|
| [`netbootxyz`](netbootxyz/) | PXE menus and local boot assets for lab hosts | Manual Compose apply through DSM Container Manager or SSH |

The future shared deployment model is tracked in
[home-lab#379](https://github.com/kelchm/home-lab/issues/379). Until that work
is complete, the running Compose definition must match this directory and any
out-of-band DSM edit must be brought back to Git.
