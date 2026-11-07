#!/usr/bin/env bash
# Exercise the instance-manager and policy-enforcement gates against captured
# shapes, without credentials, a cluster, or network access.
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- instance-manager gate ----------------------------------------------------
# Shape captured from a live instance-manager pod on 2026-11-07.
cat > "$TEST_DIR/im.json" <<'JSON'
{
  "metadata": {
    "name": "instance-manager-test",
    "annotations": {
      "k8s.v1.cni.cncf.io/network-status": "[{\"name\":\"cilium\",\"interface\":\"eth0\",\"ips\":[\"10.42.0.79\"],\"default\":true,\"dns\":{}},{\"name\":\"longhorn-system/storage-network\",\"interface\":\"lhnet1\",\"ips\":[\"10.32.25.129\"],\"dns\":{}}]"
    }
  },
  "spec": {"nodeName": "k8s-prod-1"},
  "status": {
    "phase": "Running",
    "podIP": "10.42.0.79",
    "containerStatuses": [{"ready": true}]
  }
}
JSON

im_problems() {
    jq -c -L "$SCRIPT_DIR" "include \"instance-manager\"; $1 | instance_manager_problems" "$TEST_DIR/im.json"
}
expect_im() {
    local got
    got="$(im_problems "$1")"
    [[ "$got" == *"$2"* ]] || fail "instance-manager case '$1': expected '$2', got $got"
}

[[ "$(im_problems .)" == '[]' ]] || fail "healthy instance-manager reported $(im_problems .)"
# The pod was recreated (new IP) but kept its predecessor's annotation.
expect_im '.status.podIP = "10.42.0.200"' 'network-status is not for this pod'
expect_im '.status.podIP = null' 'network-status is not for this pod'
expect_im '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] |= (fromjson | map(del(.default)) | tojson)' 'network-status is not for this pod'
expect_im '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] |= (fromjson | map(select(.default)) | tojson)' 'no storage-network on lhnet1'
expect_im '.metadata.annotations = {}' 'no storage-network on lhnet1'
expect_im '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] = "not json"' 'no storage-network on lhnet1'
expect_im '.status.containerStatuses[0].ready = false' 'not Ready'
expect_im '.status.phase = "Pending"' 'phase Pending'
echo 'ok: instance-manager gate rejects a network-status written for another pod IP'

# --- policy-enforcement matcher -----------------------------------------------
# Entries follow `cilium-dbg policy get -o json` on v1.20.4: one per direction,
# with the policy list serialized as a string.
entry() { # <ingress> <default-deny> <node> <name> <subject-json>
    jq -nc --argjson ingress "$1" --argjson deny "$2" --argjson node "$3" --arg name "$4" --argjson subject "$5" '{
      Subject: $subject, Ingress: $ingress, DefaultDeny: $deny, Node: $node,
      Labels: [{key: "io.cilium.k8s.policy.name", value: $name, source: "k8s"},
               {key: "io.cilium.k8s.policy.namespace", value: "media", source: "k8s"}]}'
}
{
    entry true true false app-in '{"matchLabels":{"any:app":"app","k8s:io.kubernetes.pod.namespace":"media"}}'
    entry false true false app-out '{"matchLabels":{"any:app":"app","k8s:io.kubernetes.pod.namespace":"media"}}'
    entry true true false expr '{"matchExpressions":[{"key":"any:tier","operator":"In","values":["db","cache"]},{"key":"k8s:skip","operator":"DoesNotExist"},{"key":"any:zone","operator":"NotIn","values":["x"]},{"key":"any:team","operator":"Exists"}]}'
    entry true true false wrong-source '{"matchLabels":{"reserved:app":"other"}}'
    entry true false false no-default-deny '{"matchLabels":{"any:app":"allow-only"}}'
    entry true true true host '{"matchLabels":{"any:app":"hostish"}}'
} | jq -sc '{revision: 7, policy: tojson}' > "$TEST_DIR/policy.json"

