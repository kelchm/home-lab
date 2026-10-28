#!/usr/bin/env bash
# Exercise the real publisher and sync parser against disposable local remotes.
# Only GitHub PR metadata and the upstream Cargo.lock download are mocked.
# An optional publisher path lets the same regression probe the old workflow.

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly PUBLISHER="${1:-${ROOT_DIR}/scripts/ci/publish-kanidm-version-ceiling.sh}"
readonly TEMP_DIR="$(mktemp -d)"
readonly BRANCH="chore/kanidm-ceiling-sync"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

# Keep fixtures independent of the operator's signing, hooks and credentials.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_ALLOW_PROTOCOL=file
export GIT_AUTHOR_NAME=Test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=Test GIT_COMMITTER_EMAIL=test@example.invalid
unset RENOVATERC_OVERRIDE KANIOP_LOCKFILE_OVERRIDE
mkdir "${TEMP_DIR}/bin"
export PATH="${TEMP_DIR}/bin:${PATH}"

cat >"${TEMP_DIR}/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >>"${TEST_CASE}/gh.log"
case "$*" in
    'pr list '*--json\ headRefOid*)
        if [[ -e "${TEST_CASE}/pr-open" ]]; then
            git --git-dir="${TEST_CASE}/remote.git" rev-parse refs/heads/chore/kanidm-ceiling-sync
        fi
        ;;
    'pr list '*--json\ number*) # Original workflow's query, for the negative probe.
        if [[ -e "${TEST_CASE}/pr-open" ]]; then echo 1; else echo 0; fi
        ;;
    'pr create '*)
        [[ ! -e "${TEST_CASE}/pr-open" ]] || { echo 'duplicate PR creation' >&2; exit 1; }
        touch "${TEST_CASE}/pr-open"
        echo 'https://example.invalid/pull/1'
        ;;
    *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
EOF

cat >"${TEMP_DIR}/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$#" == 4 && "$1" == -fsSL && "$2" == -o ]]
case "$4" in
    https://raw.githubusercontent.com/pando85/kaniop/v1.0.0/Cargo.lock) sdk=1.11.1 ;;
    https://raw.githubusercontent.com/pando85/kaniop/v2.0.0/Cargo.lock) sdk=1.12.1 ;;
    *) echo "unexpected download: $4" >&2; exit 1 ;;
esac
printf '[[package]]\nname = "kanidm_client"\nversion = "%s"\n' "${sdk}" >"$3"
# Introduce a real concurrent remote write after the publisher observed/fetched
# its head but before push. A blind force push would erase this new commit.
if [[ -e "${TEST_CASE}/race-head" ]]; then
    git --git-dir="${TEST_CASE}/remote.git" update-ref refs/heads/chore/kanidm-ceiling-sync "$(cat "${TEST_CASE}/race-head")"
    rm "${TEST_CASE}/race-head"
fi
EOF
chmod +x "${TEMP_DIR}/bin/gh" "${TEMP_DIR}/bin/curl"

function fail() {
    echo "FAIL (${TEST_CASE##*/}): $*" >&2
    cat "${TEST_CASE}/publish.log" >&2
    exit 1
}

function fixture() {
    export TEST_CASE="${TEMP_DIR}/$1"
    mkdir -p "${TEST_CASE}/seed/scripts/ci" "${TEST_CASE}/seed/kubernetes/apps/identity/kaniop/app"
    git init --quiet --bare "${TEST_CASE}/remote.git"
    git init --quiet --initial-branch=main "${TEST_CASE}/seed"
    cp "${ROOT_DIR}/scripts/ci/sync-kanidm-version-ceiling.sh" "${TEST_CASE}/seed/scripts/ci/"
    cat >"${TEST_CASE}/seed/.renovaterc.json5" <<'EOF'
{
  packageRules: [
    {
      matchPackageNames: ["valkey/valkey"],
      allowedVersions: "<8.0.0",
    },
    {
      matchPackageNames: ["/kanidm/server$/"],
      allowedVersions: "<1.10.0",
    },
  ],
}
EOF
    printf '  ref:\n    tag: 1.0.0\n' >"${TEST_CASE}/seed/kubernetes/apps/identity/kaniop/app/ocirepository.yaml"
    echo 'unrelated tracked content' >"${TEST_CASE}/seed/README.md"
    git -C "${TEST_CASE}/seed" add .
    git -C "${TEST_CASE}/seed" commit --quiet -m 'fixture main'
    git -C "${TEST_CASE}/seed" remote add origin "${TEST_CASE}/remote.git"
    git -C "${TEST_CASE}/seed" push --quiet origin main
    git clone --quiet --branch main "${TEST_CASE}/remote.git" "${TEST_CASE}/runner"
    cp "${PUBLISHER}" "${TEST_CASE}/runner/scripts/ci/publish-kanidm-version-ceiling.sh"
    cat >"${TEST_CASE}/runner/.git/hooks/pre-push" <<'EOF'
#!/usr/bin/env bash
echo push >>"${TEST_CASE}/push.log"
EOF
    chmod +x "${TEST_CASE}/runner/.git/hooks/pre-push"
    : >"${TEST_CASE}/gh.log"
    : >"${TEST_CASE}/push.log"
    : >"${TEST_CASE}/publish.log"
}

