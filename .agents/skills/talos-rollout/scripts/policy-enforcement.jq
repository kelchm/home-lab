# Match one Cilium agent's pod endpoints against the policy entries it holds
# and report each endpoint a default-deny entry selects. Cilium can cache a
# selected identity's policy as enforcing nothing and never recompute it
# (cilium/cilium#49019, home-lab #1348); the endpoint's realized policy then
# lacks a direction its rules select.
#
# Input:  `cilium-dbg endpoint list -o json` from the agent.
# $agent: `cilium-dbg policy get -o json` from the same agent, slurped.
# Output: one object per selected pod endpoint, with status "ok", "fail", or
#         "pending" (not ready, or not yet at the agent's policy revision).
#
# The check compares directions, not rule sets: a surviving rule whose subject
# is a true wildcard (only a cluster-wide policy) would keep a direction
# enforced while specific rules are missing. None exist here today.

# "k8s:app=x" -> {src: "k8s", key: "app", value: "x"}
def label_entries:
  map(capture("^(?<src>[^:]+):(?<rest>.*)$")
    | (.rest | index("=")) as $eq
    | {src, key: (if $eq then .rest[:$eq] else .rest end), value: (if $eq then .rest[$eq + 1:] else "" end)});

# Values of a selector key ("any:app" or "k8s:app") among endpoint labels.
def values_for($selector_key; $labels):
  ($selector_key
    | if test("^[^:/=]+:") then capture("^(?<src>[^:]+):(?<key>.*)$") else {src: "any", key: .} end) as $k
  | [$labels[] | select(.key == $k.key and ($k.src == "any" or .src == $k.src)) | .value];

def selects($labels):
  ((.matchLabels // {}) | to_entries | all(.value as $v | values_for(.key; $labels) | any(. == $v)))
  and ((.matchExpressions // []) | all(
    values_for(.key; $labels) as $have
    | (.values // []) as $allowed
    | if .operator == "In" then any($have[]; . as $h | $allowed | any(. == $h))
      elif .operator == "NotIn" then all($have[]; . as $h | $allowed | any(. == $h) | not)
      elif .operator == "Exists" then ($have | length) > 0
      elif .operator == "DoesNotExist" then ($have | length) == 0
      else false end));

def rule_name:
  (.Labels // []) as $l
  | [($l[] | select(.key == "io.cilium.k8s.policy.namespace") | .value),
     ($l[] | select(.key == "io.cilium.k8s.policy.name") | .value)]
  | join("/");

($agent[0].policy | if type == "string" then fromjson else . end) as $entries
| ($agent[0].revision // 0) as $revision
| [$entries[] | select(.DefaultDeny == true and .Node != true and .Subject != null)] as $deny
| .[]
| select(any(.status.identity.labels[]?; startswith("k8s:io.kubernetes.pod.namespace=")))
| (.status.identity.labels | label_entries) as $labels
| [$deny[] | select(.Subject | selects($labels))] as $hits
| select($hits | length > 0)
| (.status.policy.realized // {}) as $realized
| ($realized["policy-enabled"] // "none") as $enabled
| [if any($hits[]; .Ingress == true) then "ingress" else empty end,
   if any($hits[]; .Ingress != true) then "egress" else empty end] as $expected
| [$expected[] | select($enabled != "both" and $enabled != .)] as $missing
| {
    ip: (.status.networking.addressing[0].ipv4 // ""),
    identity: (.status.identity.id // 0),
    enabled: $enabled,
    expected: ($expected | join("+")),
    rules: ([$hits[] | rule_name] | unique | join(",")),
    status: (if .status.state != "ready" or ($realized["policy-revision"] // 0) < $revision then "pending"
             elif ($missing | length) > 0 then "fail"
             else "ok" end)
  }
