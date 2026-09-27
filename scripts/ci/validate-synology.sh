#!/usr/bin/env bash
# Render every Synology Compose project and enforce the doco-cd target contract in synology/README.md.

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
readonly SYNOLOGY_DIR="${ROOT_DIR}/synology"
readonly DEPLOY_CONFIG="${SYNOLOGY_DIR}/.doco-cd.yml"
readonly ONE_SHOT_LABEL='cd.doco.deployment.one_shot'

violations=0
targets='[]'

function violation() {
    echo "$1" >&2
    violations=$((violations + 1))
}

function compose_name() {
    yq '.name // ""' "$1"
}

if [[ -f "${DEPLOY_CONFIG}" ]]; then
    targets="$(yq -o=json '.' "${DEPLOY_CONFIG}" | jq -s '.')"
    duplicates="$(jq -r 'group_by(.name)[] | select(length > 1) | .[0].name | tostring' <<<"${targets}")"
    rows="$(jq -r '.[] | [
        (.name | if type == "string" then . else "" end),
        (.working_dir | if type == "string" then . else "" end),
        (has("destroy") | tostring),
        (has("auto_discovery") | tostring)
    ] | join("\u001f")' <<<"${targets}")"

    while IFS= read -r duplicate; do
        if [[ -n "${duplicate}" ]]; then
            violation "synology/.doco-cd.yml: duplicate target name ${duplicate}"
        fi
    done <<<"${duplicates}"

    while IFS=$'\x1f' read -r name working_dir has_destroy has_discovery; do
        if [[ -z "${name}${working_dir}${has_destroy}" ]]; then
            continue
        fi
        label="synology/.doco-cd.yml target ${name:-<unnamed>}"

        if [[ -z "${name}" || -z "${working_dir}" ]]; then
            violation "${label}: name and working_dir must be non-empty strings"
            continue
        fi
        if [[ "${has_destroy}" == "true" || "${has_discovery}" == "true" ]]; then
            violation "${label}: destroy and auto_discovery are not allowed"
        fi
        if [[ ! "${working_dir}" =~ ^synology/[^/.][^/]*$ || "${working_dir}" == synology/platform ]]; then
            violation "${label}: working_dir must be written as synology/<project>, and <project> must not be platform"
            continue
        fi

        compose_file="${ROOT_DIR}/${working_dir}/compose.yaml"
        if [[ ! -f "${compose_file}" ]]; then
            violation "${label}: ${working_dir}/compose.yaml does not exist"
        elif [[ "$(compose_name "${compose_file}")" != "${name}" ]]; then
            violation "${label}: name must match the Compose project name in ${working_dir}/compose.yaml"
        fi
    done <<<"${rows}"
fi

while IFS= read -r -d '' compose_file; do
    relative="${compose_file#"${ROOT_DIR}/"}"
    project_dir="$(dirname "${relative}")"

    if [[ -z "$(compose_name "${compose_file}")" ]]; then
        violation "${relative}: set a top-level project name"
    fi

    # Render every profile: a target's deploy config may enable any of them.
    if ! rendered="$(docker compose --file "${compose_file}" --profile '*' config --format json)"; then
        violation "${relative}: docker compose config failed"
        continue
    fi

    managed="$(jq --arg dir "${project_dir}" 'any(.[]; .working_dir == $dir)' <<<"${targets}")"

    # doco-cd serves repository files from a per-revision checkout: data written
    # there is lost on the next revision, and every deployment recreates the
    # mounting container. Only read-only mounts on one-shot services qualify.
    messages="$(jq -r --arg repo "${ROOT_DIR}/" --arg one_shot "${ONE_SHOT_LABEL}" --argjson managed "${managed}" '
        .services | to_entries[] | .key as $service | .value as $config |
        (if ($config.image // "") | test("@sha256:[0-9a-f]{64}$") | not
            then "service \($service) image must be pinned by digest" else empty end),
        (if $config.build then "service \($service) must use a published image, not build" else empty end),
        (if $managed then
            $config.volumes // [] | .[] | select(.type == "bind" and (.source | startswith($repo))) |
            if $config.restart != "no" or ($config.labels // {})[$one_shot] != "true"
                then "service \($service) mounts repository path \(.target) but is not restart: \"no\" with label \($one_shot): \"true\""
            elif .read_only != true then "service \($service) must mount repository path \(.target) read-only"
            else empty end
        else empty end)
    ' <<<"${rendered}")"

    while IFS= read -r message; do
        if [[ -n "${message}" ]]; then
            violation "${relative}: ${message}"
        fi
    done <<<"${messages}"
done < <(find "${SYNOLOGY_DIR}" -name compose.yaml -type f -print0 | sort -z)

if ((violations > 0)); then
    echo "Synology validation failed with ${violations} violation(s)" >&2
    exit 1
fi

echo "Synology Compose projects and doco-cd targets OK"
