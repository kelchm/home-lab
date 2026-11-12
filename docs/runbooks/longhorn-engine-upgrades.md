# Longhorn V1 engine consolidation

Procedure for moving existing V1/blockdev volumes to the default engine of the already-installed manager. The owner approved consolidation on 2026-11-11 in [#1282](https://github.com/kelchm/home-lab/issues/1282). This window targets `docker.io/longhornio/longhorn-engine:v1.12.2`; it does not upgrade the manager, chart, Talos, or Kubernetes. [Longhorn 1.13](https://github.com/kelchm/home-lab/pull/686) remains held on its separate release and manager-transition conditions.

## Intended behavior and baseline

The [upstream V1 procedure](https://longhorn.io/docs/1.12.2/deploy/upgrade/upgrade-engine/) supports a live upgrade of a healthy blockdev volume after the manager is upgraded. Replica processes temporarily double; afterward the original replica count returns. `iscsi` frontend volumes, V2 volumes, degraded volumes and strict-local attached volumes are outside this procedure. The [1.12.2 API implementation](https://github.com/longhorn/longhorn-manager/blob/v1.12.2/manager/volume.go) checks image availability, competing upgrades/migration and volume health before changing the requested image.

Read-only inventory on 2026-11-11: 39 V1/blockdev volumes, 38 attached/healthy and one detached/idle. Nineteen use 1.12.0; nineteen use 1.12.1, including detached Scrobl; Photon already uses 1.12.2. Each requested/current image agrees. The 1.12.2 engine is deployed on all three nodes. Six instance-managers span 1.12.1 and 1.12.2; all are Running/Ready with their current storage-network address present on `lhnet1`. Automatic engine upgrades remain disabled (`concurrent-automatic-engine-upgrade-per-node-limit=0`).

The canary is the two-replica 100 MiB `ai/digikey-mcp` claim, volume `pvc-31e77fda-b254-437d-9c9e-dc31ed1571f9`, initially on 1.12.0. Its rotating OAuth-token cache is sensitive: inspect JSON validity, size and file hashes without printing the contents. Its latest nightly backup completed on 2026-11-11 at 07:02 UTC. The API-backed workflow below must establish a new backup and inspect a restored copy before the first engine change.

## Gates before each engine change

Record a baseline and the exact volume/claim mapping, source and target images, replica count, engine/replica process state, instance-manager assignment, backup, workload identity and application check. Store raw operational evidence outside Git; record outcomes in #1282.

- All Kubernetes nodes are Ready and uncordoned. Flux is healthy; no unrelated rollout or maintenance runs concurrently.
- Every in-use volume is attached/healthy; none is degraded or faulted. No other volume has differing desired/current images, rebuild, migration, expansion, restore or backup activity. The deliberately detached Scrobl volume is not a failure while idle.
- All Longhorn nodes/disks are Ready and schedulable. Require the target EngineImage deployed and compatible on all nodes involved. Live-upgrade replica processes reuse their data directories; temporary duplicate processes do not imply a second full data copy.
- Every instance-manager is Running/Ready, and its current sandbox has the storage network on `lhnet1`. Use the shared read-only check, which verifies the actual interface as well as the annotation:

  ```sh
  kubectl -n longhorn-system get pods -l longhorn.io/component=instance-manager -o json |
    .agents/skills/talos-rollout/scripts/failing-instance-managers.sh
  ```

  An empty result is success only if the input contains the expected instance-managers and the command succeeds. Stop if any check fails.
- The default BackupTarget is available and synced. Each backed-up candidate has a Completed backup less than 24 hours old with no error. Existing tested CNPG/Kanidm recovery procedures remain required for their application data; a successful snapshot alone does not prove recovery. Regenerable Lemon/Photon exclusions retain their declared policy. Scrobl gets the one-time backup described below.
- Warning/critical alerts match the recorded baseline (currently the sandbox guest and Spark metrics); stop for any new alert or application error. Check the same baseline after each volume.

### Known replacement timeout and window limits

[longhorn#14520](https://github.com/longhorn/longhorn/issues/14520) is open as of 2026-11-11. The tagged 1.12.1 and 1.12.2 instance-manager `ProcessReplace` code waits up to 30 seconds for a replacement to become Running, but then stops the original process even if the replacement remains Starting. The manager's healthy result can therefore miss a transient EIO window. Blockdev volumes share the affected target/socket-reload path. Low I/O and serialized upgrades reduce exposure; they cannot guarantee that a first slow replacement will avoid interruption. Confirm the live-versus-offline availability decision against this known risk before an engine action, especially on database volumes.

Use a low-I/O window without active recurring/manual backups or heavy media/download activity. Issue no further engine actions at or after 06:30 UTC; the default group's nightly backup starts at 07:00 UTC. Resume only after its backups finish and all gates pass again. Complete Scrobl's owned-attachment/restore work by 05:00 UTC, at least 30 minutes before its next 05:30 UTC scheduled writer; otherwise suspend through Git or defer it. Recheck these limits before every action, not only when starting the window.

For every live upgrade, capture the hosting instance-manager's logs starting before the action. Require both `prepare to replace process` and `replacement process ... started running` for the selected engine, with elapsed time below 10 seconds, followed by successful replacement. Missing timing evidence or a transition at/above 10 seconds stops the sequence. This stop condition protects later volumes, not the already-submitted action. Check target-node kernel logs for EIO, SCSI timeouts and ext4 read-only/errors, and application logs for corresponding database/I/O errors before choosing the next volume. Read the current upstream [release known issues](https://github.com/longhorn/longhorn/wiki/Release-Known-Issues) before the window.

## Canary backup and restore evidence

1. Through Longhorn's normal API, create a uniquely named snapshot, then request its backup to the existing `default` target. Do not change retention or backup-target settings. Wait for the matching Backup CR to become Completed; record its URL, snapshot time and backup name.
2. Follow [inspect a copy](longhorn-backup-restore.md#1-restore-through-an-explicit-volume): create a uniquely named two-replica Longhorn Volume with `spec.fromBackup` set to that URL, a Retain PV and a PVC in a disposable namespace. The Volume/PV/CSI handle/claimRef must all use the new restore name, never the live volume's handle. Label the restore Volume `recurring-job-group.longhorn.io/no-backup: enabled`.
3. Capture the live `tokens.json` hash immediately before the snapshot and again after it. Wait for restore completion, then mount only the copy read-only in an inspection pod. Check that `tokens.json` exists, parses as JSON and matches that hash when both live hashes agree. If they differ because a token rotated, JSON validity/size alone does not establish byte identity: take another bounded snapshot or document the narrower recovery evidence. Do not expose token values or use restored credentials against DigiKey. The live workload and claim remain mounted and healthy throughout.
4. Stop the inspection pod, then delete only the recorded disposable PVC, PV, restore Volume and namespace. Verify their engines, replicas and VolumeAttachments disappear and that no backup history was created for the restore copy. Preserve the source recovery backup. Re-run the full production health gates before upgrading the canary.

## Upgrade one volume

Use the manager's `engineUpgrade` action, not a direct patch of the Volume's image or controller-owned Engine/Replica objects. The following local API path uses the authenticated Kubernetes Service proxy; it avoids Longhorn UI authentication and does not follow the API's advertised action URLs.

```sh
kubectl proxy --address=127.0.0.1 --port=18001
```

In another terminal, inspect the exact candidate immediately before the action:

```sh
volume='pvc-31e77fda-b254-437d-9c9e-dc31ed1571f9'
lh_api='http://127.0.0.1:18001/api/v1/namespaces/longhorn-system/services/http:longhorn-backend:9500/proxy/v1'
curl --noproxy '*' --connect-timeout 5 --max-time 30 --fail-with-body --silent --show-error "$lh_api/volumes/$volume"
```

Require `image == currentImage ==` the reviewed source image, `state=attached`, `robustness=healthy`, V1/blockdev, the expected replica count and the gates above. Confirm `engineUpgrade` is offered. Submit exactly once, with the fixed target:

```sh
curl --noproxy '*' --connect-timeout 5 --max-time 30 --fail-with-body --silent --show-error \
  -H 'Content-Type: application/json' \
  --data '{"image":"docker.io/longhornio/longhorn-engine:v1.12.2"}' \
  "$lh_api/volumes/$volume?action=engineUpgrade"
```

Record the response and observe without issuing a second action. Stop if the API rejects the request. If the POST times out, re-read the requested/current image and engine state before considering any recovery; do not blindly resubmit. Allow temporary extra replicas, but do not proceed while any volume is degraded/faulted, the target is stuck, or logs show new storage/I/O errors.

Completion requires the Volume's `spec.image` and `status.currentImage` both equal the target, attached/healthy state, exactly one running Engine at the target, Replica CR count equal to `numberOfReplicas`, every Replica at the target image with empty `failedAt` and Running state, and exactly that many Engine `replicaModeMap` entries, all RW. Record old replica names before the action; require that none remains in any instance-manager's process status. Validate all instance-manager storage interfaces and cluster health again, plus the replacement timing and node/application I/O checks above. For the canary, also require the original application pod remains Ready without new restarts, token-cache JSON remains readable, and a non-mutating DigiKey catalog tool succeeds. Observe the canary for at least two minutes after convergence before the next volume.

## Continue one volume at a time

After the canary, process ordinary application volumes, then monitoring, then each HA database/identity replica volume individually with application checks between them. For every CNPG cluster, require `ContinuousArchiving=True`, record the current primary immediately before each action, and upgrade its standby volume before its primary volume. Stop if roles change unexpectedly or any member is unhealthy. Never upgrade both volumes of a CNPG cluster or multiple Kanidm replicas concurrently. The manager/engine API upgrade does not itself authorize restarting applications, moving database primaries or draining nodes. Preserve each workload's original pod identities and restart counts as evidence.

Use only volumes from the reviewed inventory whose requested/current source image still agrees and differs from the target. Re-run the gates for every candidate and wait for full convergence before selecting another; do not fire all actions and wait at the end. Keep automatic engine upgrades at zero. Do not delete old instance-managers or engine images to accelerate cleanup; let Longhorn retire unreferenced processes normally.

### Detached Scrobl

Confirm the scheduled writer is inactive, no running pod uses its claim, and the window ends well before its next 00:30 America/New_York run. If that cannot be guaranteed, suspend its CronJob through Git and wait for Flux before proceeding, then restore the schedule afterward. Do not start a second sync.

Require no active Job or running pod uses the claim, including manually created Jobs. Record the Longhorn VolumeAttachment's complete `spec.attachmentTickets` map before this operation. Use a normal maintenance attachment (frontend disabled, named attachment owner) on one healthy node to take a one-time snapshot/backup of the idle volume:

```sh
curl --noproxy '*' --connect-timeout 5 --max-time 30 --fail-with-body -sS \
  -H 'Content-Type: application/json' \
  --data '{"hostId":"k8s-prod-1","disableFrontend":true,"attachedBy":"engine-consolidation-20261111","attacherType":"longhorn-api","attachmentID":"engine-consolidation-20261111"}' \
  "$lh_api/volumes/$scrobl_volume?action=attach"
```

Wait for attached/healthy state and confirm the frontend remains disabled and the new ticket has the intended node/owner. Verify a restored copy with SQLite `PRAGMA integrity_check`, history/loved/removed row counts and known sync metadata; never point an application writer at the copy. Remove the inspection resources. Keep the existing `no-backup` label and detached-volume-backup setting unchanged. Release only this operation's attachment ticket:

```sh
curl --noproxy '*' --connect-timeout 5 --max-time 30 --fail-with-body -sS \
  -H 'Content-Type: application/json' \
  --data '{"attachmentID":"engine-consolidation-20261111","hostId":"k8s-prod-1"}' \
  "$lh_api/volumes/$scrobl_volume?action=detach"
```

Never send `forceDetach`: it clears every attachment ticket. Wait for detached state and require the ticket map to equal its baseline, then use the normal Engine Upgrade action for the offline change. Verify requested/current images and stopped replica image assignments converge to 1.12.2 while it stays detached. If attachment is needed to complete verification, use another owned maintenance attachment with the same checks and release it afterward. Finish with the original detached/idle state and exactly the original ticket map.

Manual backups are outside recurring-job retention. Record their URLs in #1282 and retain them as this window's recovery points; do not silently delete them or expect the daily retention count to expire them. Manual snapshot cleanup, if needed, is a separately recorded action and must not purge a backup's recovery source while a restore is in progress.

## Stop and recovery

An engine action is not undone by reverting Git. On failure, stop the sequence, preserve Volume/Engine/Replica/IM JSON, events and logs, and leave healthy replicas and attachment owners alone. Do not force detach, delete instance-managers, delete a PVC or drain a node to make the image counts converge.

If `spec.image` has changed but `status.currentImage` is still the original image, the API permits requesting that current image to cancel the pending attempt; inspect the actual engine/replica state before choosing that recovery. A completed upgrade is not a pending attempt: do not guess that an image downgrade restores the earlier state. Prefer a diagnosed forward fix. If data recovery is required, preserve the failed source and restore the recorded backup into a separately named copy using the tested procedure; inspect and validate it before any separately chosen application cutover. CNPG and Kanidm follow their maintained application-specific recovery procedures rather than restoring arbitrary live replica volumes.

## Completion

Require every inventoried volume's requested/current image and its active or stopped replica assignments at 1.12.2, all in-use volumes attached/healthy, original application replicas healthy with output checks, no new warning/critical alerts, and Flux back at baseline. Confirm no restore resources, temporary attachment tickets, suspended CronJobs or altered automatic-upgrade settings remain. Old unused IM/image cleanup may lag; record their reference counts instead of forcing deletion. Record per-volume results and any exception in #1282, update this runbook's verified outcome, and retain the separate 1.13 hold.

## Verified outcome

Preparation only as of 2026-11-11. No engine has been changed by this procedure yet. Recovery evidence, canary result and final inventory will be recorded after execution.
