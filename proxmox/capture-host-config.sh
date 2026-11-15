#!/usr/bin/env bash
# Capture each pve-sbx node's cluster and host configuration into an
# age-encrypted archive. See proxmox/README.md#configuration-recovery-archive.
#
# An archive is accepted only when the SSH producer, the encryption, the full
# authenticated decryption and the tar listing all succeed and every required
# member is present. The plaintext tar stream never touches the workstation's
# disk, and an existing archive is never replaced.
set -euo pipefail
umask 077

pve_age_identity=${PVE_AGE_IDENTITY:-age.key}
pve_recovery_dir=${PVE_RECOVERY_DIR:-.private/pve-sbx/recovery}
pve_ssh_user=${PVE_SSH_USER:-kelchm}
pve_archive_stamp=$(date -u +%Y%m%dT%H%M%SZ)

# Fail rather than prompt, and give up on a dead connection within about a
# minute. The remote side bounds the capture itself with timeout(1).
pve_ssh_options=(
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o ServerAliveInterval=15
    -o ServerAliveCountMax=4
)

# Runs as root on the node. Its only argument is the root it captures: / in
# production, a synthetic tree in the offline test. Its stdout is the archive.
pve_remote_capture=$(cat <<'PVE_CAPTURE'
    set -eu
    umask 077
    pve_root=${1:?capture root}
    pve_root=${pve_root%/}
    pve_capture_dir=$(mktemp -d)
    trap 'rm -rf "$pve_capture_dir"' EXIT INT TERM

    command -v sqlite3 >/dev/null
    mkdir -p "$pve_capture_dir/var/lib/pve-cluster"
    sqlite3 "$pve_root/var/lib/pve-cluster/config.db" ".backup $pve_capture_dir/var/lib/pve-cluster/config.db"
    test "$(sqlite3 "$pve_capture_dir/var/lib/pve-cluster/config.db" "pragma quick_check")" = ok

    pveversion -v >"$pve_capture_dir/pveversion.txt"
    dpkg-query -W >"$pve_capture_dir/packages.txt"
    pvecm status >"$pve_capture_dir/pvecm-status.txt"
    pvesm status >"$pve_capture_dir/pvesm-status.txt"
    ip -j address >"$pve_capture_dir/ip-address.json"
    ip -j route >"$pve_capture_dir/ip-route.json"
    lsblk -J -O >"$pve_capture_dir/lsblk.json"
    nvme id-ctrl /dev/nvme0 >"$pve_capture_dir/nvme-id-ctrl.txt"
    nvme id-ns /dev/nvme0n1 >"$pve_capture_dir/nvme-id-ns.txt"
    nvme smart-log /dev/nvme0 >"$pve_capture_dir/nvme-smart-log.txt"
    nvme error-log /dev/nvme0 >"$pve_capture_dir/nvme-error-log.txt"
    journalctl -k -b --no-pager >"$pve_capture_dir/kernel-boot.log"
    cp "$pve_root/proc/cmdline" "$pve_capture_dir/kernel-cmdline.txt"

    # var/lib/vz/snippets holds node-local cloud-init snippets that guest
    # configurations reference; the rest of var/lib/vz (disks, ISO images,
    # backups) is deliberately out of scope.
    set --
    for pve_path in \
        etc/pve \
        etc/network/interfaces \
        etc/network/interfaces.d \
        etc/hosts \
        etc/hostname \
        etc/resolv.conf \
        etc/passwd \
        etc/group \
        etc/shadow \
        etc/gshadow \
        etc/sudoers \
        etc/sudoers.d/90-kelchm \
        etc/apt/sources.list \
        etc/apt/sources.list.d \
        etc/apt/apt.conf.d/no-nag-script \
        usr/local/bin/pve-remove-nag.sh \
        etc/corosync/authkey \
        etc/ssh/ssh_host_ed25519_key \
        etc/ssh/ssh_host_ed25519_key.pub \
        etc/lvm/backup \
        etc/lvm/archive \
        etc/kernel/cmdline \
        etc/default/grub \
        var/lib/vz/snippets \
        home/kelchm/.ssh/authorized_keys \
        root/.ssh/authorized_keys; do
        if test -e "$pve_root/$pve_path"; then
            set -- "$@" "$pve_path"
        fi
    done

    # The top-level pmxcfs dot files (.clusterlog, .rrd, .members and so on)
    # are generated runtime status, not configuration; config.db holds the
    # authoritative state. Any tar failure or warning, such as a file changing
    # while it is read, fails the capture.
    tar -C "${pve_root:-/}" --exclude='etc/pve/.*' -czf - "$@" -C "$pve_capture_dir" .
PVE_CAPTURE
)

