#!/usr/bin/env bash
# Post-reboot gates for one node. The gates are unchanged: Talos API answers at
# the target version, the node is Ready, Multus has republished its CNI conf, an
# instance-manager is Running and Ready on lhnet1, every Longhorn volume is
# healthy, and every node and instance-manager cluster-wide is Ready.
#
# Output is summarised. The volume watcher reports counts, trends, and names that
# changed state rather than reprinting every unhealthy volume each poll, and dumps
# full detail on a periodic snapshot and on timeout. Set VERBOSE=1 for raw tables.
set -Eeuo pipefail

usage() {
    echo "usage: $0 <k8s-prod-1|k8s-prod-2|k8s-prod-3|node-ip>" >&2
    exit 2
}

case "${1:-}" in
    k8s-prod-1|10.32.30.11) node_name=k8s-prod-1; node_ip=10.32.30.11 ;;
    k8s-prod-2|10.32.30.12) node_name=k8s-prod-2; node_ip=10.32.30.12 ;;
    k8s-prod-3|10.32.30.13) node_name=k8s-prod-3; node_ip=10.32.30.13 ;;
    *) usage ;;
esac

ROOT_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT_DIR"

if command -v mise >/dev/null 2>&1 \
    && { [[ -z "${KUBECONFIG:-}" || ! -f "${KUBECONFIG}" ]] \
      || [[ -z "${TALOSCONFIG:-}" || ! -f "${TALOSCONFIG}" ]]; }; then
    eval "$(mise env -s bash)"
fi

for bin in jq kubectl talosctl yq; do
    command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
done

readonly timeout_secs="${VERIFY_TIMEOUT_SECS:-1800}"
readonly poll_secs="${VERIFY_POLL_SECS:-15}"
readonly detail_secs="${VERIFY_DETAIL_SECS:-120}"
readonly VERBOSE="${VERBOSE:-}"
readonly deadline=$(( $(date +%s) + timeout_secs ))

