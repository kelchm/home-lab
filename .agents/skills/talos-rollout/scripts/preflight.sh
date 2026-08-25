#!/usr/bin/env bash
# Collect rollout facts and enforce mechanical invariants. Prints a concise
# summary per check and expands raw detail only for checks that fail. Set
# VERBOSE=1 to always print the raw tables.
#
# This script decides nothing. Database strategy, recovery, and go/no-go stay
# with the operator.
set -Eeuo pipefail

usage() {
    echo "usage: $0 [k8s-prod-1|k8s-prod-2|k8s-prod-3|node-ip]" >&2
    echo "  the optional candidate makes the access-path and CloudNativePG checks candidate-aware" >&2
    exit 2
}

candidate=''
case "${1:-}" in
    '') ;;
    k8s-prod-1|10.32.30.11) candidate=k8s-prod-1 ;;
    k8s-prod-2|10.32.30.12) candidate=k8s-prod-2 ;;
    k8s-prod-3|10.32.30.13) candidate=k8s-prod-3 ;;
    *) usage ;;
esac

ROOT_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT_DIR"

if command -v mise >/dev/null 2>&1 \
    && { [[ -z "${KUBECONFIG:-}" || ! -f "${KUBECONFIG}" ]] \
      || [[ -z "${TALOSCONFIG:-}" || ! -f "${TALOSCONFIG}" ]]; }; then
    eval "$(mise env -s bash)"
fi

for bin in jq kubectl talosctl; do
    command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
done

readonly NODES=(k8s-prod-1 k8s-prod-2 k8s-prod-3)
readonly IPS=(10.32.30.11 10.32.30.12 10.32.30.13)
readonly API_ENDPOINT=10.32.30.8
readonly VERBOSE="${VERBOSE:-}"

FAILURES=()

section() { printf '\n== %s ==\n' "$1"; }
line()    { printf '  %s\n' "$1"; }
note()    { printf '  note: %s\n' "$1"; }
detail()  { [[ -n "$VERBOSE" ]] && printf '%s\n' "$1" | sed 's/^/    /'; return 0; }

# fail <headline> [raw-detail]
fail() {
    FAILURES+=("$1")
    printf '  FAIL: %s\n' "$1" >&2
    if [[ -n "${2:-}" ]]; then
        printf '%s\n' "$2" | sed 's/^/    /' >&2
    fi
}

section "context"
line "kubeconfig:  ${KUBECONFIG:-<unset>}"
line "talosconfig: ${TALOSCONFIG:-<unset>}"
line "candidate:   ${candidate:-<none given>}"

# --- operator access path -----------------------------------------------------
# The destination address does not tell you which link you actually use. Ask the
# host routing table which interface carries each rollout destination, then show
# who serves that path so the candidate can be judged against it.
route_iface() {
    local dest="$1"
    case "$(uname -s)" in
        Darwin) route -n get "$dest" 2>/dev/null | awk '/interface:/ { print $2; exit }' ;;
        Linux)  ip -o route get "$dest" 2>/dev/null | awk '{ for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit } }' ;;
        *)      echo '' ;;
    esac
}

iface_kind() {
    case "$1" in
        '')                  echo 'no route' ;;
        utun*|tailscale*|ts*) echo 'tailnet' ;;
        *)                   echo 'direct' ;;
    esac
}

section "operator access path"
via_tailnet=0
for dest in "$API_ENDPOINT" "${IPS[@]}"; do
    iface="$(route_iface "$dest")"
    kind="$(iface_kind "$iface")"
    [[ "$kind" == 'tailnet' ]] && via_tailnet=1
    label='talos'
    [[ "$dest" == "$API_ENDPOINT" ]] && label='kube-API'
    line "$(printf '%-9s %-13s %-10s %s' "$label" "$dest" "${iface:-none}" "$kind")"
    [[ "$kind" == 'no route' ]] && fail "no route to $dest from this workstation"
done

