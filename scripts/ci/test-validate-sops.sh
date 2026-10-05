#!/usr/bin/env bash
# Exercise encryption checks with throwaway recipients and no decryption keys.

set -o errexit
set -o nounset
set -o pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
umask 077
readonly TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

unset SOPS_AGE_KEY
export SOPS_AGE_KEY_FILE="${TEST_DIR}/nonexistent"
"${SCRIPT_DIR}/validate-sops.sh"

mkdir -p "${TEST_DIR}/scripts/ci" "${TEST_DIR}/synology/app"
cp "${SCRIPT_DIR}/validate-sops.sh" "${TEST_DIR}/scripts/ci/"
git -C "${TEST_DIR}" init --quiet
recipient="$(age-keygen -o "${TEST_DIR}/fixture.key" 2>/dev/null && age-keygen -y "${TEST_DIR}/fixture.key")"
wrong_recipient="$(age-keygen -o "${TEST_DIR}/wrong.key" 2>/dev/null && age-keygen -y "${TEST_DIR}/wrong.key")"
# The private fixture keys are never supplied to SOPS.
rm "${TEST_DIR}/fixture.key" "${TEST_DIR}/wrong.key"
cat > "${TEST_DIR}/.sops.yaml" <<EOF_CONFIG
creation_rules:
  - path_regex: synology/.*\\.sops\\.env
    age: ${recipient}
EOF_CONFIG
export SOPS_CONFIG="${TEST_DIR}/.sops.yaml"
printf '# Fixture values only\nTOKEN=throwaway\nPASSWORD=another=value\n' > "${TEST_DIR}/plain.env"
sops encrypt --output "${TEST_DIR}/valid.env" --filename-override "${TEST_DIR}/synology/app/secrets.sops.env" "${TEST_DIR}/plain.env"
sops encrypt --filename-override "${TEST_DIR}/synology/app/secrets.sops.env" --age "${wrong_recipient}" --output "${TEST_DIR}/wrong.env" "${TEST_DIR}/plain.env"
readonly FIXTURE="${TEST_DIR}/synology/app/secrets.sops.env"
cp "${TEST_DIR}/valid.env" "${FIXTURE}"
git -C "${TEST_DIR}" add .
"${TEST_DIR}/scripts/ci/validate-sops.sh"
echo 'ok: encrypted dotenv with matching recipient accepted'

function expect_failure() {
    local name="$1" diagnostic="$2"
    if "${TEST_DIR}/scripts/ci/validate-sops.sh" > "${TEST_DIR}/output.log" 2>&1; then
        echo "FAIL: ${name} unexpectedly passed" >&2
        exit 1
    fi
    if ! grep -Fq "${diagnostic}" "${TEST_DIR}/output.log"; then
        echo "FAIL: ${name} failed for an unexpected reason" >&2
        cat "${TEST_DIR}/output.log" >&2
        exit 1
    fi
    echo "ok: ${name} rejected (${diagnostic})"
}

cp "${TEST_DIR}/plain.env" "${FIXTURE}"
expect_failure 'plaintext dotenv' 'SOPS file is not encrypted'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf 'PLAINTEXT=oops\n' >> "${FIXTURE}"
expect_failure 'partially encrypted dotenv' 'dotenv value is not encrypted'

cp "${TEST_DIR}/wrong.env" "${FIXTURE}"
expect_failure 'wrong recipient' 'age recipients do not match the creation rule'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf 'sops_fake=oops\n' >> "${FIXTURE}"
expect_failure 'unknown metadata prefix' 'unsupported dotenv SOPS metadata'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf 'TOKEN=oops\n' >> "${FIXTURE}"
expect_failure 'duplicate assignment' 'invalid or duplicate dotenv assignment'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf 'MALFORMED=ENC[AES256_GCM,data:YQ==,iv:YQ==,tag:YQ==,type:str]\n' >> "${FIXTURE}"
expect_failure 'malformed ciphertext' 'dotenv value is not encrypted'

echo 'SOPS validator regression tests passed.'
