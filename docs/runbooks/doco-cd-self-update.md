# doco-cd self-update

The definitions enable native self-update on the three PVE nodes, two Sparks, and Athena. After the one-time migration below, merging a doco-cd image or configuration change to `main` deploys it like another declared project. Images stay pinned by tag and digest.

Verified:

- On 2026-10-11, all six live hosts passed the manual 0.125.1 rollout: healthy `/healthcheck`, successful repository polls, and unchanged workloads.
- Disposable ARM64 rehearsals with pinned 0.125.1 passed takeover, configuration updates, failed-healthcheck rollback and suppression after successful takeover, corrected-revision recovery, and stopping the first-takeover retry loop by removing the self target.
- On 2026-10-12, both Spark identities passed native interpolation through takeover and later updates without a host identity file or runtime `SPARK_HOST` variable. Both polls succeeded and the canary stayed unchanged. A local self-disable override on the `spark-2` fixture then kept the deployer healthy and delivered a deliberate shared-poll workload update while pausing self-replacement.
- On 2026-10-12 EDT, all six live hosts completed native applier takeover from `c70c7b1f` on 0.125.1: healthy `/healthcheck` probes, at least two successful polls after handover finalization, no deployer errors, no active helper or open journal, and unchanged workload containers. Both Sparks retained their shared and host-specific polls; PVE retained host networking, AppArmor settings, and daemon configuration. Spark 1's legacy poll override was checked against Git and archived after acceptance.
- On 2026-10-18, all six live hosts completed the cross-version native update from 0.125.1 to 0.129.0 at `64e3a69e` within seven minutes of merge. Each host replaced its deployer twice: the 0.125.1 predecessor handed over to 0.129.0, and that successor replaced itself once more about three minutes later. The second-generation deployer then stayed in place through later polls. Every host ended healthy on 0.129.0 with at least two successful polls after its last finalization, both Spark targets included, no deployer errors, no active helper or open journal, unchanged workload containers, and unchanged Spark/NAS project network IDs. Athena's completed one-shot services reran and exited 0, as they do on any deployer start.