function seed_commit() {
    git -C "${TEST_CASE}/seed" add .
    git -C "${TEST_CASE}/seed" commit --quiet -m "$1"
    git -C "${TEST_CASE}/seed" push --quiet origin "HEAD:$2"
}

function open_branch() {
    git -C "${TEST_CASE}/seed" checkout --quiet -b "${BRANCH}"
    sed -i.bak 's/<1.10.0/<1.11.0/' "${TEST_CASE}/seed/.renovaterc.json5"
    rm "${TEST_CASE}/seed/.renovaterc.json5.bak"
    seed_commit 'older automated ceiling' "${BRANCH}"
    touch "${TEST_CASE}/pr-open"
}

function publish() {
    (cd "${TEST_CASE}/runner" && bash scripts/ci/publish-kanidm-version-ceiling.sh) >"${TEST_CASE}/publish.log" 2>&1
}

function head_for() {
    git --git-dir="${TEST_CASE}/remote.git" rev-parse "refs/heads/$1"
}

function assert_result() {
    local expected="$1"
    git --git-dir="${TEST_CASE}/remote.git" show main:.renovaterc.json5 | \
        sed "s/<1.10.0/${expected}/" >"${TEST_CASE}/expected.json5"
    git --git-dir="${TEST_CASE}/remote.git" show "${BRANCH}:.renovaterc.json5" >"${TEST_CASE}/published.json5"
    cmp -s "${TEST_CASE}/expected.json5" "${TEST_CASE}/published.json5" || fail 'published Renovate result is wrong'
    [[ "$(git --git-dir="${TEST_CASE}/remote.git" diff --name-only main "${BRANCH}")" == .renovaterc.json5 ]] || fail 'unexpected files in PR'
}

function assert_no_create() {
    if grep -q '^pr create ' "${TEST_CASE}/gh.log"; then fail 'created another PR'; fi
}

# The original workflow exits here and leaves <1.11.0 on the already-open PR.
# Include both staged and unstaged unrelated changes in the runner: publication
# must neither commit them nor change the caller's index or worktree.
fixture open-pr
open_branch
echo staged >"${TEST_CASE}/runner/README.md"
git -C "${TEST_CASE}/runner" add README.md
index_before="$(git -C "${TEST_CASE}/runner" write-tree)"
echo unstaged >"${TEST_CASE}/runner/README.md"
old_head="$(head_for "${BRANCH}")"
publish || fail 'existing PR update failed'
assert_result '<1.12.0'
[[ "$(head_for "${BRANCH}")" != "${old_head}" ]] || fail 'open PR did not advance'
assert_no_create
[[ "$(git -C "${TEST_CASE}/runner" write-tree)" == "${index_before}" ]] || fail 'caller index changed'
[[ "$(cat "${TEST_CASE}/runner/README.md")" == unstaged ]] || fail 'caller worktree changed'
echo 'PASS: existing PR updated; unrelated staged/unstaged changes preserved'

old_head="$(head_for "${BRANCH}")"
cp "${TEST_CASE}/push.log" "${TEST_CASE}/push-before.log"
publish || fail 'idempotent run failed'
[[ "$(head_for "${BRANCH}")" == "${old_head}" ]] || fail 'unchanged result churned commits'
cmp -s "${TEST_CASE}/push.log" "${TEST_CASE}/push-before.log" || fail 'unchanged result pushed'
assert_no_create
echo 'PASS: unchanged desired PR result causes no commit, push or PR creation'

fixture fresh-main
open_branch
git -C "${TEST_CASE}/seed" checkout --quiet main
printf '  ref:\n    tag: 2.0.0\n' >"${TEST_CASE}/seed/kubernetes/apps/identity/kaniop/app/ocirepository.yaml"
sed -i.bak 's/<8.0.0/<7.0.0/' "${TEST_CASE}/seed/.renovaterc.json5"
rm "${TEST_CASE}/seed/.renovaterc.json5.bak"
echo 'new main content' >"${TEST_CASE}/seed/README.md"
seed_commit 'new main inputs and unrelated settings' main
publish || fail 'fresh main update failed'
assert_result '<1.13.0'
[[ "$(git --git-dir="${TEST_CASE}/remote.git" rev-parse "${BRANCH}^")" == "$(head_for main)" ]] || fail 'did not rebuild on fresh main'
assert_no_create
echo 'PASS: stale checkout uses current main pin and preserves current unrelated settings'

