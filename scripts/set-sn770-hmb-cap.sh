#!/usr/bin/env bash
set -Eeuo pipefail

usage() { echo "usage: $0 0|32|128|200" >&2; exit 2; }
[ "$EUID" = 0 ] || { echo "must be root" >&2; exit 1; }
[ "$#" -eq 1 ] || usage
cap=$1
case "$cap" in 0|32|128|200) ;; *) usage ;; esac

expected_node_serial=8CG7466C9K
expected_nvme_serial=23030W800174
grub_defaults=/etc/default/grub
backup=/etc/default/grub.pre-sn770-hmb

controller_serial() {
  nvme id-ctrl /dev/nvme0 |
    awk -F: '/^sn[[:space:]]*:/ {gsub(/[[:space:]]/, "", $2); print $2; exit}'
}

[ "$(cat /sys/class/dmi/id/product_serial)" = "$expected_node_serial" ] || {
  echo "node identity mismatch" >&2
  exit 1
}
[ "$(controller_serial)" = "$expected_nvme_serial" ] || {
  echo "NVMe identity mismatch" >&2
  exit 1
}
[ -f "$grub_defaults" ] || { echo "GRUB defaults not found" >&2; exit 1; }
[ ! -e /etc/kernel/proxmox-boot-uuids ] || {
  echo "proxmox-boot-tool configuration detected; refusing GRUB-only edit" >&2
  exit 1
}

current_line=$(grep -E '^GRUB_CMDLINE_LINUX_DEFAULT=' "$grub_defaults")
case "$current_line" in
  'GRUB_CMDLINE_LINUX_DEFAULT="quiet"'|'GRUB_CMDLINE_LINUX_DEFAULT="quiet nvme.max_host_mem_size_mb=0"'|'GRUB_CMDLINE_LINUX_DEFAULT="quiet nvme.max_host_mem_size_mb=32"'|'GRUB_CMDLINE_LINUX_DEFAULT="quiet nvme.max_host_mem_size_mb=200"') ;;
  *) echo "unexpected GRUB_CMDLINE_LINUX_DEFAULT: $current_line" >&2; exit 1 ;;
esac

if [ "$cap" = 128 ]; then
  target_line='GRUB_CMDLINE_LINUX_DEFAULT="quiet"'
else
  target_line="GRUB_CMDLINE_LINUX_DEFAULT=\"quiet nvme.max_host_mem_size_mb=$cap\""
fi

[ -e "$backup" ] || cp -a "$grub_defaults" "$backup"
escaped_target=${target_line//\//\\/}
sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/$escaped_target/" "$grub_defaults"
grep -Fqx "$target_line" "$grub_defaults"
update-grub

if [ "$cap" = 128 ]; then
  if grep -Fq 'nvme.max_host_mem_size_mb=' /boot/grub/grub.cfg; then
    echo "unexpected HMB override remains in generated GRUB config" >&2
    exit 1
  fi
else
  grep -Fq "nvme.max_host_mem_size_mb=$cap" /boot/grub/grub.cfg
fi

mkdir -p /var/lib/sn770-hmb-config
{
  echo "changed=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "node_serial=$expected_node_serial"
  echo "nvme_serial=$expected_nvme_serial"
  echo "requested_cap_mb=$cap"
  echo "grub_line=$target_line"
  sha256sum "$grub_defaults" "$backup"
} >/var/lib/sn770-hmb-config/last-change
cat /var/lib/sn770-hmb-config/last-change
