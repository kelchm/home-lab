#!/usr/bin/env bash
# Rebuild a CloudNativePG standby whose physical slot was invalidated by the WAL
# cap, on a throwaway kind cluster with synthetic data, using the pinned chart
# and the given Cluster's image and cap. Proves both paths of "A lost standby" in
# docs/runbooks/cnpg-backup-restore.md. Run it after a CloudNativePG or PostgreSQL
# major change. It never reads the repository's kubeconfig.
#
#   mise exec aqua:kubernetes-sigs/kind@0.33.0 github:cloudnative-pg/cloudnative-pg@1.30.1 -- \
#     scripts/cnpg/lost-slot-drill.sh kubernetes/apps/iot/visionect/app/cluster.yaml
#
# DOCKER_CONTEXT selects the daemon. PG_IMAGE replaces the image, for example with
# the digest production is running. WINDOW_MISS_ATTEMPTS bounds the forced race.
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
manifest="${1:?usage: lost-slot-drill.sh <cluster.yaml>}"
image="${PG_IMAGE:-$(yq '.spec.imageName' "${manifest}")}"
cap="$(yq '.spec.postgresql.parameters.max_slot_wal_keep_size' "${manifest}")"
if [[ ! "${cap}" =~ ^[1-9][0-9]*GB$ ]]; then
    printf 'ERROR: max_slot_wal_keep_size must be a positive integer in GB, got %s\n' "${cap}" >&2
    exit 1
fi
chart="${root}/kubernetes/apps/cnpg-system/cnpg-operator/app/ocirepository.yaml"
segments=$((${cap%GB} * 1024 / 16 + 64))
attempts="${WINDOW_MISS_ATTEMPTS:-8}"

# A per-run name keeps a failed launch from cleaning up another drill's cluster.
name="cnpg-lost-slot-$$-${RANDOM}-${RANDOM}"
ns=drill
cluster=drill-db
for command in docker helm kind kubectl kubectl-cnpg yq; do
    command -v "${command}" >/dev/null || {
        printf 'ERROR: required command not found: %s\n' "${command}" >&2
        exit 1
    }
done
if kind get clusters | grep -qx "${name}"; then
    printf 'ERROR: kind cluster %s already exists\n' "${name}" >&2
    exit 1
fi

work="$(mktemp -d)"
kubeconfig="${work}/kubeconfig"
cleanup() {
    kind delete cluster --name "${name}" --kubeconfig "${kubeconfig}" >/dev/null 2>&1 || true
    rm -rf "${work}"
}
trap cleanup EXIT
trap 'printf "FAIL at line %s: %s\n" "${LINENO}" "${BASH_COMMAND}" >&2' ERR

say() { printf '\n== %s\n' "$*"; }
k() { kubectl --kubeconfig "${kubeconfig}" --context "kind-${name}" "$@"; }
plugin() { kubectl-cnpg --kubeconfig "${kubeconfig}" --context "kind-${name}" -n "${ns}" "$@"; }
sql() {
    local pod="$1"
    shift
    k -n "${ns}" exec -i "${pod}" -c postgres -- psql -X -qAt -v ON_ERROR_STOP=1 -d drill "$@"
}
expect() {
    if [[ "$2" != "$3" ]]; then
        printf 'FAIL %s: got [%s], want [%s]\n' "$1" "$2" "$3" >&2
        exit 1
    fi
    printf 'pass %s\n' "$1"
}
eventually() {
    local seconds="$1" elapsed=0
    shift
    until "$@"; do
        ((elapsed < seconds)) || return 1
        sleep 2
        elapsed=$((elapsed + 2))
    done
}

