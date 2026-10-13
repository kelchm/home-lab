# doco-cd on Athena

[doco-cd](https://github.com/kimdre/doco-cd) deploys the Compose projects declared in [`synology/.doco-cd.yml`](../../.doco-cd.yml) from `main`; the [Synology README](../../README.md) covers the deployment model and project contract. The definitions enable native self-update. Manual apply is needed for bootstrap, the one-time migration, and recovery.

The deployer's verified version and migration status are maintained in the [migration and recovery runbook](../../../docs/runbooks/doco-cd-self-update.md). Workload acceptance checks 1–6 below passed previously; checks 7 and 8 are tracked in [home-lab#379](https://github.com/kelchm/home-lab/issues/379).

## Configuration

| Setting | Value |
|---|---|
| Source | Anonymous HTTPS poll of `https://github.com/kelchm/home-lab.git`, `main`, every 3 minutes |
| Deploy config | `synology/.doco-cd.yml` (`DEPLOY_CONFIG_BASE_DIR=synology`) |
| Inbound endpoints | None published. The health endpoint listens on container port 8080; the webhook listener and REST API stay disabled because no secret is set. |
| State | `/volume1/docker/doco-cd/data`: a bare mirror of the repository and a read-only export per deployed commit |
| Docker access | `/var/run/docker.sock` |
| Secrets | `SOPS_AGE_KEY_FILE` points at the NAS-scoped age key, mounted read-only from `/volume1/docker/doco-cd/sops_age_key` |

Docker socket access is root-equivalent on Athena. Anyone who can merge to `main` can run arbitrary containers on the NAS, which is the same trust boundary Flux has for the cluster.

The state directory is root-only (`0700`): doco-cd decrypts each project's `*.sops.env` in place inside the commit export, and leaves it world-readable. No workload data lives under the state directory. Only one-shot `publish` services mount files from its commit exports, and doco-cd's garbage collector keeps every export that a deployed container references.

## NAS age key

doco-cd decrypts `synology/**/*.sops.env` with an age key that exists only for Athena. The private key is in 1Password as `athena-doco-cd-sops-age-key`; its recipient is the `synology/` rule in [`.sops.yaml`](../../../.sops.yaml). Install it before applying a definition that mounts it, because DSM's Docker refuses to start a container whose bind-mount source is missing:

```sh
op read 'op://Private/athena-doco-cd-sops-age-key/password' | ssh kelchm@10.32.20.5 '
  sudo install -m 0400 -o root -g root /dev/stdin /volume1/docker/doco-cd/sops_age_key
  sudo ls -l /volume1/docker/doco-cd/sops_age_key
'
```

Then run the apply below.

## Bootstrap or migrate

Apply from a checkout of `main`, so the running definition matches Git. For recovery, first satisfy the [helper and journal gates](../../../docs/runbooks/doco-cd-self-update.md#recovery).

```sh
ssh kelchm@10.32.20.5 'sudo install -d -m 0700 -o root -g root /volume1/docker/doco-cd/data'
rsync -av synology/platform/doco-cd/compose.yaml \
  kelchm@10.32.20.5:/volume1/docker/doco-cd/compose.yaml
ssh kelchm@10.32.20.5 '
  set -e
  cd /volume1/docker/doco-cd
  sudo /usr/local/bin/docker-compose pull
  sudo /usr/local/bin/docker-compose up -d
  sudo /usr/local/bin/docker-compose ps
  sudo /usr/local/bin/docker logs --tail 50 doco-cd
'
```

DSM's Docker refuses to start a container whose bind-mount source is missing, so `data/` must exist before the first start. doco-cd then clones the repository and deploys every declared project.

This apply enables native self-update. Allow the next poll to take over `doco-cd` from Git, then verify the final container's labels, health, successful polls, and unchanged workloads using the [acceptance checks](../../../docs/runbooks/doco-cd-self-update.md#acceptance-and-routine-updates).

After migration, merging a Renovate doco-cd image update triggers self-update; no routine SSH apply is needed. The applier restores the predecessor when a replacement fails health. Read the release notes before merging, particularly for state-layout or deploy-configuration changes. The local Compose copy may become stale; refresh it from a known-good Git revision before manual recovery.

## Observe deployments

```sh
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker logs --since 1h doco-cd'
ssh kelchm@10.32.20.5 "sudo /usr/local/bin/docker ps -a \
  --filter label=cd.doco.deployment.name \
  --format '{{.Label \"cd.doco.deployment.name\"}}  {{.Names}}  {{.Status}}  {{.Label \"cd.doco.deployment.target.sha\"}}'"
```

Each new commit on `main` logs one "skipping file that could not be decrypted" warning per SOPS-encrypted file in the repository. These are the cluster's secrets; Athena deliberately has no key for them.

A container's `cd.doco.deployment.target.sha` label is the `main` revision of the deployment that created it. Containers whose configuration did not change in a later deployment keep an earlier revision. doco-cd reads the newest label in a project as that project's deployed commit. Polls that find no change to the project leave every label untouched.

## Acceptance checks

Create the canary's state directory before the first bootstrap with `sudo mkdir -p /volume1/docker/doco-cd-canary`. Then run these checks in order and stop at the first failure. The canary is disposable and exists only for them. Checks 1–6 gate adopting further projects; the canary stays declared until check 7 is done.

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
| doco-cd stopped or unhealthy | Running projects are unaffected; merged changes wait. | Read deployer and helper logs and follow [self-update recovery](../../../docs/runbooks/doco-cd-self-update.md#recovery); reapply only after native recovery has finished with no active helper or open handover journal. |
| doco-cd self-update fails | The applier restores the predecessor and records the failed revision. | Preserve the journal, inspect helper and deployer logs, and follow [self-update recovery](../../../docs/runbooks/doco-cd-self-update.md#recovery). |
| A deployment fails (render, pull, health gate) | doco-cd logs the error and retries on later polls. Services that Compose had not yet replaced keep running. | Fix forward or `git revert` on `main`. Use break-glass if the service is down and a merge cannot wait. |
| GitHub unreachable | Polls fail; nothing changes on the NAS. | None. Deployments resume when polling succeeds. |
| NAS reboot | Docker restarts `unless-stopped` containers, including doco-cd. One-shot `publish` services do not rerun; their output persists in the runtime path. | Verify with [Observe deployments](#observe-deployments). |

## Reconstruction

If the state directory is lost or corrupt, recreate it only after satisfying the [helper and journal gates](../../../docs/runbooks/doco-cd-self-update.md#recovery). Preserve any remaining state for diagnosis before removing it; these commands must not interrupt an unfinished handover. Workload data lives elsewhere.

```sh
ssh kelchm@10.32.20.5 '
  cd /volume1/docker/doco-cd
  sudo /usr/local/bin/docker-compose down
  sudo rm -rf /volume1/docker/doco-cd/data
'
```

Then run the [bootstrap](#bootstrap-or-migrate). doco-cd clones the repository again and redeploys each declared project from `main`.

On a rebuilt NAS, install Container Manager, restore each project's runtime directory where it matters (see each project's README), and bootstrap doco-cd. It deploys every declared project on its first poll.
