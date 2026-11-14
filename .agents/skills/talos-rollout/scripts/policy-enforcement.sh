#!/usr/bin/env bash
# Flag pod endpoints that a Cilium default-deny policy selects but whose
# realized policy does not enforce it (cilium/cilium#49019, home-lab #1348).
# Read-only toward the cluster: it runs `cilium-dbg policy get` and
# `cilium-dbg endpoint list` in every agent and matches endpoint labels against
# each rule's subject. Every node must have a readable agent.
#
#   policy-enforcement.sh                 every unenforced endpoint fails; run
#                                         this after any cilium-agent restart
#   policy-enforcement.sh --record NODE   before rolling NODE, save the count of
#                                         unenforced endpoints per identity on
#                                         the other nodes
#   policy-enforcement.sh NODE            after rolling NODE, fail on anything
#                                         unenforced on NODE, and on any identity
#                                         with more unenforced endpoints on the
#                                         other nodes than the saved count
#
# Every endpoint on a rebooted node is created after its agent started, so the
# restored-endpoint failure cannot explain anything found there. Agents that
# have not restarted keep the pre-existing stale set, and a pod drained onto
# one can reuse a stale identity, including one that was already unenforced on
# NODE; the saved count separates that from the known set. Without a saved
# count, every unenforced endpoint fails.
set -Eeuo pipefail

usage() {
    echo "usage: $0 [--record] [k8s-prod-1|k8s-prod-2|k8s-prod-3|node-ip]" >&2
    exit 2
}

record=0
if [[ "${1:-}" == --record ]]; then
    record=1
    shift
fi
node=''
case "${1:-}" in
    '') (( ! record )) || usage ;;
    k8s-prod-1|10.32.30.11) node=k8s-prod-1 ;;
    k8s-prod-2|10.32.30.12) node=k8s-prod-2 ;;
    k8s-prod-3|10.32.30.13) node=k8s-prod-3 ;;
    *) usage ;;
