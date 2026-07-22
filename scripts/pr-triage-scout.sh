#!/usr/bin/env bash
# pr-triage-scout.sh — read-only scouting snapshot for open-PR triage.
#
# Prints four things, in order:
#   1. Open-PR table       — mergeability, CI rollup, size, author, update type.
#   2. Coupling clusters   — files touched by >1 open PR. Merging one makes the
#                            others stale; merge them one at a time and expect a
#                            rebase (adjacent-line edits can even hard-conflict).
#   3. Semantic pairs      — different PRs bumping the SAME component (e.g. a chart
#                            split across a bootstrap-CRD file and its HelmRelease).
#                            These should usually land together to avoid version skew.
#   4. Merge mechanics     — branch protection, allowed merge methods, Renovate
#                            automerge scope + schedule.
#
# Everything here is read-only (gh + jq only). It changes nothing on GitHub or
# the cluster. Run it FIRST, before analysis or merging.
#
# Usage:  scripts/pr-triage-scout.sh
set -euo pipefail

command -v gh >/dev/null || { echo "gh not found on PATH" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found on PATH" >&2; exit 1; }

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
BASE=$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name)

prs=$(gh pr list --state open --limit 200 --json \
  number,title,author,isDraft,mergeable,mergeStateStatus,reviewDecision,additions,deletions,changedFiles,labels,statusCheckRollup,files)

count=$(jq 'length' <<<"$prs")
echo "############################################################"
echo "# PR triage scout — $REPO  (base: $BASE)  —  $count open PR(s)"
echo "############################################################"

if [ "$count" -eq 0 ]; then echo; echo "No open PRs. Nothing to triage."; exit 0; fi

# ---- 1. PR table -----------------------------------------------------------
# CI rollup: FAIL if any failing conclusion/state, else PENDING if anything
# unfinished, else PASS (or "-" when a PR has no checks at all).
echo
echo "== 1. Open PRs ============================================================"
{
  printf 'PR\tAUTHOR\tTYPE\tMERGEABLE\tCI\tFILES\t±LINES\tTITLE\n'
  jq -r '
    def ci:
      (.statusCheckRollup // []) as $c
      | if ($c|length)==0 then "-"
        elif any($c[]; (.conclusion // .state) as $s | $s=="FAILURE" or $s=="ERROR" or $s=="TIMED_OUT" or $s=="CANCELLED" or $s=="ACTION_REQUIRED") then "FAIL"
        elif any($c[]; (.conclusion // .state) as $s | $s=="PENDING" or $s=="IN_PROGRESS" or $s=="QUEUED" or $s=="EXPECTED" or $s==null) then "pending"
        else "pass" end;
    def utype:
      ([.labels[]?.name] | map(select(startswith("type/"))) | .[0] // "") | sub("type/";"");
    sort_by(.number)[]
    | [ "#\(.number)",
        (.author.login // "?"),
        (if .isDraft then "DRAFT" else (utype|if .=="" then "feat" else . end) end),
        (.mergeable // "?"),
        ci,
        (.changedFiles|tostring),
        "+\(.additions)/-\(.deletions)",
        (.title[0:58])
      ] | @tsv
  ' <<<"$prs"
} | column -t -s$'\t'

# ---- 2. Coupling clusters (shared files) -----------------------------------
echo
echo "== 2. Coupling: files touched by >1 open PR (merge one-at-a-time; expect rebase) =="
coupling=$(jq -r '
  [ .[] | {pr:.number} + {f:(.files[]?.path)} ]
  | group_by(.f)
  | map(select(length>1) | {file:.[0].f, prs:(map(.pr)|unique|sort)})
  | sort_by(.file)[]
  | "  \(.file)\n      ← " + (["#\(.prs[])"]|join(" "))
' <<<"$prs")
if [ -n "$coupling" ]; then echo "$coupling"; else echo "  (none — every PR touches a disjoint file set; order does not matter)"; fi

# ---- 3. Semantic pairs (same component, different PRs) ----------------------
# Heuristic: pull the dependency name out of the Renovate title and reduce it to
# its last path segment, so "…/charts/kube-prometheus-stack" and the bare
# "kube-prometheus-stack" collapse to the same key.
echo
echo "== 3. Candidate semantic pairs: same component across multiple PRs (land together) =="
pairs=$(jq -r '
  [ .[]
    | { pr:.number,
        dep:( .title
              | (capture("update (?:image|chart|tool) (?<d>[^ ]+)") // {d:null}).d
              | if .==null then null else (sub(".*/";"")|sub(".*:";"")) end ) }
    | select(.dep!=null) ]
  | group_by(.dep)
  | map(select(length>1) | {dep:.[0].dep, prs:(map(.pr)|unique|sort)})
  | sort_by(.dep)[]
  | "  \(.dep)  ← " + (["#\(.prs[])"]|join(" "))
' <<<"$prs")
if [ -n "$pairs" ]; then echo "$pairs"; echo "  (verify: same upstream release? if so merge as a set to avoid version skew)"; else echo "  (none detected by title heuristic — still eyeball the table for split releases)"; fi

# ---- 4. Merge mechanics ----------------------------------------------------
echo
echo "== 4. Merge mechanics ====================================================="
# gh api exits non-zero on a 404 "Branch not protected" (it prints the error
# JSON to stdout, so key on the exit status, not on empty output).
if prot=$(gh api "repos/$REPO/branches/$BASE/protection" 2>/dev/null); then
  echo "$prot" | jq -r '
    "  Classic branch protection on '"$BASE"':",
    "    require up-to-date branch : \(.required_status_checks.strict // false)",
    "    required checks          : \((.required_status_checks.contexts // [])|join(", ") | if .=="" then "none" else . end)",
    "    required approvals       : \(.required_pull_request_reviews.required_approving_review_count // 0)",
    "    enforce on admins        : \(.enforce_admins.enabled // false)"'
else
  # No classic protection; a repo could still gate merges via rulesets.
  rs=$(gh api "repos/$REPO/rulesets" --jq '[.[] | select(.target=="branch" and .enforcement=="active")] | length' 2>/dev/null || echo 0)
  echo "  Branch protection: NO classic protection on '$BASE' — merge any order, no required checks/reviews."
  [ "${rs:-0}" != "0" ] && echo "  ⚠ but $rs active branch ruleset(s) exist — check 'gh api repos/$REPO/rulesets' before assuming free merges."
fi
gh repo view --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed,deleteBranchOnMerge -q \
  '"  Merge methods: squash=\(.squashMergeAllowed) merge=\(.mergeCommitAllowed) rebase=\(.rebaseMergeAllowed)  autoDeleteBranch=\(.deleteBranchOnMerge)"'

rc=$(git rev-parse --show-toplevel 2>/dev/null)/renovate.json5
if [ -f "$rc" ]; then
  am=$(grep -A3 -i 'automerge' "$rc" | grep -iE 'matchManagers|automerge:' | head -3 | tr -d '",' | sed 's/^/      /')
  sched=$(grep -iE 'schedule:' "$rc" | head -1 | tr -d '",')
  echo "  Renovate: automerge is scoped (see renovate.json5); ${sched:-no explicit schedule}"
  [ -n "$am" ] && echo "$am"
fi

echo
echo "Next: analyze non-trivial PRs (Phase 1), then tier + get explicit approval before merging."
