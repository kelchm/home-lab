# Kanidm restore

Recovery procedures for the operator-managed Kanidm cluster. Two backup layers
to choose from:

- **Application-consistent dumps** at `/data/<timestamp>.bak` (alongside the
  live `/data/kanidm.db`). Produced by Kanidm's `[online_backup]` (configured
  via `KANIDM_ONLINE_BACKUP_*` env vars). Daily at 01:00 ET, retained 7
  in the PVC.
- **Volume-level backups** of the Longhorn PVC. Produced by the cluster-wide
  `default` `RecurringJob` (daily 03:00 ET retain 7 + weekly Sunday 04:00 ET
  retain 4). Backup target: `nfs://10.32.25.5:/volume1/backups-k8s-prod/longhorn`.

The volume-level backups capture both the live DB and the bundled `.bak`
files in a single snapshot.

## Failure modes

### DB-only corruption (most common)

Triggered by a bad migration, accidental delete via the Kanidm API or web UI,
or human error. Use the latest in-PVC `.bak` — fastest path, no NAS round-trip.

```sh
# 1. Suspend Flux and scale the Kanidm replicaGroup to 0. The PVC must be
#    free (no pod attached) before a debug pod can mount it RWO.
flux suspend kustomization kanidm -n flux-system
kubectl -n identity patch kanidm kanidm --type merge \
  -p '{"spec":{"replicaGroups":[{"name":"default","replicas":0,"role":"write_replica","primaryNode":true}]}}'
kubectl -n identity wait --for=delete pod -l app.kubernetes.io/name=kanidm --timeout=120s

# 2. List candidate .bak files. Use busybox (kanidm/server is distroless,
#    no shell utilities). Pod terminates on shell exit.
kubectl -n identity run kanidm-list --rm -it \
  --image=busybox:1.37 \
  --overrides='{"spec":{"containers":[{"name":"x","image":"busybox:1.37","stdin":true,"tty":true,
    "command":["sh"],"volumeMounts":[{"name":"d","mountPath":"/data"}]}],
    "volumes":[{"name":"d","persistentVolumeClaim":{"claimName":"kanidm-data-kanidm-default-0"}}]}}' \
  -- sh
# Inside: ls -lt /data/*.bak — pick the most recent that pre-dates the
# corruption event. Note its full path. Exit.

# 3. Restore from the chosen .bak. Use the kanidm/server image so kanidmd
#    is on PATH; override the entrypoint to invoke the restore subcommand
#    directly (image has no shell to wrap it in).
kubectl -n identity run kanidm-restore --rm -it \
  --image=kanidm/server:1.9.2 \
  --overrides='{"spec":{"securityContext":{"runAsUser":389,"runAsGroup":389,"fsGroup":389},
    "containers":[{"name":"x","image":"kanidm/server:1.9.2",
      "command":["kanidmd","database","restore","/data/<timestamp>.bak"],
      "env":[{"name":"KANIDM_DOMAIN","value":"auth.home.kelch.io"},
             {"name":"KANIDM_ORIGIN","value":"https://auth.home.kelch.io"},
             {"name":"KANIDM_DB_PATH","value":"/data/kanidm.db"}],
      "volumeMounts":[{"name":"d","mountPath":"/data"}]}],
    "volumes":[{"name":"d","persistentVolumeClaim":{"claimName":"kanidm-data-kanidm-default-0"}}]}}'
# kanidmd reads its config from env vars when no /etc/kanidm/server.toml is
# present. Container exits when restore completes.

# 4. Resume Flux → Kanidm CR reconciles the StatefulSet back to replicas: 1.
flux resume kustomization kanidm -n flux-system

# 5. Sanity-check by logging in as idm_admin (password unchanged across restore).
```

### PVC loss

Triggered by Longhorn replica corruption, accidental PVC delete, or
migration error. Restore the most recent Longhorn backup to a new PVC.

Two recovery paths:

**(a) Run with the live DB from the snapshot — faster, accepts crash-consistency.**
SQLite's WAL is journaled; Kanidm replays the journal at startup and recovers
from a crash-consistent state. This is fine for Longhorn snapshots taken
during normal operation.

**(b) Restore from the bundled `.bak` — slower, guaranteed-consistent state.**
Use when (a) shows DB integrity errors at startup, or when you specifically
want a known-good point in time vs. the latest snapshot.

```sh
# 1. Suspend Flux and scale the Kanidm replicaGroup to 0 (as in DB-only).
flux suspend kustomization kanidm -n flux-system
kubectl -n identity patch kanidm kanidm --type merge \
  -p '{"spec":{"replicaGroups":[{"name":"default","replicas":0,"role":"write_replica","primaryNode":true}]}}'

# 2. Restore Longhorn backup to a new PVC via the Longhorn UI (or CLI).
#    See docs/runbooks/longhorn-backup-restore.md for the Longhorn-side flow.
#    Result: a new PVC named e.g. kanidm-data-kanidm-default-0-restored.

# 3. Rebind: delete the old (broken) PVC, rename the restored PVC to match
#    the StatefulSet's volumeClaimTemplate naming (kanidm-data-kanidm-default-0).
#    PVC rename is done by re-creating with the same name pointing at the
#    restored PV. See longhorn-backup-restore.md.

# 4. Optionally restore from .bak inside the new PVC (path (b) above) using
#    the debug-pod sequence from "DB-only corruption" step 3.

# 5. Resume Flux. Kanidm CR reconciles the StatefulSet back to replicas: 1
#    against the restored PVC.
flux resume kustomization kanidm -n flux-system
```

### NAS loss (catastrophic)

In-PVC `.bak` files are retained for 7 days. After that, no recovery —
this is a cluster-wide off-site replication gap, not a Kanidm-specific
one. Out of scope for this runbook.

## Verification (post-restore)

- [ ] Kanidm pod Running, 1/1 Ready
- [ ] `kubectl -n identity get kanidm kanidm` shows `Available=True`
- [ ] Web UI loads at `https://auth.home.kelch.io`
- [ ] Login as `idm_admin` succeeds (password from
      `kanidm-admin-passwords` Secret unchanged)
- [ ] `KanidmGroup arr-admins` and `KanidmPersonAccount kelchm` reconciled
      (visible in web UI)
- [ ] OAuth2 clients (PR D+) re-issue secrets if any were rolled by the
      restore — kaniop will emit them into `<client>-kanidm-oauth2-credentials`
      Secrets in consumer namespaces

## Restore drill

The DB-only path should be exercised against a copy of production data
once before the cluster is trusted with real OIDC clients. Walking through
the procedure proves the `.bak` files are usable end-to-end and that the
debug-pod overrides JSON works against current cluster state.

Recommended cadence for the drill: after every Kanidm major-version
upgrade (1.9 → 1.10 → ...). Backup file format may shift between majors,
and a stale runbook reads as broken when you actually need it.
