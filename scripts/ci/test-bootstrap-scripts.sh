#!/usr/bin/env bash
# Fixture tests for the fail-closed paths in scripts/lib/common.sh,
# scripts/bootstrap-apps.sh and scripts/synology/apply-media-acls.sh.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "${ROOT}"

failures=0
fail() {
    echo "FAIL: $*" >&2
    failures=$((failures + 1))
}

# --- log levels ---------------------------------------------------------
if bash -c 'source scripts/lib/common.sh; log fatal "boom"' >/dev/null 2>&1; then
    fail "log accepted the unknown level 'fatal'"
fi
if ! bash -c 'source scripts/lib/common.sh; log info "ok"' >/dev/null 2>&1; then
    fail "log rejected the level 'info'"
fi
if bash -c 'source scripts/lib/common.sh; log error "boom"' >/dev/null 2>&1; then
    fail "log error did not exit non-zero"
fi

# --- node readiness -----------------------------------------------------
nodes_json() {
    # Each argument is one node's Ready status; "none" omits the condition.
    local items=()
    for status in "$@"; do
        if [[ "${status}" == "none" ]]; then
            items+=('{"status":{"conditions":[]}}')
        else
            items+=("{\"status\":{\"conditions\":[{\"type\":\"Ready\",\"status\":\"${status}\"}]}}")
        fi
    done
    local IFS=,
    echo "{\"items\":[${items[*]}]}"
}

# shellcheck source=scripts/bootstrap-apps.sh
source scripts/bootstrap-apps.sh

expect_ready() {
    local -r want="$1" expected="$2"
    shift 2
    local got=not-ready
    if nodes_json "$@" | node_readiness "${expected}" >/dev/null; then
        got=ready
    fi
    [[ "${got}" == "${want}" ]] || fail "node_readiness expected=${expected} nodes=[$*] was ${got}, want ${want}"
}

expect_ready not-ready 3
expect_ready not-ready 3 False False
expect_ready ready 3 False False False
expect_ready ready 3 True False True
expect_ready ready 3 True True True
expect_ready not-ready 3 True True Unknown
expect_ready not-ready 3 True True none
expect_ready ready 2 False False
if echo 'not json' | node_readiness 3 >/dev/null 2>&1; then
    fail "node_readiness accepted unparseable input"
fi

# --- ACL verification ---------------------------------------------------
fixtures="$(mktemp -d)"
trap 'rm -rf "${fixtures}"' EXIT

cat >"${fixtures}/synoacltool" <<'STUB'
#!/bin/sh
# synoacltool -get PATH: print the fixture for PATH.
cat "${ACL_FIXTURES}/$(printf '%s' "$2" | tr '/' '_')"
STUB
chmod +x "${fixtures}/synoacltool"

acl() {
    # acl PATH ARCHIVE ACE... : write a `synoacltool -get` fixture.
    local path="$1" archive="$2" index=0
    shift 2
    {
        printf 'ACL version: 1 \nArchive: %s \nOwner: [root(user)] \n--------------------- \n' "${archive}"
        for ace in "$@"; do
            printf '\t [%d] %s\n' "${index}" "${ace}"
            index=$((index + 1))
        done
    } >"${fixtures}/$(printf '%s' "${path}" | tr '/' '_')"
}

mgr="rwxp-DaARWc--"
rdr="r-x---a-R-c--"
inherited=("group:administrators:allow:${mgr}:fd-- (level:1)" "user:jellyfin:allow:${rdr}:fd-- (level:1)")

good_fixtures() {
    acl /volume1/media "is_inherit,has_ACL,is_support_ACL" \
        "group:administrators:allow:${mgr}:fd-- (level:0)" \
        "user:jellyfin:allow:${rdr}:fd-- (level:0)" \
        "group:media:allow:${rdr}:---n (level:0)" \
        "everyone::allow:${rdr}:fd-- (level:1)"
    acl /volume1/media/tv "is_inherit,has_ACL,is_support_ACL" \
        "user:sonarr:allow:${mgr}:fd-- (level:0)" "user:bazarr:allow:${mgr}:fd-- (level:0)" "${inherited[@]}"
    acl /volume1/media/movies "is_inherit,has_ACL,is_support_ACL" \
        "user:radarr:allow:${mgr}:fd-- (level:0)" "user:bazarr:allow:${mgr}:fd-- (level:0)" "${inherited[@]}"
    acl /volume1/media/music "is_inherit,has_ACL,is_support_ACL" \
        "user:lidarr:allow:${mgr}:fd-- (level:0)" "${inherited[@]}"
    acl /volume1/media/.downloads "is_inherit,has_ACL,is_support_ACL" \
        "user:jellyfin:deny:rwxpdDaARWcCo:fd-- (level:0)" "group:media:allow:${mgr}:fd-- (level:0)" "${inherited[@]}"
    acl /volume1/media/books "is_inherit,is_support_ACL" "${inherited[@]}"
    acl /volume1/media/audiobooks "is_inherit,is_support_ACL" "${inherited[@]}"
}

verify() {
    PATH="${fixtures}:${PATH}" ACL_FIXTURES="${fixtures}" APPLY_MEDIA_ACLS_SOURCE_ONLY=1 \
        sh -c '. scripts/synology/apply-media-acls.sh; verify_final_state' >/dev/null 2>&1
}

good_fixtures
verify || fail "ACL verification rejected the target state"

good_fixtures
acl /volume1/media/tv "is_inherit,has_ACL,is_support_ACL" \
    "user:sonarr:allow:${mgr}:fd-- (level:0)" "${inherited[@]}"
verify && fail "ACL verification accepted a missing local ACE"

good_fixtures
acl /volume1/media/music "is_inherit,has_ACL,is_support_ACL" \
    "user:lidarr:allow:${mgr}:fd-- (level:0)" "user:radarr:allow:${mgr}:fd-- (level:0)" "${inherited[@]}"
verify && fail "ACL verification accepted an extra local ACE"

good_fixtures
acl /volume1/media/books "is_support_ACL"
verify && fail "ACL verification accepted a child that does not inherit"

good_fixtures
acl /volume1/media/.downloads "is_inherit,has_ACL,is_support_ACL" \
    "group:media:allow:${mgr}:fd-- (level:0)" "user:jellyfin:deny:rwxpdDaARWcCo:fd-- (level:0)" "${inherited[@]}"
verify && fail "ACL verification accepted an allow entry before a deny entry"

if ((failures > 0)); then
    echo "bootstrap script tests failed: ${failures}" >&2
    exit 1
fi
echo "bootstrap script tests OK"
