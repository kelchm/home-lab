# Kanidm restore

Recovery procedures for the operator-managed Kanidm cluster: three replicas, `kanidm-default-0` to `-2`, each with its own Longhorn claim `kanidm-data-kanidm-default-<n>` mounted at `/data`. They replicate to each other in both directions. `primaryNode: true` makes replica 0 the primary; Kaniop configures each secondary's link to it with `automatic_refresh`, so an empty secondary reloads itself from replica 0.

Two backup layers:

- **Online backups** at `/data/backup-<RFC 3339 UTC timestamp>.json.gz` on every replica, beside the live `/data/kanidm.db`. Kanidm's `[online_backup]` writes them, configured by the `KANIDM_ONLINE_BACKUP_*` variables in `kanidm.yaml`: daily at 05:00 UTC, seven kept. Each replica writes its own, carrying that replica's server identity and replication key.
- **Volume backups** of each claim by the cluster-wide Longhorn `default` recurring jobs (daily at 07:00 UTC, retain 7; weekly on Sunday at 08:00 UTC, retain 4). The volume backup holds the live database and that replica's online backups. The Longhorn store is copied to Backblaze B2 daily; see [Longhorn backup and restore](longhorn-backup-restore.md).

## What restore requires

From the Kanidm 1.11.2 and Kaniop 0.21.0 sources and documentation:

