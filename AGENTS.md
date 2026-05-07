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
