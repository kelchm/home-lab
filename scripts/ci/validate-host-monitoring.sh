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
    local name working_dir label rendered image spark_target="" polls entry
    local -a compose_args=() compose_environment=() mounts=()

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

    while IFS= read -r -d '' entry; do
        compose_environment+=("${entry}")
    done < <(jq -j '(.environment // {}) | to_entries[] | .key + "=" + (.value | tostring) + "\u0000"' <<<"${deployment}")

    if ! rendered="$(env -u SPARK_HOST "${compose_environment[@]}" docker compose "${compose_args[@]}" config --format json)"; then
        violation "${label}: Compose render failed"
        return
    fi
    if [[ "$(jq -r '.name' <<<"${rendered}")" != "${name}" ]]; then
        violation "${label}: Compose project name differs from the deployment name"
    fi

    if [[ "${name}" == doco-cd && "${config}" == "${ROOT_DIR}/sparks/monitoring/.doco-cd.yml" ]]; then
        violation "${label}: declare the deployer only in the per-Spark targets, never the shared target"
    fi
    if [[ "${name}" == doco-cd && "${working_dir}" == sparks/platform/doco-cd ]]; then
        case "${config##*/}" in
            .doco-cd.spark-1.yml) spark_target=spark-1 ;;
            .doco-cd.spark-2.yml) spark_target=spark-2 ;;
        esac
        if [[ -n "${spark_target}" ]]; then
            if ! polls="$(jq -r '.services["doco-cd"].environment.POLL_CONFIG // ""' <<<"${rendered}" | yq -o=json -I=0 '.')" || ! jq -e --arg target "${spark_target}" '
                type == "array" and
                any(.[]; (.target // "") == "") and
                any(.[]; .target == $target) and
                all(.[]; (.target // "") == "" or .target == $target)
            ' <<<"${polls}" >/dev/null; then
                violation "${label}: the deployer must retain the shared poll and its own ${spark_target} poll, without polling the other Spark target"
            fi
        fi
    fi
    if [[ "${name}" == doco-cd && "${working_dir}" == proxmox/platform/doco-cd ]] && ! jq -e '
        .services["doco-cd"].network_mode == "host" and
        ((.services["doco-cd"].security_opt // []) | index("apparmor=unconfined") != null)
    ' <<<"${rendered}" >/dev/null; then
        violation "${label}: PVE doco-cd must retain host networking and apparmor=unconfined"
    fi

    if [[ "${name}" == doco-cd ]] && ! jq -e '
        .services["doco-cd"].environment.SELF_UPDATE_ENABLED == "true" and
        .services["doco-cd"].environment.SELF_UPDATE_STRATEGY == "applier" and
        .services["doco-cd"].healthcheck.test == ["CMD", "/healthcheck"] and
        .services["doco-cd"].restart == "unless-stopped" and
        ([.services["doco-cd"].volumes[]? | select(
            .type == "bind" and .source == "/var/run/docker.sock" and .target == "/var/run/docker.sock"
        )] | length == 1) and
        ([.services["doco-cd"].volumes[]? | select(
            .type == "bind" and .source == "/var/lib/doco-cd" and .target == "/data" and .read_only != true
        )] | length == 1)
    ' <<<"${rendered}" >/dev/null; then
        violation "${label}: doco-cd must retain native self-update, required binds, writable state, healthcheck and restart policy"
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

configs="$(find "${ROOT_DIR}/sparks/monitoring" "${ROOT_DIR}/proxmox/monitoring" -name '.doco-cd.yml' -o -name '.doco-cd.*.yml' | sort)"
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

if ! cmp -s "${ROOT_DIR}/proxmox/monitoring/apt-metrics.sh" "${ROOT_DIR}/sparks/monitoring/apt-metrics.sh"; then
    violation "proxmox/monitoring/apt-metrics.sh and sparks/monitoring/apt-metrics.sh must be identical"
fi
if ! diff -r -q "${ROOT_DIR}/proxmox/monitoring/logs" "${ROOT_DIR}/sparks/monitoring/logs" >/dev/null; then
    violation "proxmox/monitoring/logs and sparks/monitoring/logs must be identical"
fi

if ! diff -r -q "${ROOT_DIR}/proxmox/monitoring/logs" "${ROOT_DIR}/synology/monitoring/logs" >/dev/null; then
    violation "proxmox/monitoring/logs and synology/monitoring/logs must be identical"
fi

if ((violations > 0)); then
    echo "host monitoring validation failed with ${violations} violation(s)" >&2
    exit 1
fi
echo "host monitoring projects OK"