die() {
    printf 'capture-host-config: %s\n' "$*" >&2
    exit 1
}

for pve_tool in age age-keygen sha256sum ssh tar; do
    command -v "$pve_tool" >/dev/null || die "required tool not found: $pve_tool"
done
test -r "$pve_age_identity" || die "age identity is not readable: $pve_age_identity"

pve_age_recipient=$(age-keygen -y "$pve_age_identity")
mkdir -p "$pve_recovery_dir"
chmod 700 "$pve_recovery_dir"

# Only the in-progress ciphertext this run created is ever removed.
pve_partial=
cleanup() {
    if [ -n "$pve_partial" ]; then
        rm -f -- "$pve_partial"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

capture_node() {
    local node=$1 address=$2
    local archive="$pve_recovery_dir/${node}-${pve_archive_stamp}.tar.gz.age"
    local listing required

    if [ -e "$archive" ] || [ -L "$archive" ]; then
        die "$node: $archive already exists; refusing to replace it"
    fi
    pve_partial=$(mktemp "$pve_recovery_dir/.${node}-${pve_archive_stamp}.XXXXXX")

    if ! printf '%s\n' "$pve_remote_capture" |
        ssh "${pve_ssh_options[@]}" "$pve_ssh_user@$address" \
            'sudo -n timeout --kill-after=10s 300s /bin/sh -s /' |
        age -r "$pve_age_recipient" >"$pve_partial"; then
        die "$node: capture failed; no archive was accepted"
    fi

    # Authenticate the whole ciphertext: tar stops reading at its end-of-archive
    # marker, so drain the rest to make age's exit status cover every byte.
    if ! listing=$(age -d -i "$pve_age_identity" "$pve_partial" | { tar -tzf - && cat >/dev/null; }); then
        die "$node: decryption or archive listing failed; no archive was accepted"
    fi

    for required in \
        etc/pve/storage.cfg \
        etc/pve/jobs.cfg \
        etc/pve/corosync.conf \
        etc/pve/priv/authorized_keys \
        etc/pve/priv/acme/letsencrypt-production \
        etc/pve/priv/acme/plugins.cfg \
        "etc/pve/nodes/$node/pveproxy-ssl.key" \
        "etc/pve/nodes/$node/pveproxy-ssl.pem" \
        etc/network/interfaces \
        etc/hostname \
        etc/passwd \
        etc/group \
        etc/shadow \
        etc/sudoers.d/90-kelchm \
        etc/apt/apt.conf.d/no-nag-script \
        usr/local/bin/pve-remove-nag.sh \
        etc/corosync/authkey \
        etc/ssh/ssh_host_ed25519_key \
        home/kelchm/.ssh/authorized_keys \
        ./var/lib/pve-cluster/config.db; do
        case $'\n'"$listing"$'\n' in
            *$'\n'"$required"$'\n'*) ;;
            *) die "$node: required member missing: $required; no archive was accepted" ;;
        esac
    done

    # A hard link publishes atomically and fails if the name already exists.
    ln -- "$pve_partial" "$archive" || die "$node: could not publish $archive"
    rm -f -- "$pve_partial"
    pve_partial=
    sha256sum "$archive"
}

for pve_endpoint in \
    pve-sbx-1:10.32.20.21 \
    pve-sbx-2:10.32.20.22 \
    pve-sbx-3:10.32.20.23; do
    capture_node "${pve_endpoint%%:*}" "${pve_endpoint#*:}"
done
