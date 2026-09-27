# doco-cd on Athena

[doco-cd](https://github.com/kimdre/doco-cd) deploys the Compose projects declared in [`synology/.doco-cd.yml`](../../.doco-cd.yml) from `main`; the [Synology README](../../README.md) covers the deployment model and project contract. This directory is the deployer itself. It is applied manually and is never one of its own targets.

State as of 2026-09-26: defined and CI-validated, not yet bootstrapped on Athena. [home-lab#379](https://github.com/kelchm/home-lab/issues/379) tracks bootstrap and the acceptance checks below.

## Configuration

| Setting | Value |
|---|---|
| Source | Anonymous HTTPS poll of `https://github.com/kelchm/home-lab.git`, `main`, every 3 minutes |
| Deploy config | `synology/.doco-cd.yml` (`DEPLOY_CONFIG_BASE_DIR=synology`) |
| Inbound endpoints | None published. The webhook listener and REST API stay disabled because no secret is set. |
| State | `/volume1/docker/doco-cd/data`: a bare mirror of the repository and a read-only export per deployed commit |
| Docker access | `/var/run/docker.sock` |

Docker socket access is root-equivalent on Athena. Anyone who can merge to `main` can run arbitrary containers on the NAS, which is the same trust boundary Flux has for the cluster.

No workload data lives under the state directory. Only one-shot `publish` services mount files from its commit exports, and doco-cd's garbage collector keeps every export that a deployed container references.

## Bootstrap or upgrade

Apply from a checkout of `main`, so the running definition matches Git:

```sh
ssh kelchm@10.32.20.5 'mkdir -p /volume1/docker/doco-cd'
rsync -av synology/platform/doco-cd/compose.yaml \
  kelchm@10.32.20.5:/volume1/docker/doco-cd/compose.yaml
ssh kelchm@10.32.20.5 '
  cd /volume1/docker/doco-cd
  sudo /usr/local/bin/docker-compose pull
  sudo /usr/local/bin/docker-compose up -d --wait
  sudo /usr/local/bin/docker-compose ps
  sudo /usr/local/bin/docker logs --tail 50 doco-cd
'
```

The first start creates `data/` and clones the repository, then deploys every declared project.

Renovate proposes doco-cd image updates like any other image. Merging one changes only Git, so run the same apply immediately afterwards. doco-cd is pre-1.0 and releases often; read the release notes before merging, particularly for changes to the data layout or deploy configuration.

## Observe deployments

```sh
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker logs --since 1h doco-cd'
ssh kelchm@10.32.20.5 "sudo /usr/local/bin/docker ps -a \
  --filter label=cd.doco.deployment.name \
  --format '{{.Label \"cd.doco.deployment.name\"}}  {{.Names}}  {{.Status}}  {{.Label \"cd.doco.deployment.target.sha\"}}'"
```

A container's `cd.doco.deployment.target.sha` label is the `main` revision of the deployment that created it. Containers whose configuration did not change in a later deployment keep an earlier revision. doco-cd reads the newest label in a project as that project's deployed commit. Polls that find no change to the project leave every label untouched.

## Acceptance checks

Run these after the first bootstrap, in order, and stop at the first failure. The canary is disposable and exists only for them. Checks 1–6 gate adopting further projects; the canary stays declared until check 7 is done.

1. **Deployer healthy.** `doco-cd` reports `healthy`, and its log shows a poll of `main` without errors.
2. **Canary deployed from `main`.** Within one poll, `doco-cd-canary-canary-1` is healthy, `doco-cd-canary-publish-1` exited `0`, and `/volume1/docker/doco-cd-canary/message.txt` reads `generation 1`. Both containers carry the revision that doco-cd's log reports for that deployment, and later polls leave it unchanged.
3. **Update keeps data.** Merge a change that sets `message.txt` to `generation 2` and pins both canary services to `docker.io/library/busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e`. The message updates, the canary is recreated on the new image, and `starts.log` gains a line while keeping the first.
4. **Revert restores.** `git revert` that change and merge. The message and image return to `generation 1` and `1.38.0`, `docker image ls` shows both images, and `starts.log` keeps every line.
5. **Unrelated commits are inert.** After a merge that does not touch the canary, its container IDs, start times, and labels are unchanged.
6. **Break-glass works.** Change `message.txt` locally to `generation 3` and apply it with the [break-glass procedure](../../README.md#break-glass-apply-over-ssh); the message updates while doco-cd is stopped. Merge the same change, start doco-cd, and confirm its next poll deploys that commit: the publisher carries the new revision and the message stays `generation 3`.
7. **Reboot survival without the deployer.** During a planned DSM update or restart, stop doco-cd first (`sudo /usr/local/bin/docker stop doco-cd`; `unless-stopped` keeps it down across the reboot). Afterwards, every declared project's long-running services are running with their data intact, which proves workloads do not depend on the deployer. Then start doco-cd and confirm its first poll leaves projects without Git changes untouched. Athena serves NFS to `k8s-prod`, so do not reboot it just for this check.
8. **Undeclared removal is inert.** Merge a change that removes the canary target from `.doco-cd.yml` and deletes `synology/doco-cd-canary/`. The canary keeps running through the following polls. Then clean it up by hand:

   ```sh
   ssh kelchm@10.32.20.5 '
     sudo /usr/local/bin/docker-compose -p doco-cd-canary down
     sudo rm -rf /volume1/docker/doco-cd-canary
   '
   ```

## Failure handling

| Situation | Effect | Response |
|---|---|---|
| doco-cd stopped or unhealthy | Running projects are unaffected; merged changes wait. | Read `docker logs doco-cd`, fix the cause, and reapply as above. |
| A deployment fails (render, pull, health gate) | doco-cd logs the error and retries on later polls. Services that Compose had not yet replaced keep running. | Fix forward or `git revert` on `main`. Use break-glass if the service is down and a merge cannot wait. |
| GitHub unreachable | Polls fail; nothing changes on the NAS. | None. Deployments resume when polling succeeds. |
| NAS reboot | Docker restarts `unless-stopped` containers, including doco-cd. One-shot `publish` services do not rerun; their output persists in the runtime path. | Verify with [Observe deployments](#observe-deployments). |

## Reconstruction

If the state directory is lost or corrupt, recreate it. Workload data lives elsewhere.

```sh
ssh kelchm@10.32.20.5 '
  cd /volume1/docker/doco-cd
  sudo /usr/local/bin/docker-compose down
  sudo rm -rf /volume1/docker/doco-cd/data
'
```

Then run the [bootstrap](#bootstrap-or-upgrade). doco-cd clones the repository again and redeploys each declared project from `main`.

On a rebuilt NAS, install Container Manager, restore each project's runtime directory where it matters (see each project's README), and bootstrap doco-cd. It deploys every declared project on its first poll.
