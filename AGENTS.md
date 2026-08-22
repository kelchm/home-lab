# Working in this repo

Solo-maintained homelab. Flux watches `main`; a GitHub webhook makes pushes
live in seconds. Talos config (`talos/` tree) rolls manually via `talosctl`
after merge — Flux doesn't touch it.

## Branch + PR vs direct-to-main

Branch + PR when the change is cross-cutting (multiple subsystems), can't be
undone with `git revert` (Talos rollouts, secrets, anything mutating state
outside Flux's reach), or needs a non-obvious manual step after merge.

Direct to `main` for small single-app pure-config changes — the recent
"Shift Longhorn backup cron…", "Stamp cluster=k8s-prod…" commits are the
shape.

Rule of thumb: would a bad version auto-deploying be embarrassing or just
inconvenient? Embarrassing → branch.

## Talos operations

Before any Talos upgrade or node reboot, load and follow the repository's
`talos-rollout` skill. Run the guarded `task talos:upgrade-node IP=<node-ip>`
task for upgrades; it deliberately uses `--reboot-mode=powercycle` because
these nodes have hung during the default kexec reboot path.

Operate on one node at a time. Do not continue until etcd has quorum, every
Kubernetes node is Ready, every Longhorn volume is healthy, and every Longhorn
instance-manager has its `longhorn-system/storage-network` attachment on
`lhnet1`.