esac
(( $# <= 1 )) || usage

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
cd "$ROOT_DIR"

if command -v mise >/dev/null 2>&1 && [[ -z "${KUBECONFIG:-}" || ! -f "${KUBECONFIG}" ]]; then
    eval "$(mise env -s bash)"
fi

for bin in jq kubectl; do
    command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
done

kubectl() { command kubectl --kubeconfig "${KUBECONFIG:-$ROOT_DIR/kubeconfig}" "$@"; }

baseline="${POLICY_BASELINE_DIR:-$ROOT_DIR/.talos-rollout}/policy-baseline-${node}.json"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

problems=()
: >"$work_dir/rows.jsonl"

nodes="$(kubectl get nodes -o json | jq -r '.items[].metadata.name' | sort)"
agents="$(kubectl -n kube-system get pods -l k8s-app=cilium -o json \
    | jq -r '.items[] | select(.status.phase == "Running") | [.spec.nodeName, .metadata.name] | @tsv')"
[[ -n "$nodes" ]] || problems+=('no Kubernetes nodes found')
if [[ -n "$node" ]] && ! grep -qx "$node" <<<"$nodes"; then
    problems+=("node $node not found")
fi

for each_node in $nodes; do
    agent="$(awk -F'\t' -v n="$each_node" '$1 == n { print $2; exit }' <<<"$agents")"
    if [[ -z "$agent" ]]; then
        problems+=("no Running cilium agent on $each_node")
        continue
    fi
    if ! kubectl -n kube-system exec "$agent" -c cilium-agent -- cilium-dbg policy get -o json >"$work_dir/policy.json" 2>"$work_dir/err" \
        || ! kubectl -n kube-system exec "$agent" -c cilium-agent -- cilium-dbg endpoint list -o json >"$work_dir/endpoints.json" 2>>"$work_dir/err"; then
        sed 's/^/  /' "$work_dir/err" >&2
        problems+=("could not read policy state from $agent on $each_node")
        continue
    fi
    # Every agent holds this cluster's policies and at least its health
    # endpoint, so an empty list of either is a failed read.
    if ! jq -e '.revision != null and (.policy | if type == "string" then fromjson else . end | type == "array" and length > 0)' \
            "$work_dir/policy.json" >/dev/null 2>&1 \
        || ! jq -e 'type == "array" and length > 0' "$work_dir/endpoints.json" >/dev/null 2>&1; then
        problems+=("unexpected or empty policy state from $agent on $each_node")
        continue
    fi
    kubectl get pods -A --field-selector "spec.nodeName=$each_node" -o json \
        | jq -c '[.items[] | select(.status.podIP) | {key: .status.podIP, value: "\(.metadata.namespace)/\(.metadata.name)"}] | from_entries' \
        >"$work_dir/pods.json"
    jq -c --slurpfile agent "$work_dir/policy.json" -f "$SCRIPT_DIR/policy-enforcement.jq" "$work_dir/endpoints.json" \
        | jq -c --arg node "$each_node" --slurpfile pods "$work_dir/pods.json" \
            '. + {node: $node, pod: ($pods[0][.ip] // .ip)}' >>"$work_dir/rows.jsonl"
done

# Per-node summary, then every endpoint that is not enforcing.
jq -rs --argjson nodes "$(jq -Rn '[inputs | select(length > 0)]' <<<"$nodes")" '
  . as $rows
  | $nodes[] as $n
  | [$rows[] | select(.node == $n)] as $r
  | "\n== \($n) ==",
    "  selected by a default-deny policy: \($r | length); not enforcing: \([$r[] | select(.status == "fail")] | length); pending regeneration: \([$r[] | select(.status == "pending")] | length)",
    ($r[] | select(.status != "ok")
      | "  \(.status | ascii_upcase)  \(.pod)  identity=\(.identity) realized=\(.enabled) expected=\(.expected)  rules=\(.rules)")
' "$work_dir/rows.jsonl"
printf '\n'

if (( ${#problems[@]} > 0 )); then
    printf 'POLICY ENFORCEMENT INCOMPLETE:\n' >&2
    printf '  - %s\n' "${problems[@]}" >&2
    exit 1
fi

if (( record )); then
    # A pending endpoint may still settle into a failure, which the count
    # would then excuse.
    if jq -e 'select(.status == "pending")' "$work_dir/rows.jsonl" >/dev/null; then
        printf 'Endpoints are still regenerating; re-run once none is pending.\n' >&2
        exit 1
    fi
    mkdir -p "$(dirname "$baseline")"
    jq -s --arg node "$node" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{
      node: $node, recorded_at: $at,
      unenforced: (map(select(.status != "ok" and .node != $node)) | group_by(.identity)
        | map({key: (.[0].identity | tostring), value: length}) | from_entries)
    }' "$work_dir/rows.jsonl" >"$baseline"
    printf 'Recorded %s unenforced endpoint(s) on the other nodes before rolling %s in %s.\n' \
        "$(jq '[.unenforced[]] | add // 0' "$baseline")" "$node" "$baseline"
    exit 0
fi

if [[ -z "$node" ]]; then
    gating="$(jq -c 'select(.status != "ok")' "$work_dir/rows.jsonl")"
    scope='every node'
elif [[ -f "$baseline" ]]; then
    printf 'Comparing other nodes with the count recorded at %s.\n' "$(jq -r '.recorded_at' "$baseline")"
    # On the rolled node nothing may be unenforced; elsewhere, no identity may
    # have more unenforced endpoints than before the drain.
    gating="$(jq -cs --arg node "$node" --slurpfile base "$baseline" '
      (map(select(.status != "ok" and .node != $node)) | group_by(.identity)
        | map({key: (.[0].identity | tostring), value: length}) | from_entries) as $now
      | .[] | select(.status != "ok")
      | select(.node == $node
          or .status == "pending"
          or $now[.identity | tostring] > ($base[0].unenforced[.identity | tostring] // 0))
    ' "$work_dir/rows.jsonl")"
    scope="$node, with no new unenforced endpoints elsewhere"
else
    printf 'No count was recorded before rolling %s (%s), so unenforced endpoints on every node fail.\n' "$node" "$baseline" >&2
    gating="$(jq -c 'select(.status != "ok")' "$work_dir/rows.jsonl")"
    scope='every node'
fi

if [[ -n "$gating" ]]; then
    printf 'POLICY ENFORCEMENT FAILED:\n' >&2
    jq -r '"  - \(.node): \(.pod) \(.status) (identity \(.identity), realized \(.enabled), expected \(.expected))"' <<<"$gating" >&2
    if [[ -n "$node" && -f "$baseline" ]]; then
        printf 'Re-run if any are pending regeneration. Otherwise this is not the known #1348 set: record it there and stop.\n' >&2
    else
        printf 'Re-run if any are pending regeneration. Before Cilium 1.20.5, compare with the set #1348 lists; anything else is new.\n' >&2
    fi
    exit 1
fi
printf 'POLICY ENFORCEMENT PASSED for %s.\n' "$scope"
