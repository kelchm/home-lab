#!/bin/sh
set -eu

pve_age_identity=${PVE_AGE_IDENTITY:-age.key}
pve_recovery_dir=${PVE_RECOVERY_DIR:-.private/pve-sbx/recovery}
pve_ssh_user=${PVE_SSH_USER:-kelchm}
pve_archive_stamp=$(date -u +%Y%m%dT%H%M%SZ)

test -r "$pve_age_identity"
command -v age >/dev/null
command -v age-keygen >/dev/null
command -v grep >/dev/null
command -v sha256sum >/dev/null
command -v ssh >/dev/null
command -v tar >/dev/null

pve_age_recipient=$(age-keygen -y "$pve_age_identity")
mkdir -p "$pve_recovery_dir"

for pve_endpoint in \
    pve-sbx-1:10.32.20.21 \
    pve-sbx-2:10.32.20.22 \
    pve-sbx-3:10.32.20.23; do
    pve_node=${pve_endpoint%%:*}
    pve_address=${pve_endpoint#*:}
    pve_archive="$pve_recovery_dir/${pve_node}-${pve_archive_stamp}.tar.gz.age"
    pve_partial="${pve_archive}.partial"
    pve_listing="${pve_archive}.members.partial"
    trap 'rm -f "$pve_partial" "$pve_listing"' EXIT INT TERM

    ssh "$pve_ssh_user@$pve_address" 'sudo -n /bin/sh -s' <<'PVE_CAPTURE' | age -r "$pve_age_recipient" -o "$pve_partial"
        set -eu
        pve_capture_dir=$(mktemp -d)
        trap 'rm -rf "$pve_capture_dir"' EXIT INT TERM

        command -v sqlite3 >/dev/null
        mkdir -p "$pve_capture_dir/var/lib/pve-cluster"
        sqlite3 /var/lib/pve-cluster/config.db ".backup $pve_capture_dir/var/lib/pve-cluster/config.db"
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
        cp /proc/cmdline "$pve_capture_dir/kernel-cmdline.txt"

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
            home/kelchm/.ssh/authorized_keys \
            root/.ssh/authorized_keys; do
            test -e "/$pve_path" && set -- "$@" "$pve_path"
        done

        tar -C / -czf - "$@" -C "$pve_capture_dir" .
PVE_CAPTURE

    age -d -i "$pve_age_identity" "$pve_partial" | tar -tzf - >"$pve_listing"
    for pve_required in \
        etc/pve/storage.cfg \
        etc/pve/jobs.cfg \
        etc/pve/corosync.conf \
        etc/pve/priv/authorized_keys \
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
        grep -Fqx "$pve_required" "$pve_listing"
    done
    rm -f "$pve_listing"
    mv "$pve_partial" "$pve_archive"
    trap - EXIT INT TERM
    sha256sum "$pve_archive"
done