- **The same Kanidm version that wrote the backup.** The book requires the same version; `kanidmd` enforces the same major.minor and fails with `DB0001` otherwise ([book](https://github.com/kanidm/kanidm/blob/v1.11.2/book/src/backup_and_restore.md), [check](https://github.com/kanidm/kanidm/blob/v1.11.2/server/lib/src/be/mod.rs#L1957-L1966)). Use the image in `kanidm.yaml` unless the backup predates an upgrade; then use the version that wrote it.
- **A stopped server.** `kanidmd database restore` takes the exclusive lock the running server holds, so it cannot run through `kubectl exec`.
- **`KANIDM_DOMAIN`, `KANIDM_ORIGIN`, `KANIDM_DB_PATH`, `KANIDM_TLS_CHAIN` and `KANIDM_TLS_KEY`** when no `server.toml` is supplied. The restore pod below mirrors the Job Kaniop's own restore runs ([source](https://github.com/pando85/kaniop/blob/v0.21.0/libs/operator/src/kanidm/restore/legacy.rs)). `restore` reads the `.json.gz` directly.

Restore keeps the backup's server UUID, domain UUID and replication position. A restored replica therefore looks like an ordinary replica that has fallen behind, and its partners send it every change made since the backup ([replication update vector](https://github.com/kanidm/kanidm/blob/v1.11.2/server/lib/src/repl/ruv.rs)). **Restoring one replica while the others keep their data undoes the restore.** Restore one authoritative replica, and discard the others' data so they reload from it.

A refresh cannot pick its source: `kanidmd refresh-replication-consumer` reloads from whichever partner answers first. Never refresh a replica while another still holds data you are discarding.

## Restore the cluster to a backup

Use this for database corruption, an unwanted change through the API or web UI, or any restore to an earlier point. Replica 0 is the authoritative node. Replicas 1 and 2 lose their claims and reload from it.

*Intended, unrehearsed.* It follows the sequence Kaniop's own `KanidmRestore` controller runs ([documentation](https://github.com/pando85/kaniop/blob/v0.21.0/Documentation/src/usage/backup-restore.md)). That controller is marked experimental and its CI covers two replicas; it is an alternative to evaluate in the rehearsal, not the documented path.

1. **Stop Kanidm.** Suspend Flux, then scale the replica group to zero. The JSON patch changes only the count; a merge patch on `replicaGroups` would replace the whole list and drop its other fields. At zero replicas Kaniop turns replication off and deletes the replicas' certificate Secrets.

   ```sh
   flux suspend kustomization kanidm -n identity
   kubectl -n identity patch kanidm kanidm --type json \
     -p '[{"op":"replace","path":"/spec/replicaGroups/0/replicas","value":0}]'
   kubectl -n identity wait --for=delete pod -l app.kubernetes.io/name=kanidm --timeout=180s
   ```

2. **Choose the backup.** List replica 0's online backups with a busybox pod; the server image has no shell. Pick the newest file from before the damage. Use a file written by replica 0, so the restored database keeps replica 0's identity.

   ```sh
   kubectl -n identity run kanidm-list --rm -it --restart=Never --image=busybox:1.37 \
     --overrides='{"spec":{"securityContext":{"runAsUser":389,"runAsGroup":389},"containers":[{"name":"x","image":"busybox:1.37","stdin":true,"tty":true,"command":["ls","-l","/data"],"volumeMounts":[{"name":"d","mountPath":"/data"}]}],"volumes":[{"name":"d","persistentVolumeClaim":{"claimName":"kanidm-data-kanidm-default-0"}}]}}'
   ```

   If the backup you need is older than the seven on the claim, first restore replica 0's claim from a Longhorn backup ([Restore one claim](longhorn-backup-restore.md#restore-one-claim)), then continue here.

3. **Restore replica 0.** The pod exits when the restore completes; its log must end without an error.

   ```sh
   image=kanidm/server:1.11.2   # the version that wrote the backup
   file=/data/backup-<timestamp>.json.gz
   kubectl -n identity run kanidm-restore --rm -i --restart=Never --image="$image" \
     --overrides='{"spec":{"securityContext":{"runAsUser":389,"runAsGroup":389,"fsGroup":389},
       "containers":[{"name":"x","image":"'"$image"'",
         "command":["kanidmd","database","restore","'"$file"'"],
         "env":[{"name":"KANIDM_DOMAIN","value":"auth.home.kelch.io"},
                {"name":"KANIDM_ORIGIN","value":"https://auth.home.kelch.io"},
                {"name":"KANIDM_DB_PATH","value":"/data/kanidm.db"},
                {"name":"KANIDM_TLS_CHAIN","value":"/etc/kanidm/tls/tls.crt"},
                {"name":"KANIDM_TLS_KEY","value":"/etc/kanidm/tls/tls.key"}],
         "volumeMounts":[{"name":"d","mountPath":"/data"},{"name":"tls","mountPath":"/etc/kanidm/tls","readOnly":true}]}],
       "volumes":[{"name":"d","persistentVolumeClaim":{"claimName":"kanidm-data-kanidm-default-0"}},
                  {"name":"tls","secret":{"secretName":"auth-home-kelch-io-tls"}}]}}'
   ```

4. **Discard replicas 1 and 2.** Deleting their claims deletes their volumes, including their own online backups; their Longhorn backups remain.

   ```sh
   kubectl -n identity delete pvc kanidm-data-kanidm-default-1 kanidm-data-kanidm-default-2
   kubectl -n identity get pvc   # until both are gone
   ```

5. **Start replica 0 alone and check it.** With one replica, replication stays off.

   ```sh
   kubectl -n identity patch kanidm kanidm --type json \
     -p '[{"op":"replace","path":"/spec/replicaGroups/0/replicas","value":1}]'
   kubectl -n identity get pods -l app.kubernetes.io/name=kanidm -w   # until kanidm-default-0 is Ready
   ```

   Log in as `idm_admin` with the password in the `kanidm-admin-passwords` Secret, and confirm that a change made after the backup is gone. If the login fails because the admin password changed after the backup, delete that Secret so Kaniop issues a new one, as its restore controller does.

6. **Reseed replica 1, then replica 2, keeping Flux suspended.** An empty replica asks every partner for a refresh and takes the first answer, and readiness does not wait for the refresh to finish ([source](https://github.com/kanidm/kanidm/blob/v1.11.2/server/core/src/repl/mod.rs#L451-L475)). Going straight to three replicas could let replica 2 reload from replica 1 before replica 1 holds the restored data. Add one replica at a time, so the only partners a new replica can reach already hold it. Kaniop registers each new replica's certificate and restarts the StatefulSet.

   ```sh
   kubectl -n identity patch kanidm kanidm --type json \
     -p '[{"op":"replace","path":"/spec/replicaGroups/0/replicas","value":2}]'
   kubectl -n identity get pods -l app.kubernetes.io/name=kanidm -w   # until kanidm-default-1 is Ready
   kubectl -n identity logs kanidm-default-1 -c kanidm | grep -i refresh
   ```

   Continue only when replica 1 has logged a completed refresh and shows the restored state: a change made after the backup is absent when you query that pod directly, for example through `kubectl -n identity port-forward pod/kanidm-default-1 8443` with the `kanidm` CLI pointed at `https://localhost:8443` and hostname verification skipped. Then set `"value":3`, check replica 2 the same way, and resume Flux, which leaves the group at the declared three:

   ```sh
   flux resume kustomization kanidm -n identity
   ```

7. Complete [Verification](#verification).

## Replica claim lost

- **Replica 1 or 2.** Delete the claim and the pod; the StatefulSet recreates both, and the empty replica reloads from replica 0. *Intended.*
- **Replica 0.** Restore its claim from a Longhorn backup with [Restore one claim](longhorn-backup-restore.md#restore-one-claim), scaling the group through the patch above. A backup within Kanidm's seven-day changelog window catches up from replicas 1 and 2 by normal replication. An older one is refused with a refresh-required error, because replica 0's links have no `automatic_refresh`. Then, while both partners are healthy, refresh replica 0 from them; the `-c` path is the configuration Kaniop renders, as its own certificate commands use:

  ```sh
  kubectl -n identity exec kanidm-default-0 -c kanidm -- \
    kanidmd refresh-replication-consumer -c /run/kanidm/server.toml --i-want-to-refresh-this-servers-database
  ```

  *Intended.*

## All claims lost

In a cluster rebuild, Flux starts Kanidm on three new, empty claims. Restore replica 0 only and reseed the others. *Intended, unrehearsed.*

1. Stop Kanidm as in step 1 above.
2. Delete all three claims. Restore `kanidm-data-kanidm-default-0` from its Longhorn backup with [Restore one claim](longhorn-backup-restore.md#restore-one-claim) steps 1 and 3, using the backup volume that recorded `kanidm-data-kanidm-default-0` and the newest backup from before the outage.
3. Start replica 0 alone and check it, as in step 5 above. The volume backup is crash-consistent; SQLite recovers its journal at startup. If the server reports database integrity errors, scale back to zero and restore one of the online backups on that volume as in steps 2 and 3.
4. Reseed replicas 1 and 2 and verify, as in steps 6 and 7.

If the NAS is gone too, the Longhorn store comes back from B2 first; see the [disaster-recovery runbook](longhorn-backup-restore.md#disaster-recovery-rebuild-the-cluster-and-restore-data). Restoring a Kanidm volume's backups from B2 was drilled outside Longhorn on 2026-10-05.

## Verification

- [ ] `kanidm-default-0`, `-1` and `-2` Running, each `1/1` Ready
- [ ] `kubectl -n identity get kanidm kanidm` shows `Available=True`
- [ ] Replicas 1 and 2 log a completed refresh from replica 0 and then normal replication, with no refresh-required or domain-mismatch errors
- [ ] Web UI loads at `https://auth.home.kelch.io`
- [ ] Login as `idm_admin` succeeds with the password in the `kanidm-admin-passwords` Secret
- [ ] A change made after the chosen backup is absent through each replica
- [ ] `KanidmGroup arr-admins` and `KanidmPersonAccount kelchm` reconciled (visible in web UI). Flux reapplies the Kaniop-managed people, groups and OAuth2 clients, so those reflect Git rather than the backup.
- [ ] OAuth2 client secrets: if the restore rolled any back, Kaniop writes them to the `<client>-kanidm-oauth2-credentials` Secrets in the consumer namespaces; restart consumers that read them only at startup

## Restore drill

Nothing on this page has been rehearsed against three replicas. Verified so far is only what the sources say: the backup path and format, the version rule, the offline lock, the required variables, how replicas catch up and refresh, and the steps Kaniop's restore controller takes. Whether the sequence above restores the cluster, and whether replication would have undone the old single-replica procedure, are inferences until a rehearsal on a disposable copy. The rehearsal is part of the recovery drill in [#224](https://github.com/kelchm/home-lab/issues/224).

Rehearse again after every Kanidm minor upgrade (1.11 → 1.12 and so on). Restore refuses a backup from another minor version, and the backup format can change between them.
