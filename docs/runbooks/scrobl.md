# scrobl nightly sync

## Intended deployment and acceptance

[scrobl](https://github.com/kelchm/scrobl) copies `kelchm`'s public Last.fm history into `/data/history.db` on a retained 1 GiB Longhorn RWO claim in `ai`. The [HelmRelease](../../kubernetes/apps/ai/scrobl/app/helmrelease.yaml) schedules one sync at 00:30 America/New_York, allows a late start within one hour, forbids overlapping scheduled Jobs, and fails a Job after one hour with no Kubernetes retries. It starts with an empty database; no laptop file is seeded. The first import is expected to take about 25 minutes, and subsequent runs seconds. The next run resumes by checking stored history after an interrupted sync.

This is prepared configuration, not a record of deployment acceptance. The `0.1.0` image has not completed a Last.fm sync inside a container. Its first production run must establish that TLS verification works with the image's own CA file, DNS/HTTPS pass the policy, the non-root process can write the claim, and the initial history import completes. Flux reporting the HelmRelease Ready only establishes that the scheduled workload was installed.

The image is built and published by scrobl's release workflow; home-lab pins its version and digest. Renovate proposes later `X.Y.Z` releases, and merging an update changes future Jobs. The distroless image has no shell or listener. It runs as UID/GID 1000 with a read-only root filesystem and its original `/data` working directory, so SQLite's database, journal and temporary files stay on the writable claim.

The Cilium policy permits only DNS queries for `ws.audioscrobbler.com` through CoreDNS and HTTPS to that hostname. The pod uses `ndots: 1` to query the absolute name before trying search suffixes, which the exact-name DNS rule rejects. Other DNS names and outbound destinations are not allowed.

## Production credential

The owner creates a dedicated Last.fm API key for this cluster, distinct from the development key. Store it as `SCROBL_API_KEY` in the `ai/scrobl-secrets` Secret through `app/secret.sops.yaml`, encrypted with the repository's Kubernetes SOPS rule, and include `./secret.sops.yaml` in `app/kustomization.yaml`. The container receives only that key through a Secret reference. Never use or retrieve the development key to bootstrap production.

## Observe the first scheduled run

After the owner merges and Flux reconciles, wait for the normal schedule rather than creating an additional writer:

```sh
kubectl -n ai get kustomization scrobl
kubectl -n ai get helmrelease scrobl
kubectl -n ai get cronjob scrobl
kubectl -n ai get jobs --sort-by=.metadata.creationTimestamp
kubectl -n ai logs job/<scrobl-job-name>
kubectl -n ai describe job <scrobl-job-name>
kubectl -n ai get pvc scrobl
```

Require the Job's `Complete=True` condition, exit code 0, and an advancing CronJob `lastSuccessfulTime`. Inspect the sync output for the initial history result; completion alone is not evidence of backup recoverability. Do not print the Secret or inspect the container's environment. Record the actual acceptance result in the deployment PR after observation.

A nonzero exit prints one reason on stderr; inspect the Job log, or its pod events if it never started. Existing `KubeJobFailed` raises a warning after 15 minutes and routes through vmalert/VMAlertmanager to normal-priority Pushover; the one-day Job TTL leaves time for that alert. A hung run becomes `DeadlineExceeded`. Grafana's CronJobs dashboard shows run state, and VictoriaLogs retains collected stderr. A missed schedule that creates no Job does not raise this failed-Job alert. A transient failure can recover on the following night; investigate persistent TLS, key, storage, image-pull or policy errors rather than adding a retry loop.

## One writer at a time

`Forbid` applies only to this CronJob's scheduled Jobs. scrobl has no application-level writer lock yet; that work is tracked in [scrobl#41](https://github.com/kelchm/scrobl/issues/41). Do not run another sync, manual Job, or laptop writer against this claim while a sync is active. Rare Kubernetes duplicate execution is not excluded by `Forbid` alone.

If an exceptional manual run is needed, first set `controllers.scrobl.cronjob.suspend: true` in the HelmRelease's `spec.values` through Git, wait for Flux to apply the suspension, and verify there are no active Jobs or still-running sync pods before starting exactly one Job. Let it finish before removing that setting through Git to restore the schedule. Suspension does not stop an already-running Job. Do not force-delete a pod whose process might still be writing.

When `serve` is ready, suspend the CronJob and wait for its writer to finish before handing this same claim to a single-replica `Recreate` Deployment. Revisit the writer lock and sync ownership then; do not run a separate scheduled writer concurrently without a supported ownership design.

## Persistence, backup exclusion and rollback

The claim uses Longhorn's two-replica `longhorn` class. Helm retains it on uninstall. It deliberately carries both `recurring-job.longhorn.io/source: enabled` and `recurring-job-group.longhorn.io/no-backup: enabled`: the owner chose to leave the cluster's detached-volume backup setting unchanged. There is no scheduled backup of this database. Replicas protect against a replica failure, not accidental deletion or logical damage. A lost file can mostly be read from Last.fm again, but the `removed` table cannot be reconstructed.

When `serve` keeps the volume attached continuously, remove only the `no-backup` label, retain `source`, and verify a completed normal backup. Follow [Longhorn backup and restore](longhorn-backup-restore.md#group-membership-and-opt-out) for the label synchronization semantics.

To stop future syncs, set `controllers.scrobl.cronjob.suspend: true` in the HelmRelease's `spec.values` through Git. To roll back an image or configuration update, revert it through Git; existing Jobs keep their original pod template and are not replaced by the revert. Keep the retained PVC. Do not delete it to resolve a version mismatch: a rollback does not reverse SQLite schema changes, and losing `removed` is permanent. Review scrobl's migration compatibility before future schema-changing upgrades.
