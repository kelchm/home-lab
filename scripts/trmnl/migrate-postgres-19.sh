#!/usr/bin/env bash
# Restore one stopped TRMNL trial onto its new PostgreSQL 19 claim.
# shellcheck disable=SC2016 # Container commands expand their own environment.

set -euo pipefail

if [[ $# != 1 ]]; then
  echo "Usage: $0 terminus|byos-next" >&2
  exit 2
fi
app=$1
case "$app" in
  terminus) source_claim=terminus-database ;;
  byos-next) source_claim=byos-next ;;
  *) echo "Unknown TRMNL trial: $app" >&2; exit 2 ;;
esac

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
kube=(kubectl --namespace trmnl-trials)
target_claim="$app-pg19"
source_pod="$app-pg18-transfer"
target_pod="$app-pg19-transfer"
candidate="$repo/kubernetes/apps/trmnl-trials/app/$app.yaml"
target_image=$(yq -er '.spec.values.controllers.main.containers.database.image | .repository + ":" + .tag' "$candidate")

deployment=$("${kube[@]}" get deployment "$app" -o json)
"${kube[@]}" get helmrelease "$app" -o json | jq -e '
  .spec.suspend == true and
  (any(.status.conditions[]?; .type == "Reconciling" and .status == "True") | not)
' > /dev/null || {
  echo "Suspend the $app HelmRelease and let reconciliation finish first" >&2
  exit 1
}
jq -e --arg claim "$source_claim" '
  .spec.replicas == 0 and (.status.replicas // 0) == 0 and
  any(.spec.template.spec.volumes[]; .name == "database" and .persistentVolumeClaim.claimName == $claim) and
  any(.spec.template.spec.containers[];
    .name == "database" and (.image | startswith("docker.io/library/postgres:18.")))
' <<< "$deployment" > /dev/null || {
  echo "Expected $app to be at zero replicas with its original PostgreSQL 18 claim" >&2
  exit 1
}

for claim in "$source_claim" "$target_claim"; do
  "${kube[@]}" get pvc "$claim" -o json | jq -e '
    .status.phase == "Bound" and .spec.storageClassName == "longhorn"
  ' > /dev/null
done
"${kube[@]}" get pod -o json | jq -e --arg old "$source_claim" --arg new "$target_claim" '
  all(.items[];
    all(.spec.volumes[]?;
      .persistentVolumeClaim.claimName != $old and .persistentVolumeClaim.claimName != $new))
' > /dev/null || {
  echo "Wait until no Pods mount either database claim" >&2
  exit 1
}

