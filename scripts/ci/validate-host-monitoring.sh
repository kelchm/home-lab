#!/usr/bin/env bash
# Render every host monitoring deployment the way doco-cd would and parse its scrape configuration with the pinned vmagent.

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

violations=0

function violation() {
    echo "$1" >&2
    violations=$((violations + 1))
}

function validate_deployment() {
    local config="$1" deployment="$2"
    local name working_dir label rendered image
    local -a compose_args=() mounts=()

    name="$(jq -r '.name // ""' <<<"${deployment}")"
    working_dir="$(jq -r '.working_dir // ""' <<<"${deployment}")"
    label="${config#"${ROOT_DIR}/"} deployment ${name:-<unnamed>}"

    if [[ -z "${name}" || -z "${working_dir}" ]]; then
        violation "${label}: name and working_dir must be set"
        return
    fi

    while IFS= read -r file; do
        if [[ ! -f "${ROOT_DIR}/${working_dir}/${file}" ]]; then
            violation "${label}: ${working_dir}/${file} does not exist"
            return
        fi
        compose_args+=(-f "${ROOT_DIR}/${working_dir}/${file}")
    done < <(jq -r '(.compose_files // ["compose.yaml"])[]' <<<"${deployment}")

    if ! rendered="$(docker compose "${compose_args[@]}" config --format json)"; then
        violation "${label}: Compose render failed"
        return
    fi
    if [[ "$(jq -r '.name' <<<"${rendered}")" != "${name}" ]]; then
        violation "${label}: Compose project name differs from the deployment name"
    fi

    image="$(jq -r '.services.vmagent.image // ""' <<<"${rendered}")"
    if [[ -n "${image}" ]]; then
        while IFS= read -r mount; do
            mounts+=(--volume "${mount}:ro")
        done < <(jq -r '.services.vmagent.volumes[] | select(.type == "bind" and (.target | startswith("/etc/vmagent/"))) | "\(.source):\(.target)"' <<<"${rendered}")
        if ! docker run --rm --network none "${mounts[@]}" "${image}" \
            -promscrape.config=/etc/vmagent/scrape.yaml -promscrape.config.dryRun >/dev/null 2>&1; then
            violation "${label}: vmagent rejected the scrape configuration"
            return
        fi
    fi

    echo "ok: ${label}"
}

configs="$(find "${ROOT_DIR}/sparks/monitoring" "${ROOT_DIR}/proxmox/monitoring" -name '.doco-cd.yml' | sort)"
if [[ -z "${configs}" ]]; then
    violation "no .doco-cd.yml found under sparks/monitoring or proxmox/monitoring"
fi

while IFS= read -r config; do
    if [[ -z "${config}" ]]; then
        continue
    fi
    if ! deployments="$(yq -o=json -I=0 '.' "${config}")" || [[ -z "${deployments}" ]]; then
        violation "${config#"${ROOT_DIR}/"}: not valid YAML or empty"
        continue
    fi
    while IFS= read -r deployment; do
        validate_deployment "${config}" "${deployment}"
    done <<<"${deployments}"
done <<<"${configs}"

for deployer in sparks proxmox; do
    if ! HOST=validate docker compose -f "${ROOT_DIR}/${deployer}/platform/doco-cd/compose.yaml" config --quiet; then
        violation "${deployer}/platform/doco-cd/compose.yaml: Compose render failed"
    fi
done

if ((violations > 0)); then
    echo "host monitoring validation failed with ${violations} violation(s)" >&2
    exit 1
fi
echo "host monitoring projects OK"