# jq variables, not shell ones
# shellcheck disable=SC2016
readonly INSTANCE_MANAGER_FILTER='
  .items[]
  | (.status.containerStatuses // []) as $containers
  | (.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // "[]") as $raw
  | ($raw | fromjson? // []) as $networks
  | select(.status.phase != "Running"
      or ($containers | length) == 0
      or (all($containers[]; .ready == true) | not)
      or ([$networks[]
        | select(.name == "longhorn-system/storage-network"
            and .interface == "lhnet1"
            and any(.ips[]?; startswith("10.32.25.")))] | length) == 0)
'

section() { printf '\n== %s ==\n' "$1"; }
line()    { printf '  %s\n' "$1"; }
detail()  { [[ -n "$VERBOSE" ]] && printf '%s\n' "$1" | sed 's/^/    /'; return 0; }

elapsed_since() { printf '%dm%02ds' $(( ($1) / 60 )) $(( ($1) % 60 )); }

# names present in $2 but not in $1, space separated
new_names() {
    local -a old new
    read -ra old <<<"$1"
    read -ra new <<<"$2"
    local haystack=" ${old[*]} " n
    for n in "${new[@]}"; do
        [[ "$haystack" == *" $n "* ]] || printf '%s ' "$n"
    done
}

lh_setting() {
    kubectl -n longhorn-system get settings.longhorn.io "$1" -o json 2>/dev/null \
        | jq -r '.value // .spec.value // ""' || echo ''
}

# --- Talos API + version ------------------------------------------------------
section "Talos API on $node_name"
until talos_version="$(talosctl -n "$node_ip" version --short 2>/dev/null)"; do
    (( $(date +%s) < deadline )) || { echo "timed out waiting for Talos API on $node_name" >&2; exit 1; }
    line "waiting for Talos API on $node_name..."
    sleep "$poll_secs"
done

expected_version="$(yq '.talosVersion' talos/talenv.yaml)"
server_version="$(awk '
    /^Server:/ { server=1; next }
    server && /^[[:space:]]*Tag:/ { print $2; exit }
    server && /Talos v/ { print $2; exit }
' <<<"$talos_version")"
line "server version ${server_version:-unknown} (target $expected_version)"
detail "$talos_version"
if [[ "$server_version" != "$expected_version" ]]; then
    printf 'server version %s does not match target %s\n' "${server_version:-unknown}" "$expected_version" >&2
    printf '%s\n' "$talos_version" | sed 's/^/    /' >&2
    exit 1
fi

section "Kubernetes node readiness"
kubectl wait --for=condition=Ready "node/$node_name" --timeout="${timeout_secs}s"

section "Multus CNI conf"
talosctl -n "$node_ip" read /etc/cni/net.d/00-multus.conf >/dev/null
line 'lhnet1 attachments are only possible once this file is back; present'

# --- instance-manager on this node --------------------------------------------
section "instance-manager on $node_name"
im_reported=0
while true; do
    instance_json="$(kubectl -n longhorn-system get pods \
        -l longhorn.io/component=instance-manager \
        --field-selector "spec.nodeName=$node_name" -o json)"
    im_total="$(jq '.items | length' <<<"$instance_json")"
    im_bad="$(jq "[ $INSTANCE_MANAGER_FILTER ] | length" <<<"$instance_json")"
    if (( im_total > 0 && im_bad == 0 )); then
        break
    fi
    if (( im_reported == 0 )); then
        line "waiting for a Running, Ready instance-manager with lhnet1 (found ${im_total}, ${im_bad} not yet attached)..."
        im_reported=1
    fi
    if ! (( $(date +%s) < deadline )); then
        echo "timed out waiting for a healthy instance-manager attachment on $node_name" >&2
        jq -r '.items[] | [.metadata.name, .status.phase, ((.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // "none"))] | @tsv' <<<"$instance_json" >&2
        exit 1
    fi
    sleep "$poll_secs"
done
jq -r '.items[] | "  \(.metadata.name)  Running and Ready on lhnet1"' <<<"$instance_json"

# --- Longhorn volumes ---------------------------------------------------------
section "Longhorn volume recovery"
rebuild_limit="$(lh_setting concurrent-replica-rebuild-per-node-limit)"
replenish_wait="$(lh_setting replica-replenishment-wait-interval)"
line "Longhorn rebuilds at most ${rebuild_limit:-5} replicas per node concurrently and waits ${replenish_wait:-600}s before replenishing a missing replica."
line 'A quiet stretch shorter than that interval is normal. Suspect a stall only when it is exceeded with no active rebuilds.'

watch_start="$(date +%s)"
last_change="$watch_start"
last_detail=0
last_names=''
first_count=-1

while true; do
    now="$(date +%s)"
    volume_json="$(kubectl -n longhorn-system get volumes.longhorn.io -o json)"
    volume_count="$(jq '.items | length' <<<"$volume_json")"
    bad_volumes="$(jq -r '.items[] | select(.status.robustness != "healthy") | [.metadata.name, (.status.state // "unknown"), (.status.robustness // "unknown")] | @tsv' <<<"$volume_json")"
    bad_count="$(grep -c . <<<"${bad_volumes:-}" || true)"
    names="$(cut -f1 <<<"${bad_volumes:-}" | sort | tr '\n' ' ' | sed 's/  */ /g;s/^ //;s/ $//')"

    if (( volume_count == 0 )); then
        line 'no Longhorn volumes found yet'
    elif (( bad_count == 0 )); then
        line "all ${volume_count} volumes healthy after $(elapsed_since $(( now - watch_start )))"
        break
    fi

    (( first_count < 0 )) && first_count="$bad_count"

    if [[ "$names" != "$last_names" ]]; then
        recovered="$(new_names "$names" "$last_names")"
        degraded="$(new_names "$last_names" "$names")"
        [[ -n "$recovered" ]] && line "recovered: ${recovered% }"
        [[ -n "$degraded" ]] && line "newly unhealthy: ${degraded% }"
        line "unhealthy ${bad_count}/${volume_count} (started at ${first_count}, $(elapsed_since $(( now - watch_start ))) elapsed)"
        last_change="$now"
        last_names="$names"
    fi

    if (( now - last_detail >= detail_secs )); then
        engine_json="$(kubectl -n longhorn-system get engines.longhorn.io -o json 2>/dev/null || echo '{"items":[]}')"
        rebuilds="$(jq '[.items[] | (.status.rebuildStatus // {}) | to_entries[]] | length' <<<"$engine_json")"
        max_progress="$(jq '[.items[] | (.status.rebuildStatus // {}) | to_entries[] | (.value.progress // 0)] | max // 0' <<<"$engine_json")"
        line "--- snapshot at $(elapsed_since $(( now - watch_start ))) ---"
        line "unhealthy ${bad_count}/${volume_count}; active replica rebuilds ${rebuilds} (max progress ${max_progress}%); last state change $(elapsed_since $(( now - last_change ))) ago"
        printf '%s\n' "$bad_volumes" | sed 's/^/    /'
        if (( rebuilds == 0 && now - last_change > ${replenish_wait:-600} )); then
            line "no rebuild activity and no state change for longer than the ${replenish_wait:-600}s replenishment interval — investigate before waiting further"
        fi
        last_detail="$now"
    fi

    if ! (( now < deadline )); then
        echo "timed out waiting for Longhorn volumes; ${bad_count} still unhealthy:" >&2
        printf '%s\n' "$bad_volumes" >&2
        exit 1
    fi
    sleep "$poll_secs"
done

# --- cluster-wide gates -------------------------------------------------------
section "cluster-wide"
node_json="$(kubectl get nodes -o json)"
node_total="$(jq '.items | length' <<<"$node_json")"
not_ready="$(jq -r '.items[] | select(any(.status.conditions[]?; .type == "Ready" and .status == "True") | not) | .metadata.name' <<<"$node_json")"
if [[ -n "$not_ready" ]]; then
    printf 'nodes not Ready:\n%s\n' "$not_ready" >&2
    kubectl get nodes -o wide >&2
    exit 1
fi
line "nodes Ready: ${node_total}/${node_total}"

instance_json="$(kubectl -n longhorn-system get pods -l longhorn.io/component=instance-manager -o json)"
instance_count="$(jq '.items | length' <<<"$instance_json")"
bad_instances="$(jq -r "$INSTANCE_MANAGER_FILTER | [.metadata.name, .spec.nodeName, (.status.phase // \"unknown\")] | @tsv" <<<"$instance_json")"
if (( instance_count == 0 )); then
    echo 'no Longhorn instance-manager pods found' >&2
    exit 1
fi
if [[ -n "$bad_instances" ]]; then
    printf 'instance-managers not Ready or missing storage-network/lhnet1:\n%s\n' "$bad_instances" >&2
    exit 1
fi
line "instance-managers Ready with lhnet1: ${instance_count}/${instance_count}"

cordoned="$(jq -r '.items[] | select(.spec.unschedulable == true) | .metadata.name' <<<"$node_json")"
if [[ -n "$cordoned" ]]; then
    printf 'nodes still cordoned: %s\n' "$(tr '\n' ' ' <<<"$cordoned")" >&2
    exit 1
fi

detail "$(kubectl get nodes -o wide)"
detail "$(kubectl -n tailscale get pods -o wide)"

printf '\nNODE CHECKS PASSED: %s is Ready on %s, all %s nodes are Ready and uncordoned, all %s instance-managers are Ready with lhnet1, and all %s Longhorn volumes are healthy.\n' \
    "$node_name" "$server_version" "$node_total" "$instance_count" "$volume_count"
printf 'Run talosctl health on a healthy control-plane node, then re-check the access path and CloudNativePG placement before selecting the next candidate.\n'