created_pods=()
cleanup() {
  if (( ${#created_pods[@]} )); then
    "${kube[@]}" delete pod "${created_pods[@]}" --wait --timeout=120s || exit 1
  fi
}
trap cleanup EXIT

create_pod() {
  local pod=$1 claim=$2 role=$3
  jq -c --arg pod "$pod" --arg claim "$claim" --arg role "$role" --arg target_image "$target_image" '
    .spec.template.spec as $pod_spec |
    ($pod_spec.containers[] | select(.name == "database")) as $db |
    {
      apiVersion: "v1", kind: "Pod", metadata: {name: $pod},
      spec: {
        restartPolicy: "Never",
        automountServiceAccountToken: false,
        securityContext: $pod_spec.securityContext,
        volumes: [{name: "database", persistentVolumeClaim: {claimName: $claim}}],
        containers: [{
          name: "database",
          image: (if $role == "source" then $db.image else $target_image end),
          securityContext: $db.securityContext,
          resources: $db.resources,
          volumeMounts: $db.volumeMounts,
          env: (
            [$db.env[] | select(.name == "PGDATA")] +
            (["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"] | map({
              name: ., valueFrom: {secretKeyRef: {name: $db.envFrom[0].secretRef.name, key: .}}
            }))
          ),
          command: ["/bin/sh", "-ec"],
          args: [
            (if $role == "source" then
              "test \"$(cat \"$PGDATA/PG_VERSION\")\" = 18"
            else
              "test ! -d \"$PGDATA\" || test -z \"$(ls -A \"$PGDATA\")\""
            end) +
            " || { echo Invalid PGDATA on migration claim >&2; exit 1; }; " +
            "exec docker-entrypoint.sh postgres -c listen_addresses="
          ],
          readinessProbe: {
            exec: {command: ["/bin/sh", "-ec",
              "test \"$(cat /proc/1/comm)\" = postgres && pg_isready -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\""]},
            periodSeconds: 2,
            timeoutSeconds: 3
          }
        }]
      }
    }
  ' <<< "$deployment" | "${kube[@]}" create -f -
  created_pods+=("$pod")
}

dump_roles() {
  "${kube[@]}" exec -i "$source_pod" -- sh -se <<'SH'
set -o pipefail
export PGOPTIONS="-c default_transaction_read_only=on"
pg_dumpall --roles-only --quote-all-identifiers -U "$POSTGRES_USER" -l "$POSTGRES_DB" |
  awk -v name="$POSTGRES_USER" '
    BEGIN {
      gsub(/"/, "\"\"", name)
      create_bootstrap_role = "CREATE ROLE \"" name "\";"
    }
    # initdb already created this role. Preserve its ALTER ROLE and grants.
    $0 == create_bootstrap_role { skipped++; next }
    { print }
    END { if (skipped != 1) exit 1 }
  '
SH
}

fingerprint() {
  "${kube[@]}" exec -i "$1" -- sh -ec '
    export PGOPTIONS="-c default_transaction_read_only=on"
    exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
  ' <<'SQL'
SELECT format(
  'SELECT %L, %L, count(*), md5(coalesce(string_agg(value, ''|'' ORDER BY value COLLATE "C"), ''''))
   FROM (SELECT to_jsonb(t)::text AS value FROM %I.%I t) rows',
  'table', schemaname || '.' || tablename, schemaname, tablename
)
FROM pg_tables
WHERE schemaname NOT IN ('information_schema', 'pg_catalog')
ORDER BY schemaname, tablename
\gexec
SELECT 'sequence', schemaname || '.' || sequencename, last_value FROM pg_sequences
  WHERE schemaname NOT IN ('information_schema', 'pg_catalog') ORDER BY schemaname, sequencename;
SELECT 'extension', extname FROM pg_extension ORDER BY extname;
SELECT 'role', rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin,
       rolreplication, rolbypassrls, rolconnlimit, rolvaliduntil, md5(rolpassword)
FROM pg_authid WHERE left(rolname, 3) <> 'pg_' ORDER BY rolname;
SELECT 'membership', pg_get_userbyid(roleid), pg_get_userbyid(member), pg_get_userbyid(grantor),
       admin_option, inherit_option, set_option
FROM pg_auth_members
WHERE left(pg_get_userbyid(roleid), 3) <> 'pg_' OR left(pg_get_userbyid(member), 3) <> 'pg_'
ORDER BY 2, 3, 4;
SQL
}

create_pod "$source_pod" "$source_claim" source
create_pod "$target_pod" "$target_claim" target
"${kube[@]}" wait --for=condition=Ready "pod/$source_pod" "pod/$target_pod" --timeout=180s

# Restore cluster-wide roles before the database dump applies ownership and grants.
# Keep the role dump, which contains password verifiers, off disk and out of logs.
if ! dump_roles |
  "${kube[@]}" exec -i "$target_pod" -- sh -ec '
    export PGOPTIONS="-c log_statement=none -c log_min_error_statement=panic"
    exec psql -Xq -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f - >/dev/null 2>&1
  '; then
  echo "$app role restore failed. The PostgreSQL 19 claim is not marked ready." >&2
  exit 1
fi

"${kube[@]}" exec "$source_pod" -- sh -ec '
  export PGOPTIONS="-c default_transaction_read_only=on"
  exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --lock-wait-timeout=5s
' | "${kube[@]}" exec -i "$target_pod" -- sh -ec '
  exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error --single-transaction
'

source_fingerprint=$(fingerprint "$source_pod")
target_fingerprint=$(fingerprint "$target_pod")
if [[ "$source_fingerprint" != *'table|'* || "$source_fingerprint" != "$target_fingerprint" ]]; then
  echo "$app fingerprints differ. The PostgreSQL 19 claim is not marked ready." >&2
  exit 1
fi
"${kube[@]}" exec "$target_pod" -- sh -ec '
  test "$(cat "$PGDATA/PG_VERSION")" = 19
  touch "$PGDATA/.trmnl-pg19-restored"
'
printf '%s: table contents, sequences, extensions and roles match on %s\n' "$app" "$target_claim"
