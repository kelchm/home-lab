# doco-cd self-update

The definitions enable native self-update on the three PVE nodes, two Sparks, and Athena. After the one-time migration below, merging a doco-cd image or configuration change to `main` deploys it like another declared project. Images stay pinned by tag and digest.

Verified on 2026-10-11: all six hosts run 0.125.1 with a healthy `/healthcheck`, successful repository polls, and unchanged workloads after a manual upgrade. Native self-update migration, handover, and rollback have not yet been exercised on these hosts. This runbook describes intended behavior and the acceptance checks still to perform.

A disposable ARM64 Docker rehearsal on the workstation on 2026-10-11, using the pinned 0.125.1 image and an absolute data bind, passed takeover, a successful configuration update, automatic rollback after a deliberately failing healthcheck, no retry of that failed revision across subsequent polls, and recovery on a corrected new revision. It also exposed the host Compose `up --wait` takeover race described below. This verifies the native path locally; it does not substitute for PVE/DSM migration checks or rehearse manual intervention during an unfinished handover.

## Native behavior

`SELF_UPDATE_ENABLED=true` and `SELF_UPDATE_STRATEGY=applier` select the [native applier](https://github.com/kimdre/doco-cd/blob/v0.125.1/wiki/docs/Advanced/Self-Updating.md). A temporary clone replaces doco-cd from outside, waits for Docker health, and restores the previous container if the replacement fails. The self-deployment timeout is 180 seconds. Handover and recovery are journaled under the existing `/data` bind mount; a failed revision and project hash are recorded to prevent repeated attempts against the same configuration at that commit. Any later commit, including an unrelated merge, can trigger another attempt, so revert or fix a broken self definition promptly.

This strategy fits the fixed container name, published ports, and PVE host networking. The helper inherits the predecessor's bind mounts and security options, including PVE's `apparmor=unconfined`. Preserve the project and service names (`doco-cd`), absolute data paths, and NAS age-key mount. PVE must retain its existing `daemon.json`; a deployer update requires no Docker daemon restart or networking changes. Do not add project networks on PVE, where Docker's default bridge is disabled.

| Host | SSH address | Self target | Local bootstrap directory |
|---|---|---|---|
| pve-sbx-1 | `kelchm@10.32.20.21` | `proxmox/monitoring/.doco-cd.yml` | `/opt/doco-cd` |
| pve-sbx-2 | `kelchm@10.32.20.22` | Same as pve-sbx-1 | `/opt/doco-cd` |
| pve-sbx-3 | `kelchm@10.32.20.23` | Same as pve-sbx-1 | `/opt/doco-cd` |
| spark-1 | `kelchm@10.32.21.31` | `sparks/monitoring/.doco-cd.spark-1.yml` | `/opt/doco-cd` |
| spark-2 | `kelchm@10.32.21.32` | `sparks/monitoring/.doco-cd.spark-2.yml` | `/opt/doco-cd` |
| Athena | `kelchm@10.32.20.5` | `synology/.doco-cd.yml` | `/volume1/docker/doco-cd` |

Both Sparks poll the shared monitoring configuration. The base Compose definition adds a `spark-2` poll; `compose.spark-1.yaml` replaces the whole poll list with the shared poll and the `spark-1` poll. The `spark-1` target deploys both sparkDash and doco-cd, using `[compose.yaml, compose.spark-1.yaml]` for the deployer. This preserves its dashboard poll through self-update.

## One-time migration

Merge the migration first and apply from a checkout of current `main`. Existing instances have self-update disabled and may report a refused self-deployment between merge and manual apply. That refusal leaves running containers in place. No token rotation, new age key, or Docker reinstall is needed.

Repeated refusals can raise `HostDeployFailing` on PVE and the Sparks while migration or a recovery override is pending. Diagnose the refused self target rather than changing collector containers; finish the migration or fix the self definition. Athena is not covered by that host alert rule.

Record the baseline before applying: doco-cd's image and health, recent logs, and workload container IDs, images, start times, restart counts, and deployment revisions. For PVE and the Sparks, substitute the address above:

```sh
ssh kelchm@HOST 'sudo -n docker inspect --format "{{.Config.Image}} {{json .State.Health}}" doco-cd; sudo -n docker logs --tail 20 doco-cd; sudo -n docker ps -a --no-trunc'
ssh kelchm@HOST 'sudo -n docker ps -aq | xargs sudo -n docker inspect --format "{{.Name}} {{.Id}} {{.Config.Image}} {{.State.StartedAt}} {{.RestartCount}} {{index .Config.Labels \"cd.doco.deployment.target.sha\"}}"'
```

Use `/usr/local/bin/docker` on Athena. Do not include environment variables or secret contents in receipts.

For a PVE node:

```sh
scp proxmox/platform/doco-cd/compose.yaml kelchm@HOST:/tmp/doco-cd.yaml
ssh kelchm@HOST 'set -e; sudo -n install -D -m 0644 /tmp/doco-cd.yaml /opt/doco-cd/compose.yaml; cd /opt/doco-cd; sudo -n docker compose up -d'
```

For `spark-2`:

```sh
scp sparks/platform/doco-cd/compose.yaml kelchm@10.32.21.32:/tmp/doco-cd.yaml
ssh kelchm@10.32.21.32 'set -e; sudo -n install -D -m 0644 /tmp/doco-cd.yaml /opt/doco-cd/compose.yaml; cd /opt/doco-cd; sudo -n docker compose up -d'
```

For `spark-1`, stage both files before applying:

```sh
scp sparks/platform/doco-cd/compose.yaml kelchm@10.32.21.31:/tmp/doco-cd.yaml
scp sparks/platform/doco-cd/compose.spark-1.yaml kelchm@10.32.21.31:/tmp/doco-cd.override.yaml
ssh kelchm@10.32.21.31 'set -e; sudo -n install -D -m 0644 /tmp/doco-cd.yaml /opt/doco-cd/compose.yaml; sudo -n install -m 0644 /tmp/doco-cd.override.yaml /opt/doco-cd/compose.override.yaml; cd /opt/doco-cd; sudo -n docker compose up -d'
```

For Athena, use [Bootstrap or migrate](../../synology/platform/doco-cd/README.md#bootstrap-or-migrate). Confirm that the data directory, age-key file, and Docker socket already exist; DSM refuses missing bind sources. Preserve existing state and the key.

The manual apply enables self-update. Allow the first repository poll to take over the stack from Git. The applier will recreate doco-cd even when its image is unchanged. Use `up -d` here: `up --wait` can fail with `No such container` when takeover replaces the container that host Compose is waiting on. Verify the final container after handover; the manual apply's first healthy result alone does not prove takeover.

## Acceptance and routine updates

On each migrated host, verify:

- The final container runs the image pinned on `main`, `/healthcheck` exits 0, and Docker reports `healthy` with no failing streak.
- Its labels include `cd.doco.deployment.name=doco-cd` and the migration revision in `cd.doco.deployment.target.sha`, proving takeover from Git.
- Logs show completed handover and a successful poll afterwards. Check the following scheduled poll too. Both the shared and `spark-1` polls must succeed on `spark-1`.
- Workload IDs, images, start times, restart counts, and revision labels match the baseline when their Git definitions have not changed. Completed one-shot services retain their prior state.
- The temporary helper has finished and one deployer remains running.

```sh
ssh kelchm@HOST 'sudo -n docker inspect --format "{{.Config.Image}} {{json .State.Health}} {{index .Config.Labels \"cd.doco.deployment.name\"}} {{index .Config.Labels \"cd.doco.deployment.target.sha\"}}" doco-cd; sudo -n docker logs --since 10m doco-cd; sudo -n docker ps -a --filter name=doco-cd'
```

Use `/usr/local/bin/docker` on Athena. Warnings about undecryptable secrets outside the host's scope are expected; check severity and the final job result. A newer-release notification does not change the pin.

After migration, a normal Renovate image PR triggers self-update. Read its release notes and verify handover. The local Compose copies are for bootstrap/recovery and are not rewritten by doco-cd; refresh them from a known-good Git revision before a later manual apply. Git and the running container's labels are authoritative.

The disposable rehearsal above verifies health-failure rollback. Repeat it when changing strategy or recovery behavior; do not introduce a broken definition on the live hosts solely to test rollback. Live takeover and platform-specific behavior remain acceptance work.

## Recovery

A failed self-update should restore the predecessor automatically. Capture logs from the predecessor, replacement, and helper before cleanup. Preserve the data directory and journal. The helper may still be finishing recovery after the deployer stops; inspect its state before intervening.

Manual recovery is only appropriate after native recovery has completed and no open handover journal remains. A newly created container can be classified as the successor of an open record and finalize it, even with self-update disabled. Manual interruption of such a record has not been rehearsed here: if a helper is stuck or a record remains open, preserve the evidence and diagnose native recovery before replacing containers. Do not delete the journal to bypass this gate.

The journal directory is `/var/lib/doco-cd/self-update` on PVE/Sparks and `/volume1/docker/doco-cd/data/self-update` on Athena. Open records are the per-handover JSON files; `poison.json`, snapshots, and quarantined files are not open records. Confirm helper state with `docker ps -a --filter label=cd.doco.self.stack=doco-cd`, and inspect record state without exposing snapshot environment variables.

```sh
ssh kelchm@HOST 'sudo -n ls -A /var/lib/doco-cd/self-update'
ssh kelchm@10.32.20.5 'sudo -n ls -A /volume1/docker/doco-cd/data/self-update'
```

Any non-hidden `*.json` other than `poison.json` or `*.snapshot.json` is an open record, even if its state says `failed` or `rolled_back`. `*.json.corrupt` files are quarantined. An absent directory means no journal has been created; any other listing error needs investigation before proceeding.

When there is no open handover, stage a known-good base as `compose.yaml` in the local bootstrap directory. On `spark-1`, also stage its matching Git `compose.spark-1.yaml` as `compose.override.yaml`. Save this separate recovery override as `compose.recovery.yaml` while repairing `main`; never overwrite the Spark override with it:

```yaml
services:
  doco-cd:
    environment:
      SELF_UPDATE_ENABLED: "false"
```

With no active helper or open handover, stop doco-cd and apply the explicit files, keeping the same project, mounts, healthcheck, and PVE security settings. Do not use `down -v` or delete `/data`.

```sh
# PVE or spark-2, after staging known-good files in /opt/doco-cd:
ssh kelchm@HOST 'set -e; if sudo -n docker inspect doco-cd >/dev/null 2>&1; then sudo -n docker stop doco-cd; fi; cd /opt/doco-cd; sudo -n docker compose -f compose.yaml -f compose.recovery.yaml up -d --wait --wait-timeout 180'
# spark-1, retaining its host-named dashboard override:
ssh kelchm@10.32.21.31 'set -e; if sudo -n docker inspect doco-cd >/dev/null 2>&1; then sudo -n docker stop doco-cd; fi; cd /opt/doco-cd; sudo -n docker compose -f compose.yaml -f compose.override.yaml -f compose.recovery.yaml up -d --wait --wait-timeout 180'
# Athena, after staging known-good files in /volume1/docker/doco-cd:
ssh kelchm@10.32.20.5 'set -e; if sudo -n /usr/local/bin/docker inspect doco-cd >/dev/null 2>&1; then sudo -n /usr/local/bin/docker stop doco-cd; fi; cd /volume1/docker/doco-cd; sudo -n /usr/local/bin/docker-compose -f compose.yaml -f compose.recovery.yaml up -d --wait'
```

Verify health, a successful poll of workload targets, and unchanged workloads. With self-update disabled, the self target may report a refusal until the repository fix is reviewed and merged. A new commit permits retry after native rollback. Once `main` is fixed, remove the recovery override, manually apply current `main`, and repeat takeover checks.

Rollback restores container configuration, not data migrations. Handle state-format or volume migrations separately with a backup and release-specific recovery instructions.