if command -v tailscale >/dev/null 2>&1; then
    ts_json="$(tailscale status --json 2>/dev/null || echo '{}')"
    ts_state="$(jq -r '.BackendState // "unknown"' <<<"$ts_json")"
    line "tailscale backend: $ts_state"
    ts_routers="$(jq -r '
      (.Peer // {}) | to_entries[] | .value
      | select((.PrimaryRoutes // []) | length > 0)
      | "\(.HostName)  \((.PrimaryRoutes // []) | join(","))"
    ' <<<"$ts_json")"
    if [[ -n "$ts_routers" ]]; then
        printf '%s\n' "$ts_routers" | sed 's/^/  route peer: /'
    elif (( via_tailnet )); then
        fail 'a rollout destination routes over the tailnet but no peer advertises a primary route'
    fi
elif (( via_tailnet )); then
    note 'destinations route over a tunnel interface but the tailscale CLI is unavailable to name the peer'
fi

ts_pods_json="$(kubectl -n tailscale get pods -o json 2>/dev/null || echo '{"items":[]}')"
ts_pods="$(jq -r '.items[] | "\(.metadata.name)\t\(.spec.nodeName // "unscheduled")\t\(.status.phase // "unknown")"' <<<"$ts_pods_json")"
if [[ -n "$ts_pods" ]]; then
    printf '%s\n' "$ts_pods" | sed 's/\t/  /g;s/^/  tailscale pod: /'
fi
if [[ -n "$candidate" ]]; then
    on_candidate="$(jq -r --arg n "$candidate" '.items[] | select(.spec.nodeName == $n) | .metadata.name' <<<"$ts_pods_json")"
    if [[ -n "$on_candidate" ]]; then
        note "candidate $candidate hosts tailscale pods: $(tr '\n' ' ' <<<"$on_candidate")"
        if (( via_tailnet )); then
            note 'your API/Talos path runs over the tailnet, so evicting these resets it — pre-move the router or expect a transient reset'
        fi
    else
        line "candidate $candidate hosts no tailscale pods"
    fi
fi

connector_ready="$(kubectl get connectors.tailscale.com lan-subnet-router -o json 2>/dev/null \
    | jq -r '[.status.conditions[]? | select(.type == "ConnectorReady")] | (.[0].status // "unknown")' || echo 'unknown')"
line "connector lan-subnet-router ConnectorReady=$connector_ready"
[[ "$connector_ready" == 'True' ]] || fail "tailscale connector is not Ready (ConnectorReady=$connector_ready)"

# --- Talos + etcd -------------------------------------------------------------
section "Talos versions"
for index in "${!NODES[@]}"; do
    if version_out="$(talosctl -n "${IPS[$index]}" version --short 2>&1)"; then
        server="$(awk '/^Server:/ { s = 1; next } s && /Tag:/ { print $2; exit }' <<<"$version_out")"
        line "$(printf '%-11s %-13s %s' "${NODES[$index]}" "${IPS[$index]}" "${server:-unparsed}")"
        detail "$version_out"
    else
        fail "talosctl version failed for ${NODES[$index]} (${IPS[$index]})" "$version_out"
    fi
done

section "etcd members"
if etcd_out="$(talosctl -n "${IPS[0]}" etcd members 2>&1)"; then
    # count distinct member hostnames, so the check does not depend on column layout
    member_count="$(grep -o 'k8s-prod-[0-9]' <<<"$etcd_out" | sort -u | wc -l | tr -d ' ')"
    line "members: ${member_count}/${#NODES[@]}"
    detail "$etcd_out"
    if (( member_count != ${#NODES[@]} )); then
        fail "etcd reported ${member_count} of ${#NODES[@]} members" "$etcd_out"
    fi
else
    fail 'etcd member query failed' "$etcd_out"
fi

# --- Kubernetes nodes ---------------------------------------------------------
section "Kubernetes nodes"
node_json="$(kubectl get nodes -o json)"
node_total="$(jq '.items | length' <<<"$node_json")"
not_ready="$(jq -r '.items[] | select(any(.status.conditions[]?; .type == "Ready" and .status == "True") | not) | .metadata.name' <<<"$node_json")"
cordoned="$(jq -r '.items[] | select(.spec.unschedulable == true) | .metadata.name' <<<"$node_json")"
line "Ready: $(( node_total - $(grep -c . <<<"${not_ready:-}" || true) ))/${node_total}"
detail "$(kubectl get nodes -o wide)"
if [[ -n "$not_ready" ]]; then
    fail 'nodes not Ready' "$(kubectl get nodes -o wide)"
fi
if [[ -n "$cordoned" ]]; then
    fail "nodes are cordoned: $(tr '\n' ' ' <<<"$cordoned")" 'a leftover cordon means an earlier drain never completed; uncordon deliberately and re-run preflight'
fi

section "Multus safeguards"
ds_json="$(kubectl -n kube-system get ds kube-multus-ds whereabouts cni-ready-untaint -o json)"
line "$(jq -r '.items[] | "\(.metadata.name): \(.status.numberReady // 0)/\(.status.desiredNumberScheduled // 0) ready"' <<<"$ds_json" | paste -sd'; ' -)"
unready_ds="$(jq -r '.items[] | select((.status.numberReady // 0) != (.status.desiredNumberScheduled // 0)) | .metadata.name' <<<"$ds_json")"
if [[ -n "$unready_ds" ]]; then
    fail "unready networking DaemonSets: $(tr '\n' ' ' <<<"$unready_ds")" "$(kubectl -n kube-system get ds kube-multus-ds whereabouts cni-ready-untaint)"
fi

# --- Longhorn -----------------------------------------------------------------
lh_setting() {
    kubectl -n longhorn-system get settings.longhorn.io "$1" -o json 2>/dev/null \
        | jq -r '.value // .spec.value // "unknown"' || echo 'unknown'
}

section "Longhorn volumes"
volume_json="$(kubectl -n longhorn-system get volumes.longhorn.io -o json)"
volume_count="$(jq '.items | length' <<<"$volume_json")"
bad_volumes="$(jq -r '.items[] | select(.status.robustness != "healthy") | [.metadata.name, (.status.state // "unknown"), (.status.robustness // "unknown")] | @tsv' <<<"$volume_json")"
bad_count="$(grep -c . <<<"${bad_volumes:-}" || true)"
line "healthy: $(( volume_count - bad_count ))/${volume_count}"
line "rebuild limit: $(lh_setting concurrent-replica-rebuild-per-node-limit) per node; replenishment wait: $(lh_setting replica-replenishment-wait-interval)s"
detail "$(kubectl -n longhorn-system get volumes.longhorn.io -o custom-columns='NAME:.metadata.name,STATE:.status.state,ROBUSTNESS:.status.robustness,NODE:.status.currentNodeID')"
if (( volume_count == 0 )); then
    fail 'no Longhorn volumes found'
fi
if [[ -n "$bad_volumes" ]]; then
    fail "${bad_count} Longhorn volume(s) not healthy" "$bad_volumes"
fi

section "Longhorn instance-managers"
instance_json="$(kubectl -n longhorn-system get pods -l longhorn.io/component=instance-manager -o json)"
instance_count="$(jq '.items | length' <<<"$instance_json")"
bad_instances="$(jq -r '
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
  | [.metadata.name, .spec.nodeName, (.status.phase // "unknown")] | @tsv
' <<<"$instance_json")"
bad_instance_count="$(grep -c . <<<"${bad_instances:-}" || true)"
line "Running, Ready, and attached to lhnet1: $(( instance_count - bad_instance_count ))/${instance_count}"
detail "$(jq -r '.items[] | [.metadata.name, .spec.nodeName, (.status.phase // "unknown"), ((.status.containerStatuses // []) | all(.ready == true))] | @tsv' <<<"$instance_json")"
if (( instance_count == 0 )); then
    fail 'no Longhorn instance-manager pods found'
fi
if [[ -n "$bad_instances" ]]; then
    fail "${bad_instance_count} instance-manager(s) not Ready or missing storage-network/lhnet1" "$bad_instances"
fi

# --- CloudNativePG ------------------------------------------------------------
# Facts only. Whether a primary is moved, and how, is the operator's call.
section "CloudNativePG"
operator_image="$(kubectl -n cnpg-system get deploy -l app.kubernetes.io/name=cloudnative-pg \
    -o jsonpath='{.items[0].spec.template.spec.containers[0].image}' 2>/dev/null || echo 'unknown')"
line "operator image: ${operator_image}"
line "match the kubectl-cnpg client to that tag before any promotion"
cnpg_json="$(kubectl get clusters.postgresql.cnpg.io -A -o json 2>/dev/null || echo '{"items":[]}')"
cnpg_count="$(jq '.items | length' <<<"$cnpg_json")"
if (( cnpg_count == 0 )); then
    line 'no CloudNativePG clusters found'
else
    while IFS=$'\t' read -r ns name instances ready primary phase; do
        [[ -z "$name" ]] && continue
        primary_node='unscheduled'
        if [[ "$primary" != 'none' ]]; then
            primary_node="$(kubectl -n "$ns" get pod "$primary" -o jsonpath='{.spec.nodeName}' 2>/dev/null || echo 'unknown')"
        fi
        marker=''
        [[ -n "$candidate" && "$primary_node" == "$candidate" ]] && marker='  <-- primary on candidate'
        [[ -n "$marker" && "$instances" == '1' ]] && marker='  <-- SINGLETON primary on candidate'
        line "$(printf '%-14s %-16s instances=%s ready=%s primary=%s on %s (%s)%s' \
            "$ns" "$name" "$instances" "$ready" "$primary" "$primary_node" "$phase" "$marker")"
    done < <(jq -r '.items[] | [
        .metadata.namespace, .metadata.name,
        (.spec.instances | tostring), ((.status.readyInstances // 0) | tostring),
        (.status.currentPrimary // "none"), (.status.phase // "unknown")
    ] | @tsv' <<<"$cnpg_json")
    if [[ -n "$candidate" ]]; then
        note 'a singleton primary on the candidate needs an explicit availability decision — see references/cnpg-failover.md'
    fi
fi

# --- Flux ---------------------------------------------------------------------
# Flux reverts live patches. A suspension left over from an earlier rollout is
# a stop condition; a suspension you created on purpose must be recorded.
section "Flux Kustomizations"
suspended="$(kubectl get kustomizations.kustomize.toolkit.fluxcd.io -A -o json 2>/dev/null \
    | jq -r '.items[] | select(.spec.suspend == true) | "\(.metadata.namespace)/\(.metadata.name)"' || echo '')"
if [[ -z "$suspended" ]]; then
    line 'none suspended'
else
    printf '%s\n' "$suspended" | sed 's/^/  suspended: /'
    note 'confirm each suspension is intentional and recorded; restore before the rollout is called complete'
fi

# --- disruption budgets + candidate workloads ---------------------------------
section "PodDisruptionBudgets blocking eviction"
pdb_json="$(kubectl get pdb -A -o json)"
blocking="$(jq -r '.items[] | select((.status.disruptionsAllowed // 0) == 0) | "\(.metadata.namespace)/\(.metadata.name)  allowed=0  healthy=\(.status.currentHealthy // 0)/\(.status.desiredHealthy // 0)"' <<<"$pdb_json")"
if [[ -z "$blocking" ]]; then
    line 'none at zero allowed disruptions'
else
    printf '%s\n' "$blocking" | sed 's/^/  /'
    note 'resolve each blocker deliberately; never reach for --force or --disable-eviction'
fi
detail "$(kubectl get pdb -A)"

if [[ -n "$candidate" ]]; then
    section "candidate workloads on $candidate"
    pods_json="$(kubectl get pods -A --field-selector "spec.nodeName=$candidate" -o json)"
    total_pods="$(jq '.items | length' <<<"$pods_json")"
    movable="$(jq -r '.items[] | select((.metadata.ownerReferences // []) | any(.kind == "DaemonSet") | not) | "\(.metadata.namespace)/\(.metadata.name)  \((.metadata.ownerReferences // [{}])[0].kind // "bare")"' <<<"$pods_json")"
    line "pods: ${total_pods} total, $(grep -c . <<<"${movable:-}" || true) not DaemonSet-owned"
    [[ -n "$movable" ]] && printf '%s\n' "$movable" | sed 's/^/  /'
    note 'do not run a server-side drain dry-run: its simulated cordon is not persisted, so Longhorn cannot react and the instance-manager PDB reports a false failure'
fi

# --- verdict ------------------------------------------------------------------
printf '\n'
if (( ${#FAILURES[@]} > 0 )); then
    printf 'PREFLIGHT FAILED (%d check(s)):\n' "${#FAILURES[@]}" >&2
    printf '  - %s\n' "${FAILURES[@]}" >&2
    exit 1
fi

printf 'PREFLIGHT CHECKS PASSED: etcd membership responded, all %s nodes are Ready and uncordoned, networking safeguards are Ready, %s Longhorn volumes are healthy, and %s instance-managers are Ready with lhnet1.\n' \
    "$node_total" "$volume_count" "$instance_count"
printf 'Still owed before go/no-go: talosctl health on a healthy control-plane node, an etcd snapshot verified at its path, and an explicit decision for any CloudNativePG primary on the candidate.\n'
