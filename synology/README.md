# Synology-hosted workloads

Compose projects that run directly on the Synology NAS (`Athena`), outside Kubernetes. [doco-cd](platform/doco-cd/) runs on Athena, polls `main`, and deploys the projects declared in [`.doco-cd.yml`](.doco-cd.yml). Flux does not reconcile anything here.

| Project | Purpose | Deployment |
|---|---|---|
| [`platform/doco-cd`](platform/doco-cd/) | Git-driven deployer for the projects in `.doco-cd.yml` | Manual apply over SSH |
| [`doco-cd-canary`](doco-cd-canary/) | Disposable canary for the deployer's acceptance checks | doco-cd |
| [`netbootxyz`](netbootxyz/) | PXE menus and local boot assets for lab hosts | Manual Compose apply through DSM Container Manager or SSH |

doco-cd is defined here but not yet bootstrapped on Athena; bootstrap and acceptance are tracked in [home-lab#379](https://github.com/kelchm/home-lab/issues/379). Until a project is deployed by doco-cd, its running Compose definition must match its directory, and any out-of-band DSM edit must be brought back to Git.

## How deployment works

- Merging to `main` is the deploy action. doco-cd polls the public repository every 3 minutes and applies each declared project whose files changed. Commits that touch nothing in a project leave it alone.
- Only declared projects are managed. Auto-discovery and `destroy` are disabled and CI rejects them, so removing a target from `.doco-cd.yml` or deleting its directory leaves its containers running; removing a project is a manual `docker compose -p <name> down`.
- doco-cd acts only through deployments. Event-driven reconciliation is off, and Docker restart policies keep services running and bring them back after a NAS reboot. When doco-cd is down, workloads keep running and merged changes wait until it returns.
- On each poll, doco-cd also redeploys a declared project whose long-running services are stopped or missing. Stop doco-cd before intervening by hand; see [break-glass](#break-glass-apply-over-ssh).
- doco-cd never deploys itself. Its own project under `platform/` is applied manually.
- `prune_images: false` keeps previous images on the NAS, so a revert does not download image layers again.

DSM Container Manager remains the inspection interface. It is not a second source of truth.

## Project contract

[`scripts/ci/validate-synology.sh`](../scripts/ci/validate-synology.sh) renders every project with `docker compose config` and enforces:

- A `compose.yaml` with a top-level `name`. A target's `.doco-cd.yml` name must match it, so a break-glass apply from the same file addresses the same project.
- Every image pinned by tag and digest, and no `build:`. Renovate proposes updates; merging one deploys it.
- Targets are `synology/<project>` directories outside `platform/`, without `destroy` or `auto_discovery`.
- In declared targets, repository paths are mounted only read-only, and only into one-shot services: `restart: "no"` plus the label `cd.doco.deployment.one_shot: "true"`.

The last rule exists because doco-cd deploys from a read-only export of each commit. Data written under a relative path lands in that export and is lost with the next revision, and every deployment gives the export a new path, which recreates any container that mounts it ([kimdre/doco-cd#1911](https://github.com/kimdre/doco-cd/issues/1911)). Therefore:

- Runtime data uses absolute host paths: `/volume1/docker/<project>/` for small state, or a dedicated shared folder for bulk data.
- Git-owned files such as configuration or menus reach a long-running service through a one-shot `publish` service that copies them into the runtime path after the service is healthy. No service depends on the publisher, so doco-cd needs the one-shot label to accept its exit instead of failing the deployment. The [canary](doco-cd-canary/compose.yaml) is the reference.

Also check in review: bind published ports to a specific Athena address, as `netbootxyz` does. No project has secrets yet; the first one that needs them adds a NAS-scoped age recipient to `.sops.yaml` and gives only that key to doco-cd, as described in the [NAS workloads plan](../docs/plans/20260620-nas-out-of-cluster-workloads.md#secrets--nas-scoped-age-recipient-the-one-deliberate-divergence). The cluster key never goes to the NAS.

## Add a project

1. Create `synology/<project>/compose.yaml` following the contract. Before the first deploy, create every bind-mount source on Athena, owned by the user each service runs as. DSM's Docker refuses to start a container whose bind source is missing.
2. Declare it in `.doco-cd.yml`:

   ```yaml
   ---
   name: <project>
   working_dir: synology/<project>
   prune_images: false
   reconciliation: false
   ```

   Add `timeout: <seconds>` when a first start or health gate takes longer than doco-cd's 180-second default.
3. Run `scripts/ci/validate-synology.sh`.
4. Merge, then [verify the deployment](platform/doco-cd/README.md#observe-deployments).

To adopt a project that was applied manually, keep its Compose project name. doco-cd takes it over, but Compose recreates only containers whose configuration changed. An unchanged container keeps running without doco-cd's labels until a later change recreates it.

## Update and roll back

Before merging an image update, read the upstream release notes for configuration or data migrations. A revert restores the previous definition, not the previous data. If an update migrates state irreversibly, copy the project's runtime directory aside on the NAS before merging.

To roll back, `git revert` the change and merge; doco-cd redeploys the previous definition on its next poll.

## Break-glass: apply over SSH

Use this when doco-cd is unavailable or a fix cannot wait for a merge. Stage the files first, then stop doco-cd; otherwise its next poll redeploys the project from `main`.

```sh
ssh kelchm@10.32.20.5 'mkdir -p /volume1/docker/break-glass/<project>'
rsync -a --delete synology/<project>/ \
  kelchm@10.32.20.5:/volume1/docker/break-glass/<project>/
ssh kelchm@10.32.20.5 '
  set -e
  sudo /usr/local/bin/docker stop doco-cd
  cd /volume1/docker/break-glass/<project>
  sudo /usr/local/bin/docker-compose up -d
  sudo /usr/local/bin/docker-compose ps -a
'
```

The project name comes from `compose.yaml`, so this applies the local definition to the doco-cd-managed project, and Compose recreates every container whose configuration differs. `up -d` still waits for health dependencies before starting a publisher. Check that long-running services are running and healthy and that one-shot services exited `0`. Do not use `--wait`: Compose 2.20.1 treats a completed publisher without dependents as a failure.

Land the same change on `main`, then run `sudo /usr/local/bin/docker start doco-cd`. Its next poll deploys `main` because the project's files differ from the commit it last deployed. After that deployment has rerun the one-shot services, which were the only ones mounting the staged files, remove `/volume1/docker/break-glass/<project>`.
