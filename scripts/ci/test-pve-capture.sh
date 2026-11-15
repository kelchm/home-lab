#!/usr/bin/env bash
# Exercise proxmox/capture-host-config.sh offline: a throwaway age key, a
# synthetic filesystem tree per node, and stubs for ssh and the PVE host tools.
# The capture's own remote script runs against those trees; no host is
# contacted and no production key or archive is read.

set -o errexit
set -o nounset
set -o pipefail

readonly REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly CAPTURE="${REPO}/proxmox/capture-host-config.sh"
umask 077
readonly TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

readonly STAMP=20261114T120000Z
readonly PRIOR=pve-sbx-1-20260827T200605Z.tar.gz.age
REAL_AGE="$(command -v age)"
readonly REAL_AGE
export REAL_AGE STAMP
export ROOTS="${TEST_DIR}/roots" SSH_LOG="${TEST_DIR}/ssh.log" HANG_MARK="${TEST_DIR}/hanging"

age-keygen -o "${TEST_DIR}/fixture.key" 2>/dev/null
readonly KEY="${TEST_DIR}/fixture.key"

fail() {
    echo "FAIL: $*" >&2
    for log in stdout stderr; do
        [[ ! -s "${TEST_DIR}/${log}" ]] || { echo "--- ${log}" >&2; cat "${TEST_DIR}/${log}" >&2; }
    done
    exit 1
}

# --- Synthetic node trees -------------------------------------------------

make_root() {
    local node=$1 root="${ROOTS}/$1" file
    for file in \
        etc/pve/storage.cfg etc/pve/jobs.cfg etc/pve/corosync.conf etc/pve/user.cfg \
        etc/pve/.clusterlog etc/pve/.members \
        etc/pve/priv/authorized_keys etc/pve/priv/acme/letsencrypt-production etc/pve/priv/acme/plugins.cfg \
        "etc/pve/nodes/${node}/pveproxy-ssl.key" "etc/pve/nodes/${node}/pveproxy-ssl.pem" \
        "etc/pve/nodes/${node}/qemu-server/103.conf" \
        etc/network/interfaces etc/hostname etc/passwd etc/group etc/shadow etc/sudoers.d/90-kelchm \
        etc/apt/apt.conf.d/no-nag-script usr/local/bin/pve-remove-nag.sh etc/corosync/authkey \
        etc/ssh/ssh_host_ed25519_key home/kelchm/.ssh/authorized_keys \
        var/lib/pve-cluster/config.db proc/cmdline \
        var/lib/vz/images/103/vm-103-disk-0.raw var/lib/vz/template/iso/installer.iso \
        var/lib/vz/dump/vzdump-qemu-103.vma.zst; do
        mkdir -p "${root}/$(dirname "${file}")"
        printf 'synthetic %s %s\n' "${node}" "${file}" >"${root}/${file}"
    done
}

for node in pve-sbx-1 pve-sbx-2 pve-sbx-3; do
    make_root "${node}"
done
# Node 1 keeps a renamed router's old snippet beside the current one, node 2
# has one snippet, and node 3 has never had a snippets directory.
mkdir -p "${ROOTS}/pve-sbx-1/var/lib/vz/snippets" "${ROOTS}/pve-sbx-2/var/lib/vz/snippets"
echo '#cloud-config' >"${ROOTS}/pve-sbx-1/var/lib/vz/snippets/netbird-router-1.yaml"
echo '#cloud-config' >"${ROOTS}/pve-sbx-1/var/lib/vz/snippets/netbird-pilot-router.yaml"
echo '#cloud-config' >"${ROOTS}/pve-sbx-2/var/lib/vz/snippets/netbird-router-2.yaml"

# --- Stubs ----------------------------------------------------------------

mkdir -p "${TEST_DIR}/bin" "${TEST_DIR}/tamper"

# ssh plays the node: it runs the capture's remote script, read from stdin,
# against that node's synthetic tree, and can then misbehave on request.
cat >"${TEST_DIR}/bin/ssh" <<'STUB'
#!/usr/bin/env bash
set -o pipefail
printf '%s\n' "$*" >>"${SSH_LOG}"
target=${*: -2:1}
case ${target#*@} in
    10.32.20.21) node=pve-sbx-1 ;;
    10.32.20.22) node=pve-sbx-2 ;;
    10.32.20.23) node=pve-sbx-3 ;;
    *) echo "ssh stub: unexpected target ${target}" >&2; exit 255 ;;
esac
[[ ${SSH_NODE:-${node}} == "${node}" ]] && mode=${SSH_MODE:-normal} || mode=normal
case ${mode} in
    normal) sh -s "${ROOTS}/${node}" ;;
    # A complete, valid archive, then the connection or remote shell fails.
    fail-after-archive) sh -s "${ROOTS}/${node}"; exit 255 ;;
    # A complete archive followed by more than one age chunk of other data.
    trailer) sh -s "${ROOTS}/${node}" && head -c 200000 /dev/urandom ;;
    truncate) sh -s "${ROOTS}/${node}" | head -c 600; exit 0 ;;
    garbage) cat >/dev/null; echo 'not an archive' ;;
    hang) cat >/dev/null; : >"${HANG_MARK}"; sleep 30 ;;
