#!/usr/bin/env bash
# CLI boundary tests: no registry, Git network, credentials or Docker daemon.
set -euo pipefail
root=$(git rev-parse --show-toplevel)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/bin"
export CADDY_TEST_DIR=$scratch
export GITHUB_REF=refs/heads/main GITHUB_EVENT_NAME=push
export GITHUB_STEP_SUMMARY=$scratch/summary.md
printf '{"schemaVersion":2,"manifests":[]}' > "$scratch/source.json"
digest=$(python3 -c 'import hashlib,sys; print("sha256:"+hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$scratch/source.json")
cat > "$scratch/bin/git" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case $1 in
    fetch) [[ ${MODE:-} != fetch-fail ]] ;;
    diff)
        # Promotion uses exactly the narrow image inputs, not broad PR scope.
        [[ $* == 'diff --quiet HEAD FETCH_HEAD -- proxmox/guests/hermes-1/Dockerfile.caddy proxmox/guests/hermes-1/.dockerignore' ]]
        [[ ${MODE:-} != stale ]]
        ;;
    *) exit 99 ;;
esac
SH
cat > "$scratch/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$CADDY_TEST_DIR/docker.calls"
[[ $1 == buildx && $2 == imagetools ]]
case $3 in
    inspect)
        if [[ ${MODE:-} == source-fail ]]; then exit 1; fi
        if [[ ${MODE:-} == source-mismatch || ( $* == *':candidate'* && ${MODE:-} == result-mismatch ) ]]; then
            echo '{}'
        else
            cat "$CADDY_TEST_DIR/source.json"
        fi
        ;;
    create)
        [[ $* == *'--prefer-index=false'* ]]
        if [[ $* == *'--dry-run'* ]]; then
            if [[ ${MODE:-} == copy-mismatch ]]; then echo '{}'; else cat "$CADDY_TEST_DIR/source.json"; echo; fi
        else
            [[ $* == *'--tag ghcr.io/example/caddy:candidate'* ]]
            touch "$CADDY_TEST_DIR/promoted"
        fi
        ;;
    *) exit 99 ;;
esac
SH
# macOS local verification also exercises the exact Linux command boundary.
cat > "$scratch/bin/sha256sum" <<'SH'
#!/usr/bin/env python3
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest(), sys.argv[1])
SH
chmod +x "$scratch/bin/"*
export PATH="$scratch/bin:$PATH"
run() { bash "$root/scripts/ci/hermes-caddy-promote.sh" ghcr.io/example/caddy "$digest"; }
for mode in stale fetch-fail copy-mismatch source-fail source-mismatch; do
    export MODE=$mode
    rm -f "$scratch/promoted" "$scratch/docker.calls"
    if [[ $mode == stale ]]; then
        run > "$scratch/stale.txt"
        grep -q '::warning::' "$scratch/stale.txt"
        grep -q 'Run workflow' "$GITHUB_STEP_SUMMARY"
    elif run; then echo "Expected failure: $mode" >&2; exit 1; fi
    [[ ! -e $scratch/promoted ]]
done
export MODE=success
run
[[ -f $scratch/promoted ]]
[[ $(grep -c '^buildx imagetools create --prefer-index=false --tag' "$scratch/docker.calls") == 1 ]]
# The post-copy readback must also reject a registry result with different bytes.
export MODE=result-mismatch
if run; then echo 'Expected readback failure' >&2; exit 1; fi
export MODE=success
# Wrong run authority and malformed root fail before any tag operation.
rm -f "$scratch/promoted"
for GITHUB_REF in refs/heads/feature refs/pull/1/merge; do
    export GITHUB_REF
    if run; then exit 1; fi
    [[ ! -e $scratch/promoted ]]
done
export GITHUB_REF=refs/heads/main GITHUB_EVENT_NAME=pull_request
if run; then exit 1; fi
export GITHUB_EVENT_NAME=workflow_dispatch
if bash "$root/scripts/ci/hermes-caddy-promote.sh" ghcr.io/example/caddy not-a-digest; then exit 1; fi
run
[[ -f $scratch/promoted ]]
echo 'Promotion CLI guards passed (mock registry only).'
