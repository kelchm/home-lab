# Visionect: Synology to k8s-prod

Cold-cutover runbook for moving Visionect Software Suite (VSS) from Athena to
the `iot` namespace. A few minutes of device disconnect time is acceptable;
keeping the procedure simple and rollback-safe is more valuable than logical
replication for this one-time move.

## Captured source state (2026-08-22)

| Item | Source |
|---|---|
| VSS | `visionect/visionect-server-v3:7.6.5`, immutable digest `sha256:1c8de9…8415` |
| PostgreSQL | 17.2, database/user `koala`/`visionect`, about 2 GiB |
| Database rows | 9 devices, about 2.89M `device_events`, about 3.55M `status` rows |
| Redis | 7.4.2, 12 expiring cache keys; intentionally not migrated |
| Device endpoint | `vss.home.kelch.io:11113`; 9 established clients from VLAN 90 at inventory time |
| UI | `http://vss.home.kelch.io:8081` becomes `https://vss.home.kelch.io` |
| File state | `config.json` is durable and load-bearing; `ac_apps`, `certs`, and `devices` were empty; custom fonts contained only `.uuid`; rotated VSS logs use 1.6 GiB |
| Source container | `vss` on `kelchm@athena.home.kelch.io` |

`config.json` contains the deployment key, global settings, and renderer
settings in addition to database credentials. It must be copied and only its
`Storage` connection fields changed. PostgreSQL contains the device inventory,
users, settings, sessions, and event/status history.

One stale session uses the obsolete Synology Newsprint URL
`http://vss.home.kelch.io:8090`. Newsprint is superseded by the green-field
Broadsheet rewrite, so the migration intentionally does not preserve port 8090
or add NAS egress. Repoint or remove that stale session after cutover. Four
active sessions use the private Broadsheet route, which is admitted explicitly
by the VSS Cilium policy.

## Target and invariants

- VSS stays at 7.6.5 for the migration. Do not combine a host move with an app
  upgrade.
- CNPG stays on PostgreSQL 17 (`visionect-db`, one instance, 30 GiB Longhorn).
- `vss.home.kelch.io` resolves to `10.32.150.30`, a `shared-prod` BGP VIP.
- TCP/11113 remains unchanged for devices. HTTPS/443 terminates in the
  unprivileged NGINX sidecar and proxies to VSS on pod-local 8081.
- All six source file volumes are retained. Application data uses the 5 GiB
  `visionect` Longhorn PVC and the 1.6 GiB rotated log set uses the separate
  4 GiB `visionect-logs` PVC.
- The VSS image runs as UID 0 but with no Linux capabilities, no privilege
  escalation, RuntimeDefault seccomp, no host mounts, and no `/dev/fuse`.
- The Synology database and containers remain intact and stopped after
  cutover. They are the immediate rollback target; do not delete them during
  this change.
- Never run both VSS instances against client traffic at once.

### Why VSS does not get privileged/FUSE access

Visionect 2.8 introduced a FUSE-backed cookie filesystem and the official
installation command still carries its old `--privileged`, `SYS_ADMIN`,
`MKNOD`, and `/dev/fuse` arguments. The exact 7.6.5 image contains no FUSE
package, helper, library reference, device access, mount action, or supervisor
service; its engine and migrations use PostgreSQL-backed cookie storage.

A disposable instance restored from the full production database ran all four
services (`admin`, `engine`, `gateway`, `networkmanager`) with all capabilities
dropped and no FUSE device. This is strong static and runtime evidence, but
Visionect has not formally documented the reduced permission set as supported.

