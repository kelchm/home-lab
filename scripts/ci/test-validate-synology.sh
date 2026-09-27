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

function expect_success() {
    "${ROOT_DIR}/scripts/ci/validate-synology.sh" "${TEMP_DIR}" >/dev/null
}

function expect_failure() {
    local description="$1"

    if "${ROOT_DIR}/scripts/ci/validate-synology.sh" "${TEMP_DIR}" 2>/dev/null; then
        echo "Expected Synology validation to fail: ${description}" >&2
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
