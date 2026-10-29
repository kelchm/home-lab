#!/usr/bin/env bash
# Only the main publisher calls this, after qualifying the exact returned digest.
set -euo pipefail
image=${1:?image repository}
digest=${2:?root digest}
[[ $digest =~ ^sha256:[a-f0-9]{64}$ ]]
[[ ${GITHUB_EVENT_NAME:-} == push || ${GITHUB_EVENT_NAME:-} == workflow_dispatch ]]
[[ ${GITHUB_REF:-} == refs/heads/main ]]

# GitHub job ordering is not dispatch ordering. Compare only image inputs;
# qualification/tooling edits don't obsolete an equivalent binary.
git fetch --no-tags origin main
mapfile -t paths < <(python3 scripts/ci/hermes-caddy-inputs.py paths --publish)
diff_status=0
git diff --quiet HEAD FETCH_HEAD -- "${paths[@]}" || diff_status=$?
if (( diff_status > 1 )); then
    echo '::error::git diff failed while comparing image inputs.' >&2
    exit "$diff_status"
fi
if (( diff_status == 1 )); then
    echo '::warning::Image inputs changed on main; this unique build was not promoted. If no newer build completes, dispatch Hermes Caddy Image from main.'
    if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
        printf '%s\n' 'Image inputs changed on main; candidate was left unchanged. If no newer publisher completes, run **Hermes Caddy Image** with **Run workflow** on **main** to repair it.' >> "$GITHUB_STEP_SUMMARY"
    fi
    exit 0
fi

# Preflight the exact root copy before mutating candidate. A single-source
# index is a carbon copy; --prefer-index=false also preserves a single manifest.
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
docker buildx imagetools inspect --raw "$image@$digest" > "$scratch/source.json"
[[ sha256:$(sha256sum "$scratch/source.json" | cut -d ' ' -f 1) == "$digest" ]]
docker buildx imagetools create --dry-run --prefer-index=false "$image@$digest" > "$scratch/copy.json"
# Buildx v0.38.1 dry-run appends one display newline; inspect --raw does not.
python3 - "$scratch/source.json" "$scratch/copy.json" <<'PY'
import pathlib, sys
assert pathlib.Path(sys.argv[2]).read_bytes() == pathlib.Path(sys.argv[1]).read_bytes() + b'\n', 'root copy would change the manifest'
PY
docker buildx imagetools create --prefer-index=false --tag "$image:candidate" "$image@$digest"
docker buildx imagetools inspect --raw "$image:candidate" > "$scratch/candidate.json"
cmp "$scratch/source.json" "$scratch/candidate.json"
echo "Candidate now names the qualified root $digest"
