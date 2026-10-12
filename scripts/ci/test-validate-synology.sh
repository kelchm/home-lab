#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly TEMP_DIR="$(mktemp -d)"
readonly IMAGE='docker.io/library/busybox:1.38.0@sha256:fd7dc98638c8e305f4dc34e979f1c0fdfdcaeb0fbf8fcff77ae834b6da3d7e6e'

function cleanup() {
    rm -rf -- "${TEMP_DIR}"
}

trap cleanup EXIT

# Writes a declared project "app" whose one-shot publisher mounts a repository file.
function write_fixture() {
    rm -rf -- "${TEMP_DIR}/synology"
    mkdir -p "${TEMP_DIR}/synology/app"
    cat >"${TEMP_DIR}/synology/app/compose.yaml" <<EOF
name: app
services:
  app:
    image: ${IMAGE}
    volumes:
      - /volume1/docker/app:/state
    restart: unless-stopped
  publish:
    image: ${IMAGE}
    labels:
      cd.doco.deployment.one_shot: "true"
    volumes:
      - ./file.txt:/source/file.txt:ro
      - /volume1/docker/app:/state
    restart: "no"
EOF
    printf 'name: app\nworking_dir: synology/app\n' >"${TEMP_DIR}/synology/.doco-cd.yml"
}

function edit() {
    sed -i.bak "$1" "${TEMP_DIR}/synology/$2"
}

function write_self_fixture() {
    write_fixture
    mkdir -p "${TEMP_DIR}/synology/platform/doco-cd"
    cp "${ROOT_DIR}/synology/platform/doco-cd/compose.yaml" "${TEMP_DIR}/synology/platform/doco-cd/"
    printf '\n---\nname: doco-cd\nworking_dir: synology/platform/doco-cd\n' >>"${TEMP_DIR}/synology/.doco-cd.yml"
}

function expect_success() {
    "${ROOT_DIR}/scripts/ci/validate-synology.sh" "${TEMP_DIR}" >/dev/null
}

function expect_failure() {
    local description="$1" expected="${2:-}" output

    if output="$("${ROOT_DIR}/scripts/ci/validate-synology.sh" "${TEMP_DIR}" 2>&1)"; then
        echo "Expected Synology validation to fail: ${description}" >&2
        exit 1
    fi
    if [[ -n "${expected}" && "${output}" != *"${expected}"* ]]; then
        echo "Unexpected validation failure for ${description}: ${output}" >&2
        exit 1
    fi
}

write_fixture
expect_success

write_fixture
edit "1,/image: /s|image: .*|image: docker.io/library/busybox:1.38.0|" app/compose.yaml
expect_failure 'tag-only image'

write_fixture
printf '  debug:\n    image: docker.io/library/busybox:1.38.0\n    profiles: [debug]\n' >>"${TEMP_DIR}/synology/app/compose.yaml"
expect_failure 'tag-only image in a profiled service'

write_fixture
edit 's|restart: "no"|restart: unless-stopped|' app/compose.yaml
expect_failure 'repository mount on a long-running service'

write_fixture
edit '/    labels:/d' app/compose.yaml
edit '/cd.doco.deployment.one_shot/d' app/compose.yaml
expect_failure 'repository mount on a service without the one-shot label'

write_fixture
edit 's|/source/file.txt:ro|/source/file.txt|' app/compose.yaml
expect_failure 'writable repository mount'

write_fixture
printf 'destroy: true\n' >>"${TEMP_DIR}/synology/.doco-cd.yml"
expect_failure 'destroy enabled'

write_fixture
printf 'auto_discovery: true\n' >>"${TEMP_DIR}/synology/.doco-cd.yml"
expect_failure 'auto-discovery enabled'

for working_dir in synology/platform synology/platform/app synology/./platform/app synology/./app synology/app/; do
    write_fixture
    edit "s|working_dir: synology/app|working_dir: ${working_dir}|" .doco-cd.yml
    expect_failure "non-canonical or platform working_dir ${working_dir}"
done

write_self_fixture
expect_success

write_self_fixture
edit 's|name: doco-cd|name: other|' .doco-cd.yml
expect_failure 'platform exception with the wrong project name' 'with name doco-cd'

write_self_fixture
edit 's|SELF_UPDATE_ENABLED: "true"|SELF_UPDATE_ENABLED: "false"|' platform/doco-cd/compose.yaml
expect_failure 'self-update disabled on the managed deployer' 'native self-update'

write_self_fixture
edit 's|SELF_UPDATE_STRATEGY: applier|SELF_UPDATE_STRATEGY: scale_out|' platform/doco-cd/compose.yaml
expect_failure 'wrong self-update strategy' 'native self-update'

write_self_fixture
edit 's|restart: unless-stopped|restart: "no"|' platform/doco-cd/compose.yaml
expect_failure 'deployer without crash recovery' 'restart policy'

write_self_fixture
edit 's|"/healthcheck"|"/doco-cd", "healthcheck"|' platform/doco-cd/compose.yaml
expect_failure 'obsolete deployer healthcheck' 'healthcheck'

for mutation in missing_socket missing_data wrong_source wrong_target read_only; do
    write_self_fixture
    case "${mutation}" in
        missing_socket) edit '\|/var/run/docker.sock:/var/run/docker.sock|d' platform/doco-cd/compose.yaml ;;
        missing_data) edit '\|/volume1/docker/doco-cd/data:/data|d' platform/doco-cd/compose.yaml ;;
        wrong_source) edit 's|/volume1/docker/doco-cd/data:/data|/volume1/docker/other:/data|' platform/doco-cd/compose.yaml ;;
        wrong_target) edit 's|/volume1/docker/doco-cd/data:/data|/volume1/docker/doco-cd/data:/other|' platform/doco-cd/compose.yaml ;;
        read_only) edit 's|/volume1/docker/doco-cd/data:/data|/volume1/docker/doco-cd/data:/data:ro|' platform/doco-cd/compose.yaml ;;
    esac
    expect_failure "deployer bind: ${mutation}" 'required binds, writable state'
done

for mutation in missing_key writable_key wrong_key_path; do
    write_self_fixture
    case "${mutation}" in
        missing_key) edit '\|/volume1/docker/doco-cd/sops_age_key:/run/secrets/sops_age_key:ro|d' platform/doco-cd/compose.yaml ;;
        writable_key) edit 's|/run/secrets/sops_age_key:ro|/run/secrets/sops_age_key|' platform/doco-cd/compose.yaml ;;
        wrong_key_path) edit 's|SOPS_AGE_KEY_FILE: /run/secrets/sops_age_key|SOPS_AGE_KEY_FILE: /run/secrets/other|' platform/doco-cd/compose.yaml ;;
    esac
    expect_failure "NAS age key: ${mutation}" 'read-only NAS age key'
done

write_fixture
edit 's|^name: app|name: {value: app}|' .doco-cd.yml
expect_failure 'non-string target name'

write_fixture
edit 's|^name: app|name: other|' .doco-cd.yml
expect_failure 'target name differs from the Compose project name'

# Undeclared projects keep manual-apply semantics for repository-relative paths.
write_fixture
edit 's|restart: "no"|restart: unless-stopped|' app/compose.yaml
rm "${TEMP_DIR}/synology/.doco-cd.yml"
expect_success

echo 'Synology validator regression tests passed.'