esac
STUB

# PVE and host tools print synthetic output; nvme can be made to fail.
for tool in pveversion dpkg-query pvecm pvesm ip lsblk journalctl; do
    printf '#!/bin/sh\necho "synthetic %s $*"\n' "${tool}" >"${TEST_DIR}/bin/${tool}"
done
cat >"${TEST_DIR}/bin/nvme" <<'STUB'
#!/bin/sh
[ -z "${NVME_FAIL:-}" ] || { echo 'nvme: No such device' >&2; exit 1; }
echo "synthetic nvme $*"
STUB
cat >"${TEST_DIR}/bin/sqlite3" <<'STUB'
#!/bin/sh
case $2 in
    ".backup "*) cp "$1" "${2#.backup }" ;;
    "pragma quick_check") echo ok ;;
    *) exit 1 ;;
esac
STUB
cat >"${TEST_DIR}/bin/date" <<'STUB'
#!/bin/sh
echo "${STAMP}"
STUB

# Encrypt with the real age, then flip one bit inside the final authenticated
# chunk, as storage corruption or tampering would.
cat >"${TEST_DIR}/tamper/age" <<'STUB'
#!/usr/bin/env bash
set -o pipefail
case " $* " in
    *" -r "*) "${REAL_AGE}" "$@" | perl -0777 -pe 'substr($_, -5, 1) ^= "\x01"' ;;
    *) exec "${REAL_AGE}" "$@" ;;