cluster_field() { k -n "${ns}" get cluster "${cluster}" -o "jsonpath={$1}"; }
slot_field() { sql "${primary}" -c "SELECT coalesce(($1)::text, '-') FROM pg_replication_slots WHERE slot_name = '${slot}'"; }
identity() { sql "$1" -c "SELECT system_identifier, timeline_id, pg_postmaster_start_time() FROM pg_control_system(), pg_control_checkpoint()"; }
row_hash() { sql "$1" -c "SELECT count(*), md5(string_agg(id || ':' || v, ',' ORDER BY id)) FROM invariant"; }
primary_pod() { k -n "${ns}" get pod "${primary}" -o 'jsonpath={.metadata.uid} {.status.containerStatuses[0].restartCount}'; }
slot_inactive() { [[ "$(slot_field active)" == false ]]; }
slot_fresh() { [[ "$(slot_field 'invalidation_reason IS NULL')" == true ]]; }
pod_uid() { k -n "${ns}" get pod "${standby}" -o 'jsonpath={.metadata.uid}' 2>/dev/null; }
replaced() {
    [[ "$(pod_uid)" != "$1" ]] &&
        [[ "$(k -n "${ns}" get pod "${standby}" -o 'jsonpath={.status.containerStatuses[0].ready}' 2>/dev/null)" == true ]]
}
name_returned() { [[ "$(cluster_field .status.instanceNames)" == *"\"${standby}\""* ]]; }
streaming() {
    [[ "$(sql "${primary}" -c "SELECT state FROM pg_stat_replication WHERE application_name = '${standby}'")" == streaming ]]
}
# The runbook's gate, verbatim: only a confirmed, inactive, invalidated slot on a primary.
drop_slot() {
    sql "${primary}" -c "SELECT count(pg_drop_replication_slot(slot_name)) FROM pg_replication_slots WHERE slot_name = '${slot}' AND slot_type = 'physical' AND NOT active AND wal_status = 'lost' AND invalidation_reason = 'wal_removed' AND NOT pg_is_in_recovery();"
}

lose_slot() {
    expect 'gate refuses the active slot' "$(drop_slot)" 0
    plugin fencing on "${cluster}" "${standby}"
    eventually 60 slot_inactive
    expect 'gate refuses an inactive slot that is not lost' "$(drop_slot)" 0
    sql "${primary}" >/dev/null <<SQL
SELECT 'INSERT INTO churn (t) VALUES (now()); SELECT pg_switch_wal();' FROM generate_series(1, ${segments}) \gexec
CHECKPOINT;
SQL
    expect 'cap invalidated the slot' "$(slot_field invalidation_reason)/$(slot_field wal_status)" wal_removed/lost
    plugin fencing off "${cluster}" "${standby}"
    sleep 15
    streaming && exit 1
    expect 'Cluster retains two Ready instances without streaming' "$(cluster_field .status.readyInstances)" 2
}

verify() {
    local marker="$1" lsn
    expect 'current primary' "$(cluster_field .status.currentPrimary)/$(cluster_field .status.targetPrimary)" "${primary}/${primary}"
    expect 'primary pod and restarts' "$(primary_pod)" "${base_pod}"
    expect 'primary identity and timeline' "$(identity "${primary}")" "${base_identity}"
    expect 'primary rows' "$(row_hash "${primary}")" "${base_hash}"
    eventually 120 streaming
    sql "${primary}" -c "INSERT INTO post VALUES (${marker})"
    lsn="$(sql "${primary}" -c 'SELECT pg_current_wal_lsn()')"
    caught_up() {
        [[ "$(sql "${primary}" -c "SELECT replay_lsn >= '${lsn}' FROM pg_stat_replication WHERE application_name = '${standby}'")" == t ]]
    }
    eventually 30 caught_up
    expect 'standby in recovery' "$(sql "${standby}" -c 'SELECT pg_is_in_recovery()')" t
    expect 'standby rows' "$(row_hash "${standby}")" "${base_hash}"
    expect 'standby marker' "$(sql "${standby}" -c "SELECT count(*) FROM post WHERE id = ${marker}")" 1
    expect 'standby receiver' "$(sql "${standby}" -c 'SELECT status, slot_name FROM pg_stat_wal_receiver')" "streaming|${slot}"
    expect 'slot active and valid' "$(slot_field active)/$(slot_field invalidation_reason)" true/-
    expect 'slot within the cap' "$(slot_field "wal_status IN ('reserved', 'extended') AND safe_wal_size IS NOT NULL")" true
    expect 'WAL cap' "$(sql "${primary}" -c 'SHOW max_slot_wal_keep_size')" "${cap}"
}