fixture first-pr
publish || fail 'first PR publication failed'
assert_result '<1.12.0'
[[ -e "${TEST_CASE}/pr-open" ]] || fail 'no PR created'
[[ "$(grep -c '^pr create ' "${TEST_CASE}/gh.log")" == 1 ]] || fail 'expected exactly one PR'
grep -q -- '--base main --head chore/kanidm-ceiling-sync' "${TEST_CASE}/gh.log" || fail 'PR has wrong base/head'
old_head="$(head_for "${BRANCH}")"
# Recover the push-succeeded/create-failed window without rewriting the branch.
rm "${TEST_CASE}/pr-open"
publish || fail 'PR creation retry failed'
[[ "$(head_for "${BRANCH}")" == "${old_head}" ]] || fail 'PR retry rewrote branch'
[[ "$(wc -l <"${TEST_CASE}/push.log" | tr -d ' ')" == 1 ]] || fail 'PR retry pushed again'
echo 'PASS: first PR path and PR-creation retry reuse the intended branch'

fixture main-in-sync
sed -i.bak 's/<1.10.0/<1.12.0/' "${TEST_CASE}/seed/.renovaterc.json5"
rm "${TEST_CASE}/seed/.renovaterc.json5.bak"
seed_commit 'main already in sync' main
publish || fail 'main no-op failed'
[[ ! -e "${TEST_CASE}/pr-open" && ! -s "${TEST_CASE}/push.log" ]] || fail 'in-sync main published something'
if git --git-dir="${TEST_CASE}/remote.git" show-ref --verify --quiet "refs/heads/${BRANCH}"; then fail 'in-sync main created a branch'; fi
echo 'PASS: in-sync main creates no branch or PR'

fixture stale-pr-main-in-sync
open_branch
git -C "${TEST_CASE}/seed" checkout --quiet main
sed -i.bak 's/<1.10.0/<1.12.0/' "${TEST_CASE}/seed/.renovaterc.json5"
rm "${TEST_CASE}/seed/.renovaterc.json5.bak"
seed_commit 'main already has the desired ceiling' main
publish || fail 'obsolete PR refresh failed'
[[ "$(head_for "${BRANCH}")" == "$(head_for main)" ]] || fail 'obsolete PR was not moved to main without an empty commit'
assert_no_create
echo 'PASS: stale PR refreshes even when main already has the desired ceiling'

for edit in other-file same-file; do
    fixture "unexpected-${edit}"
    open_branch
    if [[ "${edit}" == other-file ]]; then
        echo 'human edit' >"${TEST_CASE}/seed/README.md"
    else
        sed -i.bak 's/<8.0.0/<7.0.0/' "${TEST_CASE}/seed/.renovaterc.json5"
        rm "${TEST_CASE}/seed/.renovaterc.json5.bak"
    fi
    seed_commit 'unexpected human change' "${BRANCH}"
    old_head="$(head_for "${BRANCH}")"
    if publish; then fail 'unexpected branch edits were overwritten'; fi
    grep -q 'ERROR: sync branch contains' "${TEST_CASE}/publish.log" || fail 'failed for an unrelated reason'
    [[ "$(head_for "${BRANCH}")" == "${old_head}" && ! -s "${TEST_CASE}/push.log" ]] || fail 'unexpected branch was changed'
    assert_no_create
    echo "PASS: unexpected ${edit} edits refuse safely"
done

fixture head-race
open_branch
echo 'concurrent edit' >"${TEST_CASE}/seed/README.md"
seed_commit 'concurrent remote commit' race-candidate
race_head="$(head_for race-candidate)"
echo "${race_head}" >"${TEST_CASE}/race-head"
if publish; then fail 'concurrent remote commit was overwritten'; fi
grep -q 'stale info' "${TEST_CASE}/publish.log" || fail 'push did not reject the stale lease'
[[ "$(head_for "${BRANCH}")" == "${race_head}" ]] || fail 'concurrent remote commit was lost'
assert_no_create
echo 'PASS: real remote race rejects stale lease and preserves the concurrent commit'

fixture first-branch-race
echo 'concurrent branch creation' >"${TEST_CASE}/seed/README.md"
seed_commit 'concurrent branch commit' race-candidate
race_head="$(head_for race-candidate)"
echo "${race_head}" >"${TEST_CASE}/race-head"
if publish; then fail 'concurrently created branch was overwritten'; fi
grep -q 'stale info' "${TEST_CASE}/publish.log" || fail 'push did not require the branch to be absent'
[[ "$(head_for "${BRANCH}")" == "${race_head}" ]] || fail 'concurrently created branch was lost'
assert_no_create
echo 'PASS: first publication refuses to replace a concurrently created branch'

echo 'Kanidm ceiling publishing regression tests passed.'