esac
STUB
chmod +x "${TEST_DIR}"/bin/* "${TEST_DIR}/tamper/age"

# --- Helpers --------------------------------------------------------------

OUT="${TEST_DIR}/out"

# Each case starts from a recovery directory holding one previously accepted
# archive, which no run may change.
reset_output() {
    rm -rf "${OUT}" "${HANG_MARK}" "${SSH_LOG}"
    mkdir -p "${OUT}"
    printf 'previously accepted ciphertext\n' >"${OUT}/${PRIOR}"
    chmod 0644 "${OUT}/${PRIOR}"
}

run_capture() {
    (
        cd "${TEST_DIR}"
        PATH="${EXTRA_PATH:-}${TEST_DIR}/bin:${PATH}" \
            PVE_AGE_IDENTITY="${KEY}" PVE_RECOVERY_DIR="${OUT}" \
            "${CAPTURE}"
    ) >"${TEST_DIR}/stdout" 2>"${TEST_DIR}/stderr"
}

archive() { echo "${OUT}/$1-${STAMP}.tar.gz.age"; }

members() { age -d -i "${KEY}" "$1" | { tar -tzf - && cat >/dev/null; }; }

entries() { find "${OUT}" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort; }

mode_of() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }

assert_prior_intact() {
    [[ "$(cat "${OUT}/${PRIOR}")" == 'previously accepted ciphertext' ]] || fail "$1: prior archive changed"
    [[ "$(mode_of "${OUT}/${PRIOR}")" == 644 ]] || fail "$1: prior archive mode changed"
}

# A failed run exits nonzero, leaves exactly the expected accepted archives
# and no in-progress files, and leaves the prior archive untouched.
assert_rejected() {
    local name=$1 rc=$2
    shift 2
    [[ ${rc} -ne 0 ]] || fail "${name}: capture exited 0"
    local expected actual
    expected="$(printf '%s\n' "${PRIOR}" "$@" | sort)"
    actual="$(entries)"
    [[ "${actual}" == "${expected}" ]] || fail "${name}: recovery directory holds:
${actual}"
    assert_prior_intact "${name}"
    echo "PASS: ${name}"
}

expect_failure() {
    local rc=0
    run_capture || rc=$?
    echo "${rc}"
}

# --- Cases ----------------------------------------------------------------

# 1. Success: every node is accepted, private, and has the expected payload.
reset_output
run_capture || fail "success: capture exited nonzero"
for node in pve-sbx-1 pve-sbx-2 pve-sbx-3; do
    [[ -f "$(archive "${node}")" ]] || fail "success: no archive for ${node}"
    [[ "$(mode_of "$(archive "${node}")")" == 600 ]] || fail "success: ${node} archive is not 0600"
done
[[ "$(mode_of "${OUT}")" == 700 ]] || fail "success: recovery directory is not 0700"
[[ "$(entries)" == "$(printf '%s\n' "${PRIOR}" "$(basename "$(archive pve-sbx-1)")" \
    "$(basename "$(archive pve-sbx-2)")" "$(basename "$(archive pve-sbx-3)")" | sort)" ]] ||
    fail "success: unexpected files in recovery directory"
assert_prior_intact success
sha256sum -c "${TEST_DIR}/stdout" >/dev/null || fail "success: printed digests do not match"
grep -c -- '-o BatchMode=yes -o ConnectTimeout=10' "${SSH_LOG}" | grep -qx 3 || fail "success: ssh may prompt or wait"
grep -q "sudo -n timeout " "${SSH_LOG}" || fail "success: remote capture is not time-bounded"

listing="$(members "$(archive pve-sbx-1)")"
for member in \
    etc/pve/storage.cfg etc/pve/nodes/pve-sbx-1/qemu-server/103.conf ./var/lib/pve-cluster/config.db \
    var/lib/vz/snippets/netbird-router-1.yaml var/lib/vz/snippets/netbird-pilot-router.yaml \
    ./kernel-cmdline.txt ./nvme-smart-log.txt; do
    grep -qx -- "${member}" <<<"${listing}" || fail "success: pve-sbx-1 lacks ${member}"
done
for excluded in var/lib/vz/images var/lib/vz/template var/lib/vz/dump etc/pve/.clusterlog etc/pve/.members; do
    ! grep -q -- "^${excluded}" <<<"${listing}" || fail "success: pve-sbx-1 captured ${excluded}"
done
grep -qx var/lib/vz/snippets/netbird-router-2.yaml <<<"$(members "$(archive pve-sbx-2)")" ||
    fail "success: pve-sbx-2 lacks its snippet"
! grep -q '^var/lib/vz' <<<"$(members "$(archive pve-sbx-3)")" || fail "success: pve-sbx-3 captured var/lib/vz"
echo "PASS: success, with and without node-local snippets"

# Real age and tar: a tampered final chunk still yields a tar stream whose
# members all list. This is the readable prefix the next case relies on.
tampered="${TEST_DIR}/tampered.age"
age -d -i "${KEY}" "$(archive pve-sbx-1)" | cat - <(head -c 200000 /dev/urandom) |
    "${TEST_DIR}/tamper/age" -r "$(age-keygen -y "${KEY}")" >"${tampered}"
! age -d -i "${KEY}" "${tampered}" >/dev/null 2>&1 || fail "fixture: tampered ciphertext authenticated"
prefix="$(age -d -i "${KEY}" "${tampered}" 2>/dev/null | tar -tzf - 2>/dev/null || true)"
grep -qx etc/corosync/authkey <<<"${prefix}" || fail "fixture: tampered ciphertext has no readable tar prefix"

# 2. Decryption fails authentication although the archive inside lists.
reset_output
rc="$(SSH_MODE=trailer EXTRA_PATH="${TEST_DIR}/tamper:" expect_failure)"
assert_rejected "authentication failure after a readable archive" "${rc}"

# 3. The producer fails after sending a complete, valid archive. Node 1 was
#    accepted before it and stays; nothing for node 2 or 3 is written.
reset_output
rc="$(SSH_NODE=pve-sbx-2 SSH_MODE=fail-after-archive expect_failure)"
assert_rejected "producer failure after a complete archive" "${rc}" "$(basename "$(archive pve-sbx-1)")"

# 4. A remote command fails before tar runs.
reset_output
rc="$(NVME_FAIL=1 expect_failure)"
assert_rejected "remote command failure" "${rc}"

# 5. Malformed payloads.
reset_output
rc="$(SSH_MODE=garbage expect_failure)"
assert_rejected "non-archive payload" "${rc}"
reset_output
rc="$(SSH_MODE=truncate expect_failure)"
assert_rejected "truncated archive" "${rc}"

# 6. A required member is missing from the node.
mv "${ROOTS}/pve-sbx-1/etc/corosync/authkey" "${TEST_DIR}/authkey"
reset_output
rc="$(expect_failure)"
mv "${TEST_DIR}/authkey" "${ROOTS}/pve-sbx-1/etc/corosync/authkey"
grep -q 'required member missing: etc/corosync/authkey' "${TEST_DIR}/stderr" || fail "missing member: not reported"
assert_rejected "missing required member" "${rc}"

# 7. An archive already holds this run's name: it is not replaced.
reset_output
printf 'accepted earlier this second\n' >"$(archive pve-sbx-1)"
rc="$(expect_failure)"
[[ "$(cat "$(archive pve-sbx-1)")" == 'accepted earlier this second' ]] || fail "collision: archive replaced"
assert_rejected "existing archive name" "${rc}" "$(basename "$(archive pve-sbx-1)")"
[[ ! -s "${SSH_LOG}" ]] || fail "collision: contacted a node anyway"

# 8. Interrupted mid-capture: the in-progress ciphertext is removed.
reset_output
set -m
(SSH_MODE=hang run_capture) &
capture_pid=$!
set +m
for _ in $(seq 100); do
    [[ ! -e "${HANG_MARK}" ]] || break
    sleep 0.1
done
[[ -e "${HANG_MARK}" ]] || fail "interrupt: capture never reached the node"
compgen -G "${OUT}/.pve-sbx-1-${STAMP}.*" >/dev/null || fail "interrupt: no in-progress file to clean up"
kill -TERM -- "-${capture_pid}"
rc=0
wait "${capture_pid}" || rc=$?
assert_rejected "interrupted capture" "${rc}"

echo "All PVE capture tests passed."