partition() {
    local action="$1" node ip
    node="$(k -n "${ns}" get pod "${primary}" -o 'jsonpath={.spec.nodeName}')"
    ip="$(k -n "${ns}" get pod "${primary}" -o 'jsonpath={.status.podIP}')"
    docker exec "${node}" iptables "${action}" FORWARD -s "${ip}" -p tcp --dport 6443 -j REJECT --reject-with tcp-reset
    docker exec "${node}" iptables "${action}" FORWARD -d "${ip}" -p tcp --sport 6443 -j REJECT --reject-with tcp-reset
}

say "Disposable cluster: ${image}, cap ${cap}"
kind create cluster --name "${name}" --kubeconfig "${kubeconfig}" --wait 180s --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
  - role: worker
EOF
k wait --for=condition=Ready nodes --all --timeout=180s
helm install cnpg "$(yq '.spec.url' "${chart}"):$(yq '.spec.ref.tag' "${chart}")@$(yq '.spec.ref.digest' "${chart}")" \
    --kubeconfig "${kubeconfig}" --kube-context "kind-${name}" -n cnpg-system --create-namespace \
    --set monitoring.podMonitorEnabled=false --wait --timeout 240s >/dev/null
operator="$(k -n cnpg-system get deploy -o 'jsonpath={.items[0].spec.template.spec.containers[0].image}')"
kubectl-cnpg version | grep -q "Version:${operator##*:} " || {
    printf 'ERROR: kubectl-cnpg does not match operator %s\n' "${operator}" >&2
    exit 1
}
k create namespace "${ns}"
k apply -f - <<EOF
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: ${cluster}
  namespace: ${ns}
spec:
  instances: 2
  primaryUpdateMethod: switchover
  affinity:
    podAntiAffinityType: required
  imageName: ${image}
  storage:
    size: $((${cap%GB} + 6))Gi
  postgresql:
    parameters:
      max_slot_wal_keep_size: ${cap}
  bootstrap:
    initdb:
      database: drill
      owner: drill
EOF
k -n "${ns}" wait "cluster/${cluster}" --for=condition=Ready --timeout=300s

primary="$(cluster_field .status.currentPrimary)"
standby="${cluster}-2"
[[ "${primary}" != "${standby}" ]]
slot="_cnpg_${standby//-/_}"
sql "${primary}" <<'SQL'
CREATE TABLE invariant AS SELECT g AS id, md5(g::text) AS v FROM generate_series(1, 100000) g;
ALTER TABLE invariant ADD PRIMARY KEY (id);
CREATE TABLE churn (id bigserial PRIMARY KEY, t timestamptz);
CREATE TABLE post (id int PRIMARY KEY);
SQL
base_pod="$(primary_pod)"
base_identity="$(identity "${primary}")"
base_hash="$(row_hash "${primary}")"
eventually 60 streaming

say 'Stock path: lost slot, destroy, same instance and slot name'
lose_slot
uid="$(pod_uid)"
plugin destroy "${cluster}" "${standby}"
eventually 300 replaced "${uid}"
verify 1

say "Forced window miss: reset the primary's API traffic across destroy (up to ${attempts} attempts)"
reason=-
for ((attempt = 1; attempt <= attempts; attempt++)); do
    say "Attempt ${attempt}"
    lose_slot
    uid="$(pod_uid)"
    partition -I
    plugin destroy "${cluster}" "${standby}"
    # Best-effort timing; only the observed surviving slot below proves a miss.
    eventually 60 name_returned
    sleep 2
    partition -D
    eventually 300 replaced "${uid}"
    sleep 15
    expect 'primary survived the partition' "$(primary_pod)" "${base_pod}"
    reason="$(slot_field invalidation_reason)"
    [[ "${reason}" == wal_removed ]] && break
done
if [[ "${reason}" != wal_removed ]]; then
    printf 'INCONCLUSIVE: the invalidated slot never survived a rebuild, repair not exercised\n' >&2
    exit 2
fi
printf 'invalidated slot survived rebuild: active=%s wal_status=%s\n' "$(slot_field active)" "$(slot_field wal_status)"

say 'Repair: fence, gated drop, reconcile, unfence'
plugin fencing on "${cluster}" "${standby}"
eventually 60 slot_inactive
expect 'gate dropped exactly the confirmed slot' "$(drop_slot)" 1
plugin reload "${cluster}"
eventually 60 slot_fresh
plugin fencing off "${cluster}" "${standby}"
verify 2

say "Passed: ${image}"
