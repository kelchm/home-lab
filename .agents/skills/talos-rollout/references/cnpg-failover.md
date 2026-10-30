# Moving a CloudNativePG primary off a rollout candidate

The desired topology for Visionect and Bambuddy is two asynchronous instances on distinct nodes. Live HA acceptance is tracked in [#782](https://github.com/kelchm/home-lab/pull/782); read the actual topology before choosing a maintenance path. A singleton's primary is its only PostgreSQL instance, so draining its node is blocked by its PodDisruptionBudget. Never force past that budget.

For an accepted two-instance cluster, leave CNPG's PDB protection enabled. Require a healthy streaming standby on a different, non-candidate node, zero observed replay lag, healthy archiving and all Longhorn volumes healthy. CNPG [switches over ahead of a primary-node drain](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/docs/src/kubernetes_upgrade.md#pod-disruption-budgets). Watch the ordinary drain and verify the new primary, write-service endpoint, application recovery and replication afterwards. Do not temporarily scale an already-HA cluster back to one instance. If the standby is unavailable, stop and restore redundancy before proceeding.

The temporary-topology procedure below is for a **singleton** only. It moves a primary deliberately. **It does not decide anything.** Whether a given database is worth a temporary replica, or whether its downtime is acceptable, is the operator's judgment and stays outside any script. See [the permanent HA rollout and rollback](../../../../docs/runbooks/cnpg-backup-restore.md#two-instance-ha-rollout) for #782.

## Prerequisites

Find the clusters and where their primaries actually run. `preflight.sh <candidate>` prints this, and flags any singleton primary sitting on the candidate. Directly:

```bash
kubectl get clusters.postgresql.cnpg.io -A \
  -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,INSTANCES:.spec.instances,PRIMARY:.status.currentPrimary,PHASE:.status.phase'
kubectl -n <ns> get pod <primary> -o jsonpath='{.spec.nodeName}{"\n"}'
```

Check that WAL archiving is healthy before any step that stops a database pod: `ContinuousArchiving` is `True` on every Cluster and `CNPGWALArchivingStalled` is not firing. PostgreSQL waits for its archiver during shutdown, so while the backup store on Athena is unreachable a stop that normally takes seconds took 8.5 minutes in a test, and CloudNativePG allows it up to 30. See [cnpg-backup-restore](../../../../docs/runbooks/cnpg-backup-restore.md#wal-archiving-is-stalled).

Promotion needs the `kubectl-cnpg` client matched to the running operator. The client ships from the operator's own release, and a mismatched one can write fields the running operator does not act on. Read the version off the cluster rather than assuming the chart's `appVersion` — Renovate bumps the chart:

```bash
kubectl -n cnpg-system get deploy -l app.kubernetes.io/name=cloudnative-pg \
  -o jsonpath='{.items[0].spec.template.spec.containers[0].image}{"\n"}'
```

There is no aqua package for it, so pull that exact tag through mise's github backend. Nothing is installed globally and nothing is pinned in `.mise.toml`, which would drift from the operator:

```bash
mise x 'github:cloudnative-pg/cloudnative-pg[exe=kubectl-cnpg]@<version>' -- kubectl-cnpg version
```

Every `kubectl-cnpg` command below assumes that prefix.

## Step 0 — make the availability decision explicitly

State which branch you are taking and why before touching anything.

**Accept downtime.** Only after confirming a restorable backup exists for that database. Then remove the budget for that one cluster — `spec.enablePDB: false` deletes the PodDisruptionBudget and permits eviction — subject to the Flux handling below, and set it back to `true` afterwards. Do not reach for `--force` or `--disable-eviction`: those bypass Longhorn's drain policy for every workload on the node, not just this one.

**Preserve availability.** Continue with the steps below. Budget roughly 15–30 minutes per database plus the Longhorn rebuild time for the new replica's volume.

## Step 1 — record the original state

Write it somewhere outside the repository and keep it open until the rollout ends:

```bash
kubectl -n <ns> get cluster <name> -o yaml > <record-path>/<name>-before.yaml
kubectl -n <ns> get cluster <name> \
  -o jsonpath='{.spec.instances}{"\t"}{.status.currentPrimary}{"\t"}{.status.targetPrimary}{"\n"}'
```

You are restoring to this, not to whatever looks right later.

## Step 2 — keep Flux from reverting the temporary topology

A live `kubectl patch` of `spec.instances` is owned by Flux, and the next reconciliation of the Kustomization that manages that cluster reverts it. This is not hypothetical: during the v1.13.9 rollout an instances patch was reverted while the rollout was still active, after the controllers had themselves moved to another node and resumed reconciling.

Identify the owning Kustomization, then choose one of two paths:

```bash
flux get kustomizations -A | grep <app>
```

- **Suspend only that Kustomization** when the temporary topology lives and dies inside this session. `flux suspend kustomization <name> -n flux-system`. Suspend the single relevant Kustomization, never `flux-system` as a whole — that would freeze every unrelated app for the length of the rollout.
- **Stage a temporary Git change** to `spec.instances` when the topology must survive longer than the session, or when you want the change auditable. Flux then reconciles the temporary state instead of fighting it.

Either way, write down what you changed. A suspension you forget is a cluster that silently stops reconciling. `preflight.sh` lists every suspended Kustomization for exactly this reason, and the rollout is not complete while one is outstanding.

## Step 3 — add a temporary replica

```bash
kubectl -n <ns> patch cluster <name> --type=merge -p '{"spec":{"instances":2}}'
kubectl -n <ns> get pods -l cnpg.io/cluster=<name> -o wide -w
```

The new instance clones from the primary and joins as a streaming standby. It also provisions a new Longhorn volume of the same size, which rebuilds replicas across nodes.

## Step 4 — confirm the replica did not land on the candidate

CloudNativePG's anti-affinity is a preference, not a requirement, so verify placement instead of assuming it:

```bash
kubectl -n <ns> get pods -l cnpg.io/cluster=<name> -o wide
```

If the new instance landed on the candidate it is useless for this purpose. Scale back to 1, clean up per Step 9, and retry with a temporary `spec.affinity.nodeSelector` excluding the candidate.

Then let the new volume settle. Longhorn must be fully healthy again before the drain starts — the same gate that applies between nodes applies here, because a rebuilding replica is the storage risk this rollout is built to avoid stacking.

## Step 5 — wait for zero replay lag

Ask PostgreSQL directly. The Cluster status is a summary; `pg_stat_replication` on the primary is the fact:

```bash
kubectl -n <ns> exec <primary-pod> -c postgres -- psql -qAt -c \
  "select application_name, state, sent_lsn, replay_lsn, sent_lsn - replay_lsn as lag_bytes from pg_stat_replication;"
```

Require `state = streaming` and `lag_bytes = 0` for the new instance. Promoting a standby that is still replaying loses the difference.

## Step 6 — promote

```bash
mise x 'github:cloudnative-pg/cloudnative-pg[exe=kubectl-cnpg]@<version>' -- \
  kubectl-cnpg promote <cluster> <new-instance-pod> -n <ns>
```

## Step 7 — verify the switchover directly

`.status.currentPrimary` records the operator's intent. Confirm the databases agree.

The new primary is out of recovery and can assign a transaction ID:

```bash
kubectl -n <ns> exec <new-primary-pod> -c postgres -- psql -qAt -c "select pg_is_in_recovery();"   # expect f
kubectl -n <ns> exec <new-primary-pod> -c postgres -- psql -qAt -c "select txid_current();"        # expect a number
```

The old primary is now a standby streaming from it:

```bash
kubectl -n <ns> exec <old-primary-pod> -c postgres -- psql -qAt -c "select pg_is_in_recovery();"   # expect t
kubectl -n <ns> exec <new-primary-pod> -c postgres -- psql -qAt -c \
  "select application_name, state from pg_stat_replication;"                                       # old primary, streaming
```

And the write service points at the new primary, which is what the application actually resolves:

```bash
kubectl -n <ns> get endpoints "$(kubectl -n <ns> get cluster <name> -o jsonpath='{.status.writeService}')" -o wide
```

Only now is the candidate free of this database. Return to the rollout.

## Step 8 — roll the node

Back to `SKILL.md`. Do not start the next database's move until this node has passed every post-node check.

## Step 9 — restore the original topology

Reverse Step 3 by the same route you used in Step 2 — patch back if you suspended, or revert the staged Git change:

```bash
kubectl -n <ns> patch cluster <name> --type=merge -p '{"spec":{"instances":1}}'
```

The primary stays wherever it is now. That is fine and expected; the instance numbering will not match the recorded "before" state, and forcing it back is another unnecessary switchover. Record which instance ended up primary.

## Step 10 — check for leftovers

Scaling down does not always reclaim everything. Check all three, and remove what remains deliberately:

```bash
kubectl -n <ns> get pvc -l cnpg.io/cluster=<name>
kubectl -n longhorn-system get volumes.longhorn.io
kubectl -n <ns> exec <primary-pod> -c postgres -- psql -qAt -c \
  "select slot_name, active from pg_replication_slots;"
```

An orphaned PVC quietly consumes Longhorn capacity. An orphaned **inactive** replication slot is worse: it pins WAL indefinitely and will fill the primary's volume.

## Step 11 — resume Flux and confirm convergence

```bash
flux resume kustomization <name> -n flux-system
flux reconcile kustomization <name> -n flux-system --with-source
kubectl -n <ns> get cluster <name> -o jsonpath='{.spec.instances}{"\n"}'
```

The live value must match Git. Re-run `preflight.sh` and confirm it reports no suspended Kustomizations.

## Stop conditions

- Replay lag will not reach zero, or the standby leaves `streaming`.
- The promoted instance still reports `pg_is_in_recovery() = t`.
- The old primary does not come back as a streaming standby.
- Longhorn does not return to fully healthy after the new replica's volume is created.

Any of these means the database is in a worse position than when you started. Restore the recorded topology before rolling anything.
