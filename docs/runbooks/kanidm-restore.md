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

**Drilled** on 2026-11-08 in a disposable three-replica cluster, not in `identity`; see [Restore drill](#restore-drill-2026-11-08). Steps 1–6 ran as written apart from the namespace, domain and TLS Secret. The Flux suspend and resume commands were not exercised, because Flux does not manage that cluster. It follows the sequence Kaniop's own `KanidmRestore` controller runs ([documentation](https://github.com/pando85/kaniop/blob/v0.21.0/Documentation/src/usage/backup-restore.md)). That controller is marked experimental and its CI covers two replicas; it remains an unevaluated alternative, not the documented path.

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

   Log in as `idm_admin` with the password in the `kanidm-admin-passwords` Secret, and confirm that a change made after the backup is gone. If the login fails because the admin password changed after the backup, delete that Secret so Kaniop issues a new one, as its restore controller does. Kaniop resets both admin passwords on replica 0 with `kanidmd recover-account`; in the drill the new Secret appeared within 3 seconds.

   ```sh
   kubectl -n identity delete secret kanidm-admin-passwords
   kubectl -n identity get secret kanidm-admin-passwords   # until it exists again
   ```

6. **Reseed replica 1, then replica 2, keeping Flux suspended.** An empty replica asks every partner for a refresh and takes the first answer, and readiness does not wait for the refresh to finish ([source](https://github.com/kanidm/kanidm/blob/v1.11.2/server/core/src/repl/mod.rs#L451-L475)). Going straight to three replicas could let replica 2 reload from replica 1 before replica 1 holds the restored data. Add one replica at a time, so the only partners a new replica can reach already hold it. Kaniop registers each new replica's certificate and restarts the StatefulSet.

   In the drill each scale-up took Kaniop two or three rolling restarts, including replica 0, over 1.5–2.5 minutes. Pods are briefly Ready between restarts, so `-w` alone ends too early. Wait until the custom resource reports every replica `ready` and no pod has been recreated for a minute, and only then read the logs: `kubectl logs` shows only the current pod. Until a new replica's refresh completes, every replica logs replication errors about mismatched `domain_uuid`s and an out-of-date consumer. Those are expected; they must stop once the refresh is logged.

   ```sh
   kubectl -n identity get kanidm kanidm -o jsonpath='{range .status.replicaStatuses[*]}{.podName}={.state}{"\n"}{end}'
   kubectl -n identity get pods -l app.kubernetes.io/name=kanidm   # AGE stable for a minute
   ```

   ```sh
   kubectl -n identity patch kanidm kanidm --type json \
     -p '[{"op":"replace","path":"/spec/replicaGroups/0/replicas","value":2}]'
   kubectl -n identity get pods -l app.kubernetes.io/name=kanidm -w   # until kanidm-default-1 is Ready
   kubectl -n identity logs kanidm-default-1 -c kanidm | grep -i refresh
   ```

   Continue only when replica 1 has logged `replication consumer refresh complete!` and shows the restored state: a change made after the backup is absent when you query that pod directly, for example through `kubectl -n identity port-forward pod/kanidm-default-1 8443` with the `kanidm` CLI pointed at `https://localhost:8443` and hostname verification skipped. Then add replica 2 and check it the same way:

   ```sh
   kubectl -n identity patch kanidm kanidm --type json \
     -p '[{"op":"replace","path":"/spec/replicaGroups/0/replicas","value":3}]'
   kubectl -n identity get pods -l app.kubernetes.io/name=kanidm -w   # until kanidm-default-2 is Ready
   kubectl -n identity logs kanidm-default-2 -c kanidm | grep -i refresh
   ```

   Once replica 2 holds the restored state too, resume Flux, which leaves the group at the declared three:

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

## Restore drill, 2026-11-08

[Restore the cluster to a backup](#restore-the-cluster-to-a-backup) passed against a disposable three-replica Kaniop cluster in namespace `drill-kanidm-20261108`, running `kanidm/server:1.11.2` under the production Kaniop 0.21.0 ([#224](https://github.com/kelchm/home-lab/issues/224)). The source was production replica 0's online backup `backup-2026-11-07T05:00:00.574593642Z.json.gz`, copied out of a Longhorn restore of that replica's volume; no production pod or claim was touched.

**Setup.** The backup was restored once with the same commands and grown to three replicas. Then a group `drill-224-marker` was created on replica 0 and confirmed on all three, standing in for a change made after the backup.

| Step | Time (UTC) | Result |
|---|---|---|
| 1. Scale to zero | 00:36:56–00:38:01 | Pods gone; Kaniop deleted all three `replica-cert` Secrets |
| 2. List backups | 00:38:19 | Listed the staged file |
| 3. Restore replica 0 | 00:38:40–00:38:55 | `Restore Success!`, 323 entries reindexed |
| 4. Delete claims 1 and 2 | 00:38:59–00:39:00 | Claims and Longhorn volumes gone |
| 5. One replica | 00:39:03–00:40:17 | Old admin password refused; Secret deleted and reissued; marker absent; 35 groups, 1 person |
| 6a. Two replicas | 00:40:25–00:43:00 | Replica 1 refreshed from replica 0; marker absent |
| 6b. Three replicas | 00:43:10–00:44:46 | Replica 2 refreshed from a restored partner; marker absent on all three |

From scale-down to a verified three-replica cluster took 7 minutes 50 seconds. In the following two hours each replica ran about 960 incremental replication cycles with no errors and no restarts. The custom resource reported `Available=True` with every replica `ready`, and `/status` and `/ui/login` answered 200.

**What the drill showed:**

- A restored database keeps the backup's domain name. `kanidmd` logs `Using domain name from the database auth.home.kelch.io` and serves that domain whatever `KANIDM_DOMAIN` says. It refuses to start unless the origin's host is that domain or a subdomain of it ([source](https://github.com/kanidm/kanidm/blob/v1.11.2/server/lib/src/idm/server.rs#L174-L195)). A disposable copy therefore needs an origin under `auth.home.kelch.io`; the drill used `https://drill.auth.home.kelch.io` with a self-signed certificate.
- An empty replica starts as a fresh database with its own domain UUID. Replica 0, whose links have no `automatic_refresh`, refused to consume from it, as intended. The new replica refreshed itself on its first replication cycle.
- Kaniop restarted the StatefulSet two or three times per scale-up; see step 6.

**Isolating a disposable copy.** Kaniop 0.21.0 watches `Kanidm` resources in every namespace and keeps what it creates in the resource's own namespace: the StatefulSet, Services, Secrets and replication hosts `<pod>.kanidm.<namespace>.svc.cluster.local`. Production Kanidm admits replication on 8444 only from `identity` pods, and the drill namespace had a CiliumNetworkPolicy that allowed traffic only inside the namespace and to DNS. The restored copy still carries production's domain UUID, replica 0's server UUID and its replication key until Kaniop renews the certificate, so do not loosen either policy. Give the copy's `volumeClaimTemplate` the labels `recurring-job.longhorn.io/source: enabled` and `recurring-job-group.longhorn.io/no-backup: enabled`, so it is never backed up.

**Side effect: alerts.** Kaniop tries to reach every Kanidm it manages for domain appearance and mail sender reconciliation, even when neither is configured. Its egress policy blocks namespaces other than `identity`, so while the drill cluster existed `KaniopReconcileFailures`, `KaniopK8sApiErrors`, `KaniopReconcileDurationHigh` and `CiliumPolicyDrops` fired as warnings. Expect them during a repeat, or plan a bounded silence.

**Not exercised:** the restore in `identity`, the Flux suspend and resume commands, Kaniop's reconciliation of people, groups and OAuth2 clients against a restored cluster, consumer OAuth2 Secrets, the web UI through Traefik, [Replica claim lost](#replica-claim-lost) and [All claims lost](#all-claims-lost).

Rehearse again after every Kanidm minor upgrade (1.11 → 1.12 and so on). Restore refuses a backup from another minor version, and the backup format can change between them.
