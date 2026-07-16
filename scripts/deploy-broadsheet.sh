#!/usr/bin/env bash
# deploy-broadsheet.sh — point the broadsheet HelmRelease at a published image
# and let Flux roll it out. One-shot; default target is the latest broadsheet
# main commit (image tag sha-<short>).
#
# Usage:
#   scripts/deploy-broadsheet.sh                 # deploy latest broadsheet main
#   scripts/deploy-broadsheet.sh sha-83262ea     # deploy an explicit image tag
#   scripts/deploy-broadsheet.sh 83262ea         # (the sha- prefix is optional)
#   scripts/deploy-broadsheet.sh --dry-run       # show what it would do, change nothing
#
# It resolves the tag, WAITS for that image to be published to GHCR (the
# broadsheet CI builds multi-arch, ~15 min), bumps the pin in helmrelease.yaml,
# commits + pushes to main (direct-to-main: a single-app config bump), forces a
# Flux reconcile, and waits for the rollout. The Longhorn data volume is
# untouched — Recreate only swaps the pod, so expect a brief blip, not data loss.
#
# Env:
#   BROADSHEET_REPO   broadsheet checkout (default: ~/Development/kelchm/broadsheet)
#   IMAGE_WAIT_SECS   how long to wait for the image to publish (default: 1200)
set -Eeuo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

# --- constants ------------------------------------------------------------
readonly IMAGE="ghcr.io/kelchm/broadsheet"
readonly REG_REPO="kelchm/broadsheet"       # path in the GHCR registry API
readonly HR_FILE="kubernetes/apps/iot/broadsheet/app/helmrelease.yaml"
readonly NS="iot"
readonly DEPLOY="broadsheet"
readonly KUSTOMIZATION="broadsheet"          # Flux Kustomization (ns iot)
readonly GITREPO="flux-system"               # Flux GitRepository (ns flux-system)
BROADSHEET_REPO="${BROADSHEET_REPO:-$HOME/Development/kelchm/broadsheet}"
IMAGE_WAIT_SECS="${IMAGE_WAIT_SECS:-1200}"

DRY_RUN=false
ARG=""
for a in "$@"; do
    case "$a" in
        --dry-run) DRY_RUN=true ;;
        -*) log error "unknown flag: $a"; exit 2 ;;
        *) ARG="$a" ;;
    esac
done

# --- run from the repo root, with mise's env (KUBECONFIG, flux, kubectl) ---
ROOT_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT_DIR"
if command -v mise >/dev/null 2>&1; then
    eval "$(mise env)"
fi
for bin in flux kubectl git curl python3; do
    command -v "$bin" >/dev/null 2>&1 || { log error "missing required tool: $bin"; exit 1; }
done

# --- resolve the target image tag -----------------------------------------
if [[ -n "$ARG" ]]; then
    TAG="sha-${ARG#sha-}"
else
    [[ -d "$BROADSHEET_REPO/.git" ]] || { log error "broadsheet repo not at $BROADSHEET_REPO (set BROADSHEET_REPO)"; exit 1; }
    git -C "$BROADSHEET_REPO" fetch -q origin main
    full="$(git -C "$BROADSHEET_REPO" rev-parse origin/main)"
    TAG="sha-${full:0:7}"   # matches docker/metadata-action type=sha,format=short
fi
log info "Target: ${IMAGE}:${TAG}"

CURRENT="$(grep -oE 'tag: sha-[0-9a-f]+' "$HR_FILE" | awk '{print $2}' || true)"
log info "Currently pinned: ${CURRENT:-<none>}"

# --- wait for the image to exist in GHCR (anonymous pull token) ------------
manifest_exists() {
    local tok code
    tok="$(curl -fsS "https://ghcr.io/token?scope=repository:${REG_REPO}:pull&service=ghcr.io" \
        | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')" || return 2
    code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${tok}" \
        -H 'Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json' \
        "https://ghcr.io/v2/${REG_REPO}/manifests/${1}")"
    [[ "$code" == "200" ]]
}
log info "Waiting for ${IMAGE}:${TAG} to be published (up to ${IMAGE_WAIT_SECS}s)…"
deadline=$(( $(date +%s) + IMAGE_WAIT_SECS ))
until manifest_exists "$TAG"; do
    (( $(date +%s) < deadline )) || { log error "image ${TAG} not published within ${IMAGE_WAIT_SECS}s"; exit 1; }
    sleep 20
done
log info "Image published ✓"

# --- bump the pin ----------------------------------------------------------
if [[ "$CURRENT" == "$TAG" ]]; then
    log info "Pin already at ${TAG}; skipping commit, verifying rollout only."
else
    if $DRY_RUN; then
        log warn "[dry-run] would set ${HR_FILE} tag: ${CURRENT:-<none>} -> ${TAG}, commit, and push to main."
    else
        sed -i.bak -E "s/(^[[:space:]]*tag:[[:space:]]*)sha-[0-9a-f]+/\1${TAG}/" "$HR_FILE"
        rm -f "${HR_FILE}.bak"
        grep -q "tag: ${TAG}" "$HR_FILE" || { log error "failed to update tag in ${HR_FILE}"; exit 1; }
        log info "Updated ${HR_FILE}: ${CURRENT:-<none>} -> ${TAG}"
        git add "$HR_FILE"
        git commit -q -m "feat(container): update image ${IMAGE} ( ${CURRENT:-none} -> ${TAG} )"
        git push -q
        log info "Committed + pushed to main ✓"
    fi
fi

if $DRY_RUN; then
    log info "[dry-run] would: flux reconcile source/kustomization/helmrelease, then wait for rollout."
    log info "[dry-run] done."
    exit 0
fi

# --- reconcile Flux (the webhook also fires; this just makes it immediate) --
log info "Reconciling Flux…"
flux -n flux-system reconcile source git "$GITREPO"
flux -n "$NS" reconcile kustomization "$KUSTOMIZATION"
flux -n "$NS" reconcile helmrelease "$DEPLOY"

# --- wait for rollout + verify --------------------------------------------
log info "Waiting for rollout (Recreate: a brief blip is expected, data is on the PVC)…"
kubectl -n "$NS" rollout status "deploy/${DEPLOY}" --timeout=5m
running="$(kubectl -n "$NS" get deploy "$DEPLOY" -o jsonpath='{.spec.template.spec.containers[0].image}')"
[[ "$running" == "${IMAGE}:${TAG}" ]] || { log error "deployment image is ${running}, expected ${IMAGE}:${TAG}"; exit 1; }
kubectl -n "$NS" wait --for=condition=Available "deploy/${DEPLOY}" --timeout=2m >/dev/null
log info "✅ Deployed ${IMAGE}:${TAG} — deployment healthy."