endpoint() { # <ip> <state> <revision> <enabled> <labels...>
    local ip="$1" state="$2" revision="$3" enabled="$4"
    shift 4
    jq -nc --arg ip "$ip" --arg state "$state" --argjson rev "$revision" --arg enabled "$enabled" '{
      status: {state: $state, identity: {id: 1, labels: $ARGS.positional},
               networking: {addressing: [{ipv4: $ip}]},
               policy: {realized: {"policy-enabled": $enabled, "policy-revision": $rev}}}}' --args "$@"
}
ns=k8s:io.kubernetes.pod.namespace=media
{
    endpoint 10.0.0.1 ready 7 none k8s:app=app "$ns"            # the #1348 failure
    endpoint 10.0.0.2 ready 7 both k8s:app=app "$ns"            # enforcing
    endpoint 10.0.0.3 ready 7 ingress k8s:app=app "$ns"         # egress rule not realized
    endpoint 10.0.0.4 ready 6 none k8s:app=app "$ns"            # behind the agent's revision
    endpoint 10.0.0.5 regenerating 7 none k8s:app=app "$ns"     # mid-regeneration
    endpoint 10.0.0.6 ready 7 none k8s:tier=db k8s:team=a "$ns" # matched by expressions
    endpoint 10.0.0.7 ready 7 none k8s:tier=db k8s:team=a k8s:skip=1 "$ns"
    endpoint 10.0.0.8 ready 7 none k8s:tier=db k8s:team=a k8s:zone=x "$ns"
    endpoint 10.0.0.9 ready 7 none k8s:app=other "$ns"          # selector names another source
    endpoint 10.0.0.10 ready 7 none k8s:app=allow-only "$ns"    # no default deny: enforces nothing by design
    endpoint 10.0.0.11 ready 7 none k8s:app=hostish "$ns"       # host policy entry
    endpoint 10.0.0.12 ready 7 none reserved:health             # not a pod endpoint
} | jq -sc . > "$TEST_DIR/endpoints.json"

jq -c --slurpfile agent "$TEST_DIR/policy.json" -f "$SCRIPT_DIR/policy-enforcement.jq" "$TEST_DIR/endpoints.json" \
    | jq -r '[.ip, .status, .expected] | join(" ")' > "$TEST_DIR/got"
cat > "$TEST_DIR/want" <<'EOF'
10.0.0.1 fail ingress+egress
10.0.0.2 ok ingress+egress
10.0.0.3 fail ingress+egress
10.0.0.4 pending ingress+egress
10.0.0.5 pending ingress+egress
10.0.0.6 fail ingress
EOF
diff -u "$TEST_DIR/want" "$TEST_DIR/got" || fail 'policy-enforcement matcher output differs'
echo 'ok: matcher flags selected endpoints that do not enforce, and skips unselected, host and allow-only cases'

# --- cluster-facing wrappers -------------------------------------------------
# One stub kubectl serves both wrappers from files in $GATE_TEST_DIR, so each
# case swaps files rather than code.
mkdir "$TEST_DIR/bin" "$TEST_DIR/state"
cat > "$TEST_DIR/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
set -eu
[[ "$1" == --kubeconfig ]] && shift 2
d="$GATE_TEST_DIR/state"
args="$*"
case "$args" in
    'get nodes -o json') cat "$d/nodes.json" ;;
    *'get pods -l k8s-app=cilium -o json') cat "$d/agents.json" ;;
    *' exec '*' -- cilium-dbg policy get -o json')
        agent="${args#*exec }"; [[ ! -e "$d/unreadable-${agent%% *}" ]] || { echo 'error: agent unreachable' >&2; exit 1; }
        cat "$GATE_TEST_DIR/policy.json" ;;
    *' exec '*' -- cilium-dbg endpoint list -o json')
        agent="${args#*exec }"; cat "$d/ep-${agent%% *}.json" ;;
    *'get pods -A --field-selector spec.nodeName='*)
        node="${args#*spec.nodeName=}"; cat "$d/pods-${node%% *}.json" ;;
    *' exec '*' -- ip -o -4 addr show dev lhnet1')
        pod="${args#*exec }"; cat "$d/lhnet1-${pod%% *}" 2>/dev/null || { echo 'Device "lhnet1" does not exist.' >&2; exit 1; } ;;
    *) echo "unexpected kubectl call: $args" >&2; exit 1 ;;
