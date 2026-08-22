---
name: deploy-broadsheet
description: One-shot deploy of the broadsheet app to the k8s cluster — bumps the HelmRelease image pin to a freshly-built main image and rolls it out via Flux. Use when the user wants to deploy/ship/roll out broadsheet after merging to main (e.g. "deploy broadsheet", "ship the latest broadsheet", "get the new broadsheet version live").
---

# Deploy broadsheet

Broadsheet ships as `ghcr.io/kelchm/broadsheet:sha-<short>`, built and pushed by
the repo's `docker-publish` workflow **on every push to `main`** (no version tag
needed). Deploying = pointing this repo's HelmRelease pin at that new tag and
letting Flux reconcile.

## The one-shot

From the home-lab repo root:

```bash
scripts/deploy-broadsheet.sh            # deploy the latest broadsheet main commit
scripts/deploy-broadsheet.sh sha-83262ea # or an explicit image tag
scripts/deploy-broadsheet.sh --dry-run   # preview; changes nothing
```

The script does everything: resolves the target tag (default = latest
`origin/main` of the broadsheet checkout), **waits for CI to publish that image
to GHCR** (~15 min for a fresh merge), bumps `tag:` in
`kubernetes/apps/iot/broadsheet/app/helmrelease.yaml`, commits + pushes to
`main`, forces a Flux reconcile, and waits for the rollout to go healthy.

## How to run it (for the agent)

1. If the user just merged and wants it live, run the script with **no argument**
   — it targets the latest `origin/main` and waits for the image itself. If they
   name a specific tag/sha, pass it.
2. Run a `--dry-run` first if you want to confirm the resolved old→new tags, then
   run for real.
3. Report the old→new tag, that the image was found, and the final rollout
   status. If it fails, the script says why (image never published, push failed,
   rollout stuck) — relay that; don't retry blindly.

## Good to know

- **Direct to `main` is correct here.** A broadsheet image bump is a single-app
  pure-config change (per `AGENTS.md`); a bad one is just inconvenient — revert
  the commit. No PR needed.
- **No data loss, brief blip.** The archive/cache/DB live on the Longhorn PVC
  (`existingClaim: broadsheet`). The Deployment uses `Recreate` (required by the
  RWO volume), so the old pod stops before the new one mounts the *same* volume —
  a few seconds of downtime during the swap, nothing lost.
- **Prerequisites:** run where `mise` is active (the script loads mise's env for
  `KUBECONFIG`, `flux`, `kubectl`), and the 1Password SSH agent unlocked so the
  `git push` can sign. `curl`/`python3` are used for the anonymous GHCR
  tag check.
- **Rollback:** `scripts/deploy-broadsheet.sh <previous-sha>` (or `git revert`
  the deploy commit) points the pin back and rolls the old image out the same way.

## Mechanics reference

- Image tag scheme: `docker/metadata-action` `type=sha,format=short` → `sha-` +
  first 7 chars of the commit SHA.
- Flux objects: GitRepository `flux-system` (ns `flux-system`), Kustomization
  `broadsheet` (ns `iot`), HelmRelease `broadsheet` (ns `iot`), Deployment
  `broadsheet` (ns `iot`). A GitHub webhook already makes pushes live in seconds;
  the explicit reconcile just makes it immediate and lets the script verify.
