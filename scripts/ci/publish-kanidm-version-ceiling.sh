#!/usr/bin/env bash
# Rebuild the ceiling PR from current main. Only replace the observed branch
# head, and refuse to discard edits outside the ceiling managed by the sync.

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly BRANCH="chore/kanidm-ceiling-sync"
readonly BRANCH_REF="refs/heads/${BRANCH}"
readonly TITLE="chore(renovate): sync kanidm/server ceiling with kaniop SDK"
cd "${ROOT_DIR}"

function fail() {
    echo "ERROR: $*" >&2
    exit 1
}

# A queued run's event SHA may be old. Use the latest main for both the pinned
# kaniop input and Renovate settings, without touching the caller's index/files.
git fetch --no-tags origin +refs/heads/main:refs/remotes/origin/main
main_head="$(git rev-parse refs/remotes/origin/main)"
pr_head="$(gh pr list --base main --head "${BRANCH}" --state open \
    --json headRefOid --jq '.[].headRefOid')"
remote_head="$(git ls-remote --heads origin "${BRANCH_REF}" | cut -f1)"
if [[ -n "${pr_head}" && "${pr_head}" != "${remote_head}" ]]; then
    fail "sync PR head and remote branch disagree; retry after inspecting the branch"
fi

if [[ -n "${remote_head}" ]]; then
    git fetch --no-tags origin "${BRANCH_REF}"
    [[ "$(git rev-parse FETCH_HEAD)" == "${remote_head}" ]] || fail "sync branch changed during fetch"
    branch_base="$(git merge-base "${main_head}" "${remote_head}")"
    changed_files="$(git diff --name-only "${branch_base}" "${remote_head}")"
    [[ -z "${changed_files}" || "${changed_files}" == .renovaterc.json5 ]] || \
        fail "sync branch contains changes outside .renovaterc.json5; preserve them for manual review"
fi

readonly TEMP_DIR="$(mktemp -d)"
readonly WORKTREE="${TEMP_DIR}/worktree"
function cleanup() {
    git worktree remove --force "${WORKTREE}" >/dev/null 2>&1 || true
    rm -rf -- "${TEMP_DIR}"
}
trap cleanup EXIT
git worktree add --detach "${WORKTREE}" "${main_head}"

function sync_file() {
    RENOVATERC_OVERRIDE="$1" "${WORKTREE}/scripts/ci/sync-kanidm-version-ceiling.sh"
}
sync_file "${WORKTREE}/.renovaterc.json5"

if [[ -n "${remote_head}" ]]; then
    git show "${branch_base}:.renovaterc.json5" >"${TEMP_DIR}/base.json5"
    git show "${remote_head}:.renovaterc.json5" >"${TEMP_DIR}/head.json5"
    cp "${TEMP_DIR}/head.json5" "${TEMP_DIR}/original-head.json5"
    # Normalizing both files with the existing parser removes only its managed
    # ceiling. Any remaining difference is an unexpected edit, even in the same
    # Renovate file. Do not silently overwrite it with main's version.
    sync_file "${TEMP_DIR}/base.json5"
    sync_file "${TEMP_DIR}/head.json5"
    cmp -s "${TEMP_DIR}/base.json5" "${TEMP_DIR}/head.json5" || \
        fail "sync branch contains edits beyond the managed ceiling; preserve them for manual review"
fi

if [[ -z "${pr_head}" ]] && git -C "${WORKTREE}" diff --quiet -- .renovaterc.json5; then
    echo "main's ceiling is already in sync; nothing to publish"
    exit 0
fi

if [[ -n "${remote_head}" ]] && cmp -s "${WORKTREE}/.renovaterc.json5" "${TEMP_DIR}/original-head.json5"; then
    echo "sync branch already has the desired result; no commit or push needed"
else
    git -C "${WORKTREE}" add -- .renovaterc.json5
    # Main may already contain the desired ceiling while an open PR is stale.
    # In that case move the branch to main without manufacturing an empty commit.
    if ! git -C "${WORKTREE}" diff --cached --quiet; then
        git -C "${WORKTREE}" \
            -c user.name='github-actions[bot]' \
            -c user.email='github-actions[bot]@users.noreply.github.com' \
            commit -m "${TITLE}"
    fi
    # An empty expected head requires the branch to be absent. An explicit SHA
    # protects existing branches even if a background fetch updates local refs.
    git -C "${WORKTREE}" push "--force-with-lease=${BRANCH_REF}:${remote_head}" \
        origin "HEAD:${BRANCH_REF}"
fi

if [[ -z "${pr_head}" ]]; then
    gh pr create --base main --head "${BRANCH}" --title "${TITLE}" \
        --body "Automated: the pinned kaniop release bundles a kanidm_client SDK whose minor no longer matches the Renovate allowedVersions ceiling for kanidm/server. See scripts/ci/sync-kanidm-version-ceiling.sh and #311."
else
    echo "updated the existing sync PR"
fi