esac
STUB
chmod +x "$TEST_DIR/bin/kubectl"
printf 'fixture\n' > "$TEST_DIR/kubeconfig"
with_stub() {
    local rc=0
    GATE_TEST_DIR="$TEST_DIR" KUBECONFIG="$TEST_DIR/kubeconfig" POLICY_BASELINE_DIR="$TEST_DIR/baseline" \
        PATH="$TEST_DIR/bin:$PATH" "$@" > "$TEST_DIR/out" 2>&1 || rc=$?
    ! grep -q 'unexpected kubectl call' "$TEST_DIR/out" || { cat "$TEST_DIR/out" >&2; fail "$* made an unexpected kubectl call"; }
    return "$rc"
}
expect_out() { grep -qF -- "$1" "$TEST_DIR/out" || { cat "$TEST_DIR/out" >&2; fail "expected output: $1"; }; }

# instance-manager: the annotation passes, so the pod's own lhnet1 decides.
jq '{items: [.]}' "$TEST_DIR/im.json" > "$TEST_DIR/ims.json"
im_rows() { with_stub "$SCRIPT_DIR/failing-instance-managers.sh" < "$TEST_DIR/ims.json" || fail 'failing-instance-managers.sh exited non-zero'; }
printf '5: lhnet1    inet 10.32.25.129/28 brd 10.32.25.143 scope global lhnet1\n' > "$TEST_DIR/state/lhnet1-instance-manager-test"
im_rows
[[ ! -s "$TEST_DIR/out" ]] || fail "instance-manager with its lhnet1 address reported: $(cat "$TEST_DIR/out")"
printf '5: lhnet1    inet 10.32.25.133/28 brd 10.32.25.143 scope global lhnet1\n' > "$TEST_DIR/state/lhnet1-instance-manager-test"
im_rows
expect_out 'does not carry the annotated 10.32.25.129'
rm "$TEST_DIR/state/lhnet1-instance-manager-test"
im_rows
expect_out 'lhnet1 not present'
echo 'ok: instance-manager gate confirms lhnet1 inside the pod, even when the pod IP is reused'

# policy enforcement: k8s-prod-1 enforces; k8s-prod-2 holds one known stale
# endpoint (identity 1, like every fixture endpoint).
agents() { jq -n '{items: [$ARGS.positional[] | split("=") | {metadata: {name: .[1]}, spec: {nodeName: .[0]}, status: {phase: "Running"}}]}' --args "$@" > "$TEST_DIR/state/agents.json"; }
nodes() { jq -n '{items: [$ARGS.positional[] | {metadata: {name: .}}]}' --args "$@" > "$TEST_DIR/state/nodes.json"; }
endpoints() { jq -c "[.[] | select(.status.networking.addressing[0].ipv4 | IN($2))]" "$TEST_DIR/endpoints.json" > "$TEST_DIR/state/ep-$1.json"; }
printf '%s' '{"items":[{"metadata":{"namespace":"media","name":"app-good"},"status":{"podIP":"10.0.0.2"}}]}' > "$TEST_DIR/state/pods-k8s-prod-1.json"
printf '%s' '{"items":[{"metadata":{"namespace":"media","name":"app-stale"},"status":{"podIP":"10.0.0.1"}},{"metadata":{"namespace":"media","name":"app-moved"},"status":{"podIP":"10.0.0.9"}}]}' > "$TEST_DIR/state/pods-k8s-prod-2.json"
nodes k8s-prod-1 k8s-prod-2
agents k8s-prod-1=cilium-a k8s-prod-2=cilium-b
endpoints cilium-a '"10.0.0.2"'
endpoints cilium-b '"10.0.0.1"'
gate() { with_stub "$SCRIPT_DIR/policy-enforcement.sh" "$@"; }

