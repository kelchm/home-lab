#!/usr/bin/env bash
# Catalog errors can remove issue and PR associations: exercise the failure cases.
set -euo pipefail

readonly VALIDATOR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/validate-labels.sh"
readonly FIXTURE="$(mktemp -d)"
trap 'rm -rf "${FIXTURE}"' EXIT
mkdir -p "${FIXTURE}/.github"

function reset_fixture() {
    cat > "${FIXTURE}/.github/labels.yaml" <<'YAML'
- name: platform/example
  color: "c5def5"
  aliases: [area/example, area/legacy]
- name: area/docs
  color: "bfd4f2"
- name: area/new
  color: "bfd4f2"
YAML
    cat > "${FIXTURE}/.github/labeler.yaml" <<'YAML'
platform/example:
  - changed-files:
      - any-glob-to-any-file: [example/**]
area/docs:
  - changed-files:
      - any-glob-to-any-file: [docs/**]
YAML
    echo '[{"name":"area/example"},{"name":"area/docs"}]' > "${FIXTURE}/live.json"
}

function expect_failure() {
    local message="$1"
    if "${VALIDATOR}" "${FIXTURE}" "${FIXTURE}/live.json" > "${FIXTURE}/result" 2>&1; then
        echo "FAIL: expected ${message}" >&2
        exit 1
    fi
    if ! grep -Fq "${message}" "${FIXTURE}/result"; then
        cat "${FIXTURE}/result" >&2
        exit 1
    fi
    echo "ok: rejects ${message}"
}

reset_fixture
"${VALIDATOR}" "${FIXTURE}" "${FIXTURE}/live.json"
echo "ok: rename, unchanged label, and new label coexist"

echo '[{"name":"platform/example"},{"name":"area/docs"},{"name":"area/new"}]' > "${FIXTURE}/live.json"
"${VALIDATOR}" "${FIXTURE}" "${FIXTURE}/live.json"
echo "ok: post-migration catalog is safe to reapply"

reset_fixture
yq -i '.[0].aliases = ["area/legacy"]' "${FIXTURE}/.github/labels.yaml"
expect_failure 'labels would be deleted'

reset_fixture
echo '[{"name":"area/example"},{"name":"platform/example"}]' > "${FIXTURE}/live.json"
expect_failure 'labels would be merged'

reset_fixture
echo '[{"name":"AREA/EXAMPLE"},{"name":"area/legacy"}]' > "${FIXTURE}/live.json"
expect_failure 'labels would be merged'

reset_fixture
yq -i '.[0].delete = true' "${FIXTURE}/.github/labels.yaml"
expect_failure 'explicit deletion'

reset_fixture
yq -i '.[1].aliases = ["area/example"]' "${FIXTURE}/.github/labels.yaml"
expect_failure 'duplicate label names or aliases'

reset_fixture
yq -i '."area/typo" = []' "${FIXTURE}/.github/labeler.yaml"
expect_failure 'labeler keys absent from catalog'

reset_fixture
yq -i '.[0].description = "This description is deliberately longer than the maximum allowed by the GitHub labels API: one hundred characters."' "${FIXTURE}/.github/labels.yaml"
expect_failure 'invalid label fields'

echo "Label validation regression cases OK"