Per-host live acceptance evidence is recorded in [#815](https://github.com/kelchm/home-lab/issues/815). PVE and DSM helper behavior is now verified. No host selected the project-network recreation path, and the existing Spark/NAS project networks retained their IDs. That path and manual interruption of an unfinished journal remain unverified.

## Native behavior

`SELF_UPDATE_ENABLED=true` and `SELF_UPDATE_STRATEGY=applier` select the [native applier](https://github.com/kimdre/doco-cd/blob/v0.125.1/wiki/docs/Advanced/Self-Updating.md). A temporary clone replaces doco-cd from outside, waits for Docker health, and restores the previous container if the replacement fails. Self deployments use the native three-minute timeout default in 0.125.1. Handover and recovery are journaled under the existing `/data` bind mount. After successful takeover, a failed revision and project hash prevent repeated attempts against that same configuration at that commit. Any later commit, including an unrelated merge, can trigger another attempt, so revert or fix a broken self definition promptly. The first takeover has a retry exception described below.

This strategy fits the fixed container name, published ports, and PVE host networking. The helper inherits the predecessor's bind mounts and security options, including PVE's `apparmor=unconfined`. Preserve the project and service names (`doco-cd`), absolute data paths, and NAS age-key mount. PVE must retain its existing `daemon.json`; a deployer update requires no Docker daemon restart or networking changes. Do not add project networks on PVE, where Docker's default bridge is disabled.

| Host | SSH address | Self target | Local bootstrap directory |
|---|---|---|---|
| pve-sbx-1 | `kelchm@10.32.20.21` | `proxmox/monitoring/.doco-cd.yml` | `/opt/doco-cd` |
| pve-sbx-2 | `kelchm@10.32.20.22` | Same as pve-sbx-1 | `/opt/doco-cd` |
| pve-sbx-3 | `kelchm@10.32.20.23` | Same as pve-sbx-1 | `/opt/doco-cd` |
| spark-1 | `kelchm@10.32.21.31` | `sparks/monitoring/.doco-cd.spark-1.yml` | `/opt/doco-cd` |
| spark-2 | `kelchm@10.32.21.32` | `sparks/monitoring/.doco-cd.spark-2.yml` | `/opt/doco-cd` |
| Athena | `kelchm@10.32.20.5` | `synology/.doco-cd.yml` | `/volume1/docker/doco-cd` |

Both Sparks use the same Compose file and poll the shared monitoring configuration plus their own host target. Each self target sets `environment.SPARK_HOST` to its host name; native interpolation supplies the host-specific poll during takeover and later updates. Manual applies supply the same value on the command line. The variable is required, with no default or host `.env` file. The `spark-1` target still deploys both sparkDash and doco-cd.

## Bootstrap or migrate

Merge at the start of the migration session, then apply from a checkout of current `main`. Existing instances have self-update disabled and may report a refused self-deployment between merge and manual apply. That refusal leaves running containers in place. No token rotation, new age key, or Docker reinstall is needed.

In 0.125.1, a failed first takeover can repeat: the manually started predecessor has no doco-cd deployment revision, rollback preserves that absence, and the failed-revision check is bypassed without a prior deployed commit. The restored deployer can immediately retry at startup and on later polls. To stop that loop, remove only the YAML document with `name: doco-cd` from the applicable target file on `main`, preserving all sibling workload documents and valid document separators. After native rollback, the restored deployer's next poll then has no self stack to replace. Keep its data/journal intact and wait for native recovery to finish before using the manual recovery procedure. This exception affects the deployer; other projects remain running.

The edit has different scope on each platform:

- PVE: remove the self document from `proxmox/monitoring/.doco-cd.yml`, retaining `monitoring`. This pauses self-updates on **all three PVE nodes**, including already-migrated nodes; their running deployers remain in place.
- `spark-1`: remove the self document from `sparks/monitoring/.doco-cd.spark-1.yml`, retaining `sparkdash` and the existing poll configuration.
- Athena: remove the self document from `synology/.doco-cd.yml`, retaining every workload document.
- `spark-2`: its `sparks/monitoring/.doco-cd.spark-2.yml` contains only the self document, so remove that file temporarily. The dedicated `spark-2` poll will report a missing target until it is restored; the separate shared monitoring poll continues. Do not leave an empty target file, which 0.125.1 also rejects.

Restore the removed self document or file after correcting the definition and completing recovery, then repeat takeover checks on affected instances. Do not remove the shared PVE, `spark-1`, or Athena target file, or drop their polls to stop a self-update loop.

Repeated refusals can raise `HostDeployFailing` on PVE and the Sparks while migration or a recovery override is pending. Diagnose the refused self target rather than changing collector containers; finish the migration or fix the self definition. Athena is not covered by that host alert rule.

Record the baseline before applying: doco-cd's image and health, recent logs, and workload container IDs, images, start times, restart counts, and deployment revisions. For PVE and the Sparks, substitute the address above:

```sh
ssh kelchm@HOST 'sudo -n docker inspect --format "{{.Config.Image}} {{json .State.Health}}" doco-cd; sudo -n docker logs --tail 20 doco-cd; sudo -n docker ps -a --no-trunc'
ssh kelchm@HOST 'sudo -n docker ps -aq | xargs sudo -n docker inspect --format "{{.Name}} {{.Id}} {{.Config.Image}} {{.State.StartedAt}} {{.RestartCount}} {{index .Config.Labels \"cd.doco.deployment.target.sha\"}}"'
```

Use `/usr/local/bin/docker` on Athena. Do not include environment variables or secret contents in receipts.

On the Sparks and Athena, also record the host Compose version and labels of the existing `doco-cd_default` network before applying:

```sh
ssh kelchm@HOST 'sudo -n docker compose version; sudo -n docker network inspect --format "{{.Name}} {{json .Labels}}" doco-cd_default'
ssh kelchm@10.32.20.5 'sudo -n /usr/local/bin/docker-compose version; sudo -n /usr/local/bin/docker network inspect --format "{{.Name}} {{json .Labels}}" doco-cd_default'
```

PVE uses host networking and has no deployer project network. The local rehearsals used host Compose 5.5.1, matching the version embedded in doco-cd 0.125.1. A different network name or a nonempty `com.docker.compose.config-hash` label that differs from the embedded Compose calculation selects a separate path that recreates the project network and moves the helper to Docker's default bridge. This path has not been rehearsed locally.

For a PVE node:

```sh
scp proxmox/platform/doco-cd/compose.yaml kelchm@HOST:/tmp/doco-cd.yaml
ssh kelchm@HOST 'set -e; sudo -n install -D -m 0644 /tmp/doco-cd.yaml /opt/doco-cd/compose.yaml; cd /opt/doco-cd; sudo -n docker compose up -d'
```

For either Spark, substitute its address for `HOST` and `spark-1` or `spark-2` for `SPARK_NAME`:

```sh
scp sparks/platform/doco-cd/compose.yaml kelchm@HOST:/tmp/doco-cd.yaml
ssh kelchm@HOST 'set -e; sudo -n install -D -m 0644 /tmp/doco-cd.yaml /opt/doco-cd/compose.yaml; cd /opt/doco-cd; sudo -n env SPARK_HOST=SPARK_NAME docker compose -f compose.yaml up -d'
```

Always name `compose.yaml` explicitly on a Spark. This ignores older bootstrap override files still on the host. No host identity file is created; Git's self target supplies the identity for native updates.

For Athena, use [Bootstrap or migrate](../../synology/platform/doco-cd/README.md#bootstrap-or-migrate). Confirm that the data directory, age-key file, and Docker socket already exist; DSM refuses missing bind sources. Preserve existing state and the key.

The manual apply enables self-update. Allow the first repository poll to take over the stack from Git. The applier will recreate doco-cd even when its image is unchanged. Use `up -d` here: `up --wait` can fail with `No such container` when takeover replaces the container that host Compose is waiting on. Capture the manually applied predecessor's logs during takeover, including `self-update: strategy selected` and its `reasons`, before it is removed; attach `sudo -n docker logs --follow --timestamps doco-cd` immediately after the apply and retain the output (on Athena, use `sudo -n /usr/local/bin/docker logs --follow --timestamps doco-cd`). Verify the final container after handover; the manual apply's first healthy result alone does not prove takeover.

## Acceptance and routine updates

On each migrated host, verify:

- The final container runs the image pinned on `main`, `/healthcheck` exits 0, and Docker reports `healthy` with no failing streak.
- Its labels include `cd.doco.deployment.name=doco-cd` and the migration revision in `cd.doco.deployment.target.sha`, proving takeover from Git.
- Retain the takeover strategy and reasons from the predecessor log. `a project network must be recreated` identifies the unrehearsed network-recreation path; on Sparks/Athena, compare the final network labels with the baseline and confirm helper completion.
- Logs show `self-update finalised` followed by at least two successful completed polls. An initial poll can finish before handover finalization, so count only later polls. On each Spark, both the shared and host-specific polls must pass twice after finalization.
- Workload IDs, images, start times, restart counts, and revision labels match the baseline when their Git definitions have not changed. Completed one-shot services retain their prior state.
- The temporary helper has finished, one deployer remains running, and no open handover record remains (see [Recovery](#recovery)).

```sh
ssh kelchm@HOST 'sudo -n docker inspect --format "{{.Config.Image}} {{json .State.Health}} {{index .Config.Labels \"cd.doco.deployment.name\"}} {{index .Config.Labels \"cd.doco.deployment.target.sha\"}}" doco-cd; sudo -n docker logs --since 10m doco-cd; sudo -n docker ps -a --filter name=doco-cd'
```

Use `/usr/local/bin/docker` on Athena. Warnings about undecryptable secrets outside the host's scope are expected; check severity and the final job result. A newer-release notification does not change the pin.

After `spark-1` passes the takeover checks above, archive its legacy `/opt/doco-cd/compose.override.yaml` poll override so a later apply without `-f` cannot silently load it. Inspect the file first and confirm it is the old `spark-1` poll override; stop if it contains other local changes. Keep the archive for rollback; numbered backups preserve any earlier archive.

```sh
ssh kelchm@10.32.21.31 'set -e; cd /opt/doco-cd; if [ -f compose.override.yaml ]; then sudo -n mv --backup=numbered compose.override.yaml compose.override.yaml.pre-self-update; fi'
```

Continue to use the explicit Compose files and required `SPARK_HOST` when applying manually. Archiving the legacy override does not change the running container.

After migration, a normal Renovate image PR triggers self-update. Read its release notes and verify handover. A version change can replace the deployer twice on each host, as in the 0.125.1 to 0.129.0 update; a third replacement on the same host at the same commit is a loop, so check container start times as well as health. The local Compose copies are for bootstrap/recovery and are not rewritten by doco-cd; refresh them from a known-good Git revision before a later manual apply. Git and the running container's labels are authoritative.

The native health gate checks the HTTP health endpoint and Docker API access, not successful repository polling or deployment. A successor with a broken Git/deployment path can pass health and remain running; verify the post-handover polls and fix or recover that configuration explicitly.

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

When there is no open handover, stage a known-good base as `compose.yaml` in the local bootstrap directory. On a Spark, supply its `SPARK_HOST` value when applying. Save this separate recovery override as `compose.recovery.yaml` while repairing `main`:

```yaml
services:
  doco-cd:
    environment:
      SELF_UPDATE_ENABLED: "false"
```

With no active helper or open handover, stop doco-cd and apply the explicit files, keeping the same project, mounts, healthcheck, and PVE security settings. Do not use `down -v` or delete `/data`.

```sh
# PVE, after staging known-good files in /opt/doco-cd:
ssh kelchm@HOST 'set -e; if sudo -n docker inspect doco-cd >/dev/null 2>&1; then sudo -n docker stop doco-cd; fi; cd /opt/doco-cd; sudo -n docker compose -f compose.yaml -f compose.recovery.yaml up -d --wait --wait-timeout 180'
# Either Spark, substituting its address and SPARK_NAME (spark-1 or spark-2):
ssh kelchm@HOST 'set -e; if sudo -n docker inspect doco-cd >/dev/null 2>&1; then sudo -n docker stop doco-cd; fi; cd /opt/doco-cd; sudo -n env SPARK_HOST=SPARK_NAME docker compose -f compose.yaml -f compose.recovery.yaml up -d --wait --wait-timeout 180'
# Athena, after staging known-good files in /volume1/docker/doco-cd:
ssh kelchm@10.32.20.5 'set -e; if sudo -n /usr/local/bin/docker inspect doco-cd >/dev/null 2>&1; then sudo -n /usr/local/bin/docker stop doco-cd; fi; cd /volume1/docker/doco-cd; sudo -n /usr/local/bin/docker-compose -f compose.yaml -f compose.recovery.yaml up -d --wait'
```

Verify health, a successful poll of workload targets, and unchanged workloads. With self-update disabled, the self target may report a refusal until the repository fix is reviewed and merged. A new commit permits retry after native rollback. Once `main` is fixed, remove the recovery override, manually apply current `main`, and repeat takeover checks.

Rollback restores container configuration, not data migrations. Handle state-format or volume migrations separately with a backup and release-specific recovery instructions.