References: [2.8 release notes](https://docs.visionect.com/TechnicalDocumentation/ReleaseNotes/VisionectSoftwareSuite.html#release-2-8),
[5.2 release notes](https://docs.visionect.com/TechnicalDocumentation/ReleaseNotes/VisionectSoftwareSuite.html#release-5-2),
[installation guide](https://docs.visionect.com/VisionectSoftwareSuite/Installation.html).

## Phase 1: stage without starting VSS

The staging PR leaves `controllers.visionect.replicas: 0`. After it merges:

1. Apply the updated `network/unifi/frr.conf` to the gateway so `TALOS-IN`
   accepts exact `/32`s from `10.32.150.0/24`. Read back the live config; do not
   rely on the repo file alone.
2. Create the three ordered UniFi rules documented under
   `network/unifi/README.md#shared-prod-tenant-rules`:
   IoT→`.30`:11113 allow, Main→`.30`:443 allow, then `.30` all-other drop.
3. Reconcile and wait for the staged target:

   ```sh
   flux reconcile kustomization cilium-bgp -n kube-system --with-source
   flux reconcile kustomization visionect -n iot --with-source

   kubectl -n iot wait --for=condition=Ready certificate/vss-home-kelch-io \
     --timeout=5m
   kubectl -n iot wait --for=condition=Ready cluster/visionect-db \
     --timeout=10m
   kubectl -n iot get deploy/visionect svc/vss \
     pvc/visionect pvc/visionect-logs \
     certificate/vss-home-kelch-io cluster/visionect-db
   ```

4. These checks must pass before maintenance begins:

   ```sh
   # Deployment exists but has no pod and cannot touch the empty target DB.
   test "$(kubectl -n iot get deploy visionect \
     -o jsonpath='{.spec.replicas}')" = 0

   test "$(kubectl -n iot get svc vss \
     -o jsonpath='{.status.loadBalancer.ingress[0].ip}')" = 10.32.150.30

   # k8s-gateway is already ready to answer after the UniFi override is removed.
   dig +short @10.32.130.2 vss.home.kelch.io A

   # On the UniFi gateway: the live inbound filter must admit shared-prod.
   # With externalTrafficPolicy=Local and zero endpoints, absence of the .30
   # route is expected until the VSS pod starts.
   vtysh -c 'show ip prefix-list TALOS-IN'
   ```

Prepare a second, reviewed cutover commit that changes only `replicas: 0` to
`replicas: 1`, but do not merge it yet.

## Phase 2: live pre-cutover backup

Take a logical backup while the source remains online. This is the safety copy;
the authoritative transfer is repeated after VSS stops.

On Athena, create a root-only directory under
`/volume1/docker/dockerge/visionect/migration/<timestamp>/` and capture:

```sh
sudo /var/packages/ContainerManager/target/usr/bin/docker exec pdb \
  sh -c 'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --format=custom --no-owner --no-acl' \
  > koala-precutover.dump

sudo /var/packages/ContainerManager/target/usr/bin/docker cp \
  vss:/opt/visionect/vss/config/. files/config
sha256sum koala-precutover.dump files/config/config.json > SHA256SUMS
```

The redirection must run from a root shell in that root-only directory. Confirm
the dump with `pg_restore --list`; do not print `config.json` because it contains
the deployment key and database password.

Record a source fingerprint for later comparison:

```sql
SELECT 'device', count(*) FROM device
UNION ALL SELECT 'device_events', count(*) FROM device_events
UNION ALL SELECT 'session', count(*) FROM session
UNION ALL SELECT 'settings', count(*) FROM settings
UNION ALL SELECT 'status', count(*) FROM status
ORDER BY 1;
```

## Phase 3: cold cutover

Create a mode-0700 local staging directory with `mktemp -d` and record its exact
path. Then:

```sh
visionect_cutover_dir=$(mktemp -d /tmp/visionect-cutover.XXXXXX)
chmod 700 "$visionect_cutover_dir"
printf '%s\n' "$visionect_cutover_dir"
```

1. Stop only VSS. Leave PostgreSQL and Redis running:

   ```sh
   ssh kelchm@athena.home.kelch.io \
     'sudo /var/packages/ContainerManager/target/usr/bin/docker stop --time 60 vss'
   nc -zvw2 vss.home.kelch.io 11113 && false || true
   ```

2. Take the final consistent dump and copy every declared durable file volume
   from the stopped container. `docker cp ... -` emits a tar stream and works
   for stopped containers:

   ```sh
   umask 077
   ssh kelchm@athena.home.kelch.io \
     'sudo /var/packages/ContainerManager/target/usr/bin/docker exec pdb \
       sh -c '\''pg_dump --username "$POSTGRES_USER" \
       --dbname "$POSTGRES_DB" --format=custom --no-owner --no-acl'\''' \
     > "$visionect_cutover_dir/koala-final.dump"

   while IFS='|' read -r archive source; do
     ssh kelchm@athena.home.kelch.io \
       "sudo /var/packages/ContainerManager/target/usr/bin/docker cp \
         vss:${source}/. -" \
       > "$visionect_cutover_dir/${archive}.tar"
   done <<'EOF'
   ac_apps|/opt/visionect/vss/ac_apps
   certs|/opt/visionect/vss/certs
   config|/opt/visionect/vss/config
   devices|/opt/visionect/vss/devices
   customfonts|/usr/share/fonts/truetype/customfonts
   logs|/var/log/vss
   EOF

   sha256sum "$visionect_cutover_dir"/*.dump \
     "$visionect_cutover_dir"/*.tar \
     > "$visionect_cutover_dir/SHA256SUMS"
   ```

3. Restore PostgreSQL while the target Deployment remains at zero:

   ```sh
   kubectl -n iot exec -i visionect-db-1 -- \
     pg_restore --username postgres --dbname koala --role visionect \
       --clean --if-exists --no-owner --no-acl --exit-on-error \
     < "$visionect_cutover_dir/koala-final.dump"
   ```

4. Mount the target data PVC using the manual helper:

   ```sh
   kubectl apply -f tools/visionect-migration/data-import-pod.yaml
   kubectl -n iot wait --for=condition=Ready pod/visionect-data-import \
     --timeout=5m
   kubectl -n iot exec visionect-data-import -- \
     mkdir -p /data/ac_apps /data/certs /data/config /data/devices \
       /data/customfonts /logs
   ```

5. Extract each copied volume into its matching `/data` directory. For example:

   ```sh
   for area in ac_apps certs config devices customfonts; do
     kubectl -n iot exec -i visionect-data-import -- \
       tar -C "/data/$area" -xf - \
       < "$visionect_cutover_dir/$area.tar"
   done

   kubectl -n iot exec -i visionect-data-import -- \
     tar -C /logs -xf - < "$visionect_cutover_dir/logs.tar"
   ```

6. Rewrite only the database connection fields in `config.json`. The port is a
   JSON number; all other connection values are strings. These commands keep
   the generated password out of terminal output and Git:

   ```sh
   db_host=$(kubectl -n iot get secret visionect-db-app \
     -o jsonpath='{.data.host}' | base64 --decode)
   db_port=$(kubectl -n iot get secret visionect-db-app \
     -o jsonpath='{.data.port}' | base64 --decode)
   db_user=$(kubectl -n iot get secret visionect-db-app \
     -o jsonpath='{.data.username}' | base64 --decode)
   db_password=$(kubectl -n iot get secret visionect-db-app \
     -o jsonpath='{.data.password}' | base64 --decode)
   db_name=$(kubectl -n iot get secret visionect-db-app \
     -o jsonpath='{.data.dbname}' | base64 --decode)

   tar -xOf "$visionect_cutover_dir/config.tar" ./config.json \
     | jq --arg host "$db_host" \
          --argjson port "$db_port" \
          --arg user "$db_user" \
          --arg password "$db_password" \
          --arg database "$db_name" \
          '.Storage.Host = $host
           | .Storage.Port = $port
           | .Storage.Username = $user
           | .Storage.Password = $password
           | .Storage.Database = $database' \
     > "$visionect_cutover_dir/config.target.json"

   tar -xOf "$visionect_cutover_dir/config.tar" ./config.json \
     | jq --sort-keys 'del(.Storage)' \
     > "$visionect_cutover_dir/config.source.nonstorage.json"
   jq --sort-keys 'del(.Storage)' \
     "$visionect_cutover_dir/config.target.json" \
     > "$visionect_cutover_dir/config.target.nonstorage.json"
   cmp "$visionect_cutover_dir/config.source.nonstorage.json" \
     "$visionect_cutover_dir/config.target.nonstorage.json"

   kubectl -n iot exec -i visionect-data-import -- \
     sh -c 'umask 077; cat > /data/config/config.json' \
     < "$visionect_cutover_dir/config.target.json"
   unset db_host db_port db_user db_password db_name
   ```
7. Remove the helper pod so the RWO claim is free:

   ```sh
   kubectl delete -f tools/visionect-migration/data-import-pod.yaml
   ```

8. Run the fingerprint query against `visionect-db-1`; all five counts must
   match the stopped source. Also confirm the source and target contain the same
   `schema_migrations` value.
9. Merge the prepared `replicas: 1` cutover commit and reconcile. Wait for all
   three containers:

   ```sh
   flux reconcile kustomization visionect -n iot --with-source
   kubectl -n iot rollout status deployment/visionect --timeout=10m
   kubectl -n iot get pod -l app.kubernetes.io/name=visionect
   ```

10. Validate the target before changing DNS:

    ```sh
    # The Local-policy Service begins advertising only after this endpoint is
    # Ready. Confirm the gateway installed the new /32.
    vtysh -c 'show ip route 10.32.150.30'
    curl --fail --resolve vss.home.kelch.io:443:10.32.150.30 \
      https://vss.home.kelch.io/
    kubectl -n iot exec deploy/visionect -c app -- supervisorctl status
    ```

11. Delete the UniFi local-DNS override
    `vss.home.kelch.io CNAME athena.home.kelch.io`. Do not create a manual A
    record: k8s-gateway derives `vss → 10.32.150.30` from the Service at TTL 1.
12. Verify normal resolution, HTTPS, all nine TCP connections from VLAN 90,
    device online state, and visible output for the supported session origins:
    `demo.visionect.com` and `broadsheet.home.kelch.io`. The obsolete Newsprint
    session is allowed to fail and can be removed or repointed afterward.

## Rollback

The source database is unchanged after the source VSS stops. If any target gate
fails:

1. Suspend the target HelmRelease and scale its Deployment to zero so there is
   no chance of two active gateways:

   ```sh
   flux suspend helmrelease visionect -n iot
   kubectl -n iot scale deployment/visionect --replicas=0
   ```

2. Restore the UniFi `vss CNAME athena` override and confirm it resolves to
   `10.32.10.5`.
3. Start the old container:

   ```sh
   ssh kelchm@athena.home.kelch.io \
     'sudo /var/packages/ContainerManager/target/usr/bin/docker start vss'
   ```

4. Confirm port 11113, all nine device connections, the UI on 8081, and display
   output. Revert the cutover commit before resuming Flux reconciliation.

Do not delete or overwrite the target database during rollback; it is evidence
for diagnosing the failed cutover.

## Post-cutover

- Trigger and verify an immediate Longhorn backup for both `visionect` and
  `visionect-db-1`, then confirm they joined the default daily/weekly jobs.
- Keep the stopped Synology VSS, PostgreSQL, Redis, anonymous volumes, and final
  dump for at least seven days and through one successful restore drill.
- Remove the sensitive local `mktemp` directory only after verifying the NAS
  final dump and Longhorn backups. Delete the exact recorded path, never a broad
  glob or unresolved variable.
- Record the final row counts, device reconnect duration, route/firewall
  read-back, and rollback-retirement date in this runbook.
- Upgrade VSS 7.6.5 and PostgreSQL 17 only in later, independent changes.
