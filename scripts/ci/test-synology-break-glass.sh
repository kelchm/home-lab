#!/usr/bin/env bash
# Exercise scripts/synology/break-glass-apply.sh offline: a throwaway age key,
# synthetic secrets, and ssh/rsync stubs that record what would reach Athena.
# No production secret is read.

set -o errexit
set -o nounset
set -o pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
umask 077
readonly TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

readonly REPO="${TEST_DIR}/repo"
readonly PROJECT="${REPO}/synology/app"
readonly LOG="${TEST_DIR}/calls.log"
readonly SENT="${TEST_DIR}/sent"
export LOG SENT
mkdir -p "${REPO}/scripts/synology" "${PROJECT}" "${TEST_DIR}/bin"
cp "${SCRIPT_DIR}/../synology/break-glass-apply.sh" "${REPO}/scripts/synology/"
git -C "${REPO}" init --quiet

unset SOPS_AGE_KEY
age-keygen -o "${TEST_DIR}/fixture.key" 2>/dev/null
recipient="$(age-keygen -y "${TEST_DIR}/fixture.key")"
other_recipient="$(age-keygen -o "${TEST_DIR}/other.key" 2>/dev/null && age-keygen -y "${TEST_DIR}/other.key")"
rm "${TEST_DIR}/other.key"
export SOPS_AGE_KEY_FILE="${TEST_DIR}/fixture.key"
printf 'creation_rules:\n  - age: %s\n' "${recipient}" >"${TEST_DIR}/.sops.yaml"
export SOPS_CONFIG="${TEST_DIR}/.sops.yaml"

printf '# Fixture values only\nTOKEN=throwaway\n' >"${TEST_DIR}/plain.env"
encrypt() {
    sops encrypt --input-type dotenv --output-type dotenv --age "$1" --output "$2" "${TEST_DIR}/plain.env"
}
encrypt "${recipient}" "${TEST_DIR}/good.env"
encrypt "${other_recipient}" "${TEST_DIR}/foreign.env"
printf 'name: app\nservices:\n  app:\n    image: busybox\n    env_file: [app.sops.env]\n' >"${PROJECT}/compose.yaml"

# ssh: record the call, fail the remote Compose step when asked.
cat >"${TEST_DIR}/bin/ssh" <<'STUB'
#!/usr/bin/env bash
printf 'ssh %s\n' "$2" >>"${LOG}"
[[ -z "${FAIL_COMPOSE:-}" || "$2" != *docker-compose* ]] || exit 1
STUB
# rsync: record the call and keep a copy of what would be sent.
cat >"${TEST_DIR}/bin/rsync" <<'STUB'
#!/usr/bin/env bash
printf 'rsync\n' >>"${LOG}"
rm -rf "${SENT}" && cp -R "${@: -2:1}" "${SENT}"
STUB
chmod +x "${TEST_DIR}/bin/ssh" "${TEST_DIR}/bin/rsync"

function fail() {
    echo "FAIL: $*" >&2
    cat "${TEST_DIR}/output.log" >&2
    exit 1
}

# Each run stages under its own TMPDIR, which must be empty afterwards.
run() {
    : >"${LOG}"
    rm -rf "${SENT}" "${TEST_DIR}/tmp"
    mkdir "${TEST_DIR}/tmp"
    local status=0
    (cd "${REPO}" && PATH="${TEST_DIR}/bin:${PATH}" TMPDIR="${TEST_DIR}/tmp" \
        scripts/synology/break-glass-apply.sh app) >"${TEST_DIR}/output.log" 2>&1 || status=$?
    [[ -z "$(ls -A "${TEST_DIR}/tmp")" ]] || fail "a local staging copy was left behind"
    return "${status}"
}

# A decryptable secret is sent in plaintext, Compose runs, and the decrypted
# files are removed from Athena afterwards.
cp "${TEST_DIR}/good.env" "${PROJECT}/app.sops.env"
run || fail "a decryptable project was refused"
grep -qx 'TOKEN=throwaway' "${SENT}/app.sops.env" || fail "the staged secret was not decrypted"
if grep -rq 'ENC\[' "${SENT}"; then fail "ciphertext was sent"; fi
if find "${SENT}" -perm -g=r | grep -q .; then fail "staged files are readable by group or others"; fi
grep -q 'docker stop doco-cd' "${LOG}" || fail "doco-cd was not stopped"
tail -n 1 "${LOG}" | grep -q "find '/volume1/docker/break-glass/app' -type f -name '\*.sops.env'" ||
    fail "the decrypted files were not removed from Athena last"
echo "ok: decrypted, applied and cleaned up"

# A Compose failure still removes the decrypted files.
if FAIL_COMPOSE=1 run; then fail "a Compose failure was reported as success"; fi
tail -n 1 "${LOG}" | grep -q 'find ' || fail "a Compose failure left decrypted files on Athena"
echo "ok: Compose failure cleaned up"

# A secret this key cannot decrypt stops everything before Athena is touched.
cp "${TEST_DIR}/foreign.env" "${PROJECT}/app.sops.env"
if run; then fail "an undecryptable secret was accepted"; fi
[[ ! -s "${LOG}" ]] || fail "Athena was contacted after a decryption failure"
echo "ok: undecryptable secret refused before stopping doco-cd"

# Ciphertext outside *.sops.env is caught by the leftover check.
cp "${TEST_DIR}/good.env" "${PROJECT}/app.sops.env"
cp "${TEST_DIR}/good.env" "${PROJECT}/extra.env"
if run; then fail "leftover ciphertext was accepted"; fi
grep -q 'still encrypted' "${TEST_DIR}/output.log" || fail "leftover ciphertext failed for an unexpected reason"
[[ ! -s "${LOG}" ]] || fail "Athena was contacted with leftover ciphertext"
rm "${PROJECT}/extra.env"
echo "ok: leftover ciphertext refused before stopping doco-cd"