! gate || fail 'the cluster-wide check passed with a stale endpoint'
expect_out '  - k8s-prod-2: media/app-stale fail'
! gate k8s-prod-1 || fail 'a node check without a recorded count ignored other nodes'
expect_out 'No count was recorded before rolling k8s-prod-1'
gate --record k8s-prod-1 || { cat "$TEST_DIR/out" >&2; fail 'recording failed'; }
gate k8s-prod-1 || { cat "$TEST_DIR/out" >&2; fail 'the known stale endpoint on another node gated the rolled node'; }
expect_out 'FAIL  media/app-stale'
expect_out 'POLICY ENFORCEMENT PASSED for k8s-prod-1'
# A pod drained off k8s-prod-1 lands on k8s-prod-2 and reuses the stale identity.
jq -c '. + [.[0] | .status.networking.addressing[0].ipv4 = "10.0.0.9"]' "$TEST_DIR/state/ep-cilium-b.json" > "$TEST_DIR/state/ep-new.json"
mv "$TEST_DIR/state/ep-new.json" "$TEST_DIR/state/ep-cilium-b.json"
! gate k8s-prod-1 || fail 'a newly unenforced endpoint on another node passed'
expect_out '  - k8s-prod-2: media/app-moved fail'
endpoints cilium-b '"10.0.0.1"'
endpoints cilium-a '"10.0.0.1", "10.0.0.2"'
! gate k8s-prod-1 || fail 'an unenforced endpoint on the rolled node passed'
expect_out '  - k8s-prod-1: 10.0.0.1 fail'
endpoints cilium-a '"10.0.0.2"'
agents k8s-prod-1=cilium-a
! gate || fail 'the cluster-wide check passed without an agent on every node'
expect_out 'no Running cilium agent on k8s-prod-2'
! gate k8s-prod-1 || fail 'the node check passed without an agent on every node'
expect_out 'no Running cilium agent on k8s-prod-2'
agents
! gate || fail 'the cluster-wide check passed with no agents'
expect_out 'no Running cilium agent on k8s-prod-1'
agents k8s-prod-1=cilium-a k8s-prod-2=cilium-b
! gate 10.32.30.13 || fail 'a node that is not in the cluster passed'
expect_out 'node k8s-prod-3 not found'
# A stale endpoint on the rolled node is recreated on a node whose agent holds
# the same stale identity; the cluster-wide count would not change.
endpoints cilium-a '"10.0.0.1", "10.0.0.2"'
endpoints cilium-b '"10.0.0.2"'
gate --record k8s-prod-1 || { cat "$TEST_DIR/out" >&2; fail 'recording failed'; }
expect_out 'Recorded 0 unenforced endpoint(s) on the other nodes'
endpoints cilium-a '"10.0.0.2"'
jq -c '[.[] | select(.status.networking.addressing[0].ipv4 == "10.0.0.2")]
  + [.[] | select(.status.networking.addressing[0].ipv4 == "10.0.0.1") | .status.networking.addressing[0].ipv4 = "10.0.0.9"]' \
    "$TEST_DIR/endpoints.json" > "$TEST_DIR/state/ep-cilium-b.json"
! gate k8s-prod-1 || fail 'a stale endpoint moved off the rolled node passed'
expect_out '  - k8s-prod-2: media/app-moved fail'
# A pending endpoint could settle into a failure the count would then excuse.
endpoints cilium-b '"10.0.0.2", "10.0.0.4"'
! gate --record k8s-prod-1 || fail 'a count was recorded while an endpoint was pending'
expect_out 'still regenerating'
# Counts are per identity: a new failure in one identity is not excused by
# another identity's recorded failures, even when the total is unchanged.
stale() { jq -c "[.[] | select(.status.networking.addressing[0].ipv4 == \"10.0.0.1\")][0] | .status.networking.addressing[0].ipv4 = \"$1\" | .status.identity.id = $2" "$TEST_DIR/endpoints.json"; }
{ stale 10.0.0.1 1; stale 10.0.0.9 1; } | jq -sc . > "$TEST_DIR/state/ep-cilium-b.json"
gate --record k8s-prod-1 || { cat "$TEST_DIR/out" >&2; fail 'recording failed'; }
expect_out 'Recorded 2 unenforced endpoint(s) on the other nodes'
{ stale 10.0.0.1 1; stale 10.0.0.9 2; } | jq -sc . > "$TEST_DIR/state/ep-cilium-b.json"
! gate k8s-prod-1 || fail 'a new identity failure passed because the total was unchanged'
expect_out '  - k8s-prod-2: media/app-moved fail (identity 2'
! grep -q '^  - k8s-prod-2: media/app-stale' "$TEST_DIR/out" || fail 'a recorded failure gated'
# An agent whose state cannot be read has not been checked.
touch "$TEST_DIR/state/unreadable-cilium-a"
! gate || fail 'an unreadable agent passed'
expect_out 'could not read policy state from cilium-a on k8s-prod-1'
rm "$TEST_DIR/state/unreadable-cilium-a"
# An agent that answers with nothing has not been checked.
printf '[]' > "$TEST_DIR/state/ep-cilium-b.json"
! gate || fail 'an empty endpoint list passed'
expect_out 'unexpected or empty policy state from cilium-b on k8s-prod-2'
echo 'ok: rolled node and new failures gate; the recorded known set does not; every node needs a readable agent'
