#!/usr/bin/env bash
# pr-triage-verify.sh — read-only post-merge rollout verification.
#
# After merging PRs, Flux reconciles them onto the LIVE cluster. This confirms the
# rollout actually landed healthy, in the order that matters:
#   1. Flux source caught up   — has the GitRepository pulled the latest main commit?
#   2. Kustomizations Ready    — the apply layer reconciled that revision.
#   3. HelmReleases            — every HR Ready; prints each deployed chart version
#                                so you can confirm merged targets upgraded AND that
#                                held PRs did NOT (cross-check the versions yourself).
#   4. Unhealthy pods          — anything not Running/Ready. Completed Jobs (phase
#                                Succeeded, 0/1 ready) are NOT flagged — they're
#                                supposed to look that way.
#   5. Certificates            — all Ready (cert-manager rolls can blip the webhook).
#   6. Recent Warning events   — CAVEAT: during a roll, readiness-probe failures that
#                                reference OLD/terminating pod IPs are expected churn,
#                                not failures. Judge by current pod state (step 4).
#
# Read-only: kubectl get / gh api only. Nothing is mutated.
#
# Usage:  scripts/pr-triage-verify.sh [warn_minutes]     (default warn window: 20)
#   Run from the repo root so mise supplies KUBECONFIG/kubectl.
set -euo pipefail

WARN_MIN="${1:-20}"
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
export KUBECONFIG="${KUBECONFIG:-$ROOT/kubeconfig}"
KC() { mise exec -- kubectl "$@"; }

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "?")
echo "############################################################"
echo "# PR triage rollout verify — $REPO"
echo "############################################################"

# ---- 1. Flux source caught up ---------------------------------------------
echo
echo "== 1. Flux source vs latest main =========================================="
latest=$(gh api "repos/$REPO/commits/main" --jq '.sha' 2>/dev/null || echo "")
fluxrev=$(KC get gitrepository -n flux-system flux-system -o jsonpath='{.status.artifact.revision}' 2>/dev/null || echo "")
echo "  latest main (remote): ${latest:0:12}"
echo "  flux GitRepository  : $fluxrev"
if [ -n "$latest" ] && [[ "$fluxrev" == *"${latest:0:12}"* ]]; then
  echo "  ✅ Flux has pulled the latest commit."
else
  echo "  ⏳ Flux not yet at latest — reconcile pending (or force: flux reconcile source git flux-system)."
fi

# ---- 2. Kustomizations -----------------------------------------------------
echo
echo "== 2. Flux Kustomizations ================================================="
KC get kustomization -A -o json 2>/dev/null | jq -r '
  ([.items[] | select(([.status.conditions[]?|select(.type=="Ready")][0].status)!="True")]) as $bad
  | "  \(.items|length) total, \(($bad|length)) not Ready"
  , ($bad[] | "  ❌ \(.metadata.namespace)/\(.metadata.name): \(([.status.conditions[]?|select(.type=="Ready")][0].message)//"?")")'

# ---- 3. HelmReleases: readiness + deployed version ------------------------
echo
echo "== 3. HelmReleases (readiness + deployed version) ========================="
KC get helmrelease -A -o json 2>/dev/null | jq -r '
  ([.items[] | select(([.status.conditions[]?|select(.type=="Ready")][0].status)!="True")]) as $bad
  | "  \(.items|length) total, \(($bad|length)) NOT Ready\n"
  , ($bad[] | "  ❌ \(.metadata.namespace)/\(.metadata.name)  \(.status.history[0].chartVersion // "?")  — \(([.status.conditions[]?|select(.type=="Ready")][0].message)//"?"|.[0:70])")
  , (if ($bad|length)==0 then "  ✅ all HelmReleases Ready" else "" end)'
echo "  --- deployed chart versions (cross-check merged targets ↑ and held PRs ↓) ---"
KC get helmrelease -A -o json 2>/dev/null | jq -r '
  .items[] | "     \(.metadata.namespace)/\(.metadata.name)\t\(.status.history[0].chartVersion // "?")"' \
  | sort | column -t -s$'\t'

# ---- 4. Unhealthy pods (completed Jobs excluded) --------------------------
echo
echo "== 4. Unhealthy pods (Running-but-not-Ready / Pending / Failed / waiting) =="
bad=$(KC get pods -A -o json 2>/dev/null | jq -r '
  .items[]
  | .status.phase as $ph
  | [.status.containerStatuses[]?] as $cs
  | ($cs|map(select(.ready==true))|length) as $rdy
  | ($cs|length) as $tot
  | (($cs|map(select(.state.waiting!=null))|map(.state.waiting.reason))|join(",")) as $wait
  | (($cs|map(.restartCount)|add)//0) as $rs
  # Succeeded = completed one-shot Job/pod → NOT unhealthy. Only flag live trouble.
  | select( ($ph=="Failed" or $ph=="Unknown" or $ph=="Pending")
            or ($ph=="Running" and $rdy<$tot)
            or ($wait!="") )
  | "  ❌ \($ph)  \(.metadata.namespace)/\(.metadata.name)  ready=\($rdy)/\($tot) restarts=\($rs) \($wait)"' | sort)
if [ -n "$bad" ]; then echo "$bad"; else echo "  ✅ no unhealthy pods (completed Jobs correctly ignored)"; fi

# ---- 5. Certificates -------------------------------------------------------
echo
echo "== 5. Certificates ========================================================"
badc=$(KC get certificate -A -o json 2>/dev/null | jq -r '
  .items[] | select((([.status.conditions[]?|select(.type=="Ready")][0].status)//"?")!="True")
  | "  ❌ \(.metadata.namespace)/\(.metadata.name)"')
if [ -n "$badc" ]; then echo "$badc"; else echo "  ✅ all Certificates Ready"; fi

# ---- 6. Recent Warning events ---------------------------------------------
echo
echo "== 6. Warning events, last ${WARN_MIN}m (transient roll churn is expected) =="
KC get events -A --field-selector type=Warning -o json 2>/dev/null | jq -r --argjson mins "$WARN_MIN" '
  [ .items[]
    | select(.lastTimestamp != null)
    | select((.lastTimestamp|fromdateiso8601) > (now - ($mins*60)))
    | "  \(.lastTimestamp)  \(.metadata.namespace)  \(.reason)  \(.involvedObject.kind)/\(.involvedObject.name)  \((.message//"")[0:64])" ]
  | (if length==0 then "  ✅ none in window" else (sort | .[-25:][]) end)'

echo
echo "Verdict: green when Flux is caught up, all Kustomizations + HelmReleases Ready,"
echo "no unhealthy pods, all certs Ready, and any warnings trace to terminating pods."
