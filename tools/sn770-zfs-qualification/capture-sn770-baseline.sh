#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 6 ]]; then
  echo "usage: $0 HOST KNOWN_HOSTS SSH_KEY OUTPUT_FILE EXPECTED_NODE_SERIAL EXPECTED_NVME_SERIAL" >&2
  exit 2
fi

host=$1
known_hosts=$2
ssh_key=$3
output_file=$4
expected_node_serial=$5
expected_nvme_serial=$6
[[ -r "$known_hosts" ]] || { echo "known_hosts required: $known_hosts" >&2; exit 1; }
ssh_options=(
  -i "$ssh_key"
  -o BatchMode=yes
  -o UserKnownHostsFile="$known_hosts"
  -o StrictHostKeyChecking=yes
)

identity=$(ssh "${ssh_options[@]}" "root@$host" '
  printf "node_serial="
  cat /sys/class/dmi/id/product_serial
  nvme id-ctrl /dev/nvme0n1 | sed -n -e "s/^sn  *: */nvme_serial=/p" -e "s/^fr  *: */nvme_firmware=/p"
')

if ! grep -Fxq "node_serial=$expected_node_serial" <<<"$identity"; then
  echo "refusing capture: expected node serial $expected_node_serial" >&2
  printf '%s\n' "$identity" >&2
  exit 1
fi
if ! grep -Fxq "nvme_serial=$expected_nvme_serial" <<<"$identity"; then
  echo "refusing capture: expected NVMe serial $expected_nvme_serial" >&2
  printf '%s\n' "$identity" >&2
  exit 1
fi

output_dir=$(dirname -- "$output_file")
output_name=$(basename -- "$output_file")
mkdir -p "$output_dir"
if [[ -e "$output_file" || -L "$output_file" ]]; then
  echo "refusing capture: output already exists: $output_file" >&2
  exit 1
fi
output_tmp=$(mktemp "$output_dir/.$output_name.tmp.XXXXXX")
chmod 0600 "$output_tmp"
trap 'rm -f -- "$output_tmp"' EXIT INT TERM

if ! ssh "${ssh_options[@]}" "root@$host" 'bash -s' >"$output_tmp" 2>&1 <<'REMOTE'
set -u

section() {
  printf '\n===== %s =====\n' "$1"
}

run() {
  printf '\n$'
  printf ' %q' "$@"
  printf '\n'
  "$@" 2>&1 || true
}

section identity
run date --iso-8601=seconds
run hostnamectl
run uname -a
run cat /proc/cmdline
run cat /etc/os-release
run cat /sys/class/dmi/id/sys_vendor
run cat /sys/class/dmi/id/product_name
run cat /sys/class/dmi/id/product_serial
run dmidecode

section nvme
run nvme list -v
run nvme id-ctrl /dev/nvme0n1
run nvme id-ctrl -H /dev/nvme0n1
run nvme id-ns -H /dev/nvme0n1
run nvme smart-log /dev/nvme0
run nvme error-log -e 256 /dev/nvme0
run smartctl -x /dev/nvme0

section storage
run lsblk -e7 -o NAME,KNAME,PATH,MODEL,SERIAL,WWN,TRAN,RM,ROTA,SIZE,FSTYPE,FSVER,LABEL,PARTUUID,UUID,MOUNTPOINTS
run blkid
run udevadm info --query=all --name=/dev/nvme0
run udevadm info --query=all --name=/dev/nvme0n1
run cat /sys/block/nvme0n1/queue/logical_block_size
run cat /sys/block/nvme0n1/queue/physical_block_size
run cat /sys/block/nvme0n1/size
run findmnt
run df -hT

section pci
run lspci -nnk
run lspci -s 02:00.0 -nnvv
run lspci -s 02:00.0 -xxxx
run lspci -tv
run find /sys/kernel/iommu_groups -maxdepth 2 -type l -print

section kernel
run cat /proc/modules
run modinfo nvme
run modinfo nvme_core
for parameter in /sys/module/nvme/parameters/* /sys/module/nvme_core/parameters/*; do
  [[ -f "$parameter" ]] || continue
  printf '%s=' "$parameter"
  cat "$parameter"
done
run dmesg -T

section network
run ip -br link
run ip -details link
run ip -br addr
run ip route
run ip -6 route
run cat /etc/resolv.conf
for interface_path in /sys/class/net/*; do
  interface=${interface_path##*/}
  [[ "$interface" == lo ]] && continue
  printf '\n--- interface %s ---\n' "$interface"
  run cat "$interface_path/address"
  run cat "$interface_path/operstate"
  run ethtool -i "$interface"
  run ethtool "$interface"
done

section zfs-and-tools
run zfs --version
run zpool --version
run zpool list -v
run zpool status -v
run zfs list -t all
run smartctl --version
run nvme version

section pve-if-present
run pveversion --verbose
run proxmox-boot-tool status
run dpkg-query -W 'pve-kernel*' 'proxmox-kernel*' 'zfs*' nvme-cli smartmontools intel-microcode
run cat /etc/hostname
run cat /etc/hosts
run cat /etc/network/interfaces
run pvesm status
run journalctl --no-pager -b
run journalctl --no-pager -k -b
REMOTE
then
  echo "baseline capture failed; no output published" >&2
  exit 1
fi

if ! ln "$output_tmp" "$output_file"; then
  echo "refusing capture: output appeared during collection: $output_file" >&2
  exit 1
fi
rm -f -- "$output_tmp"
trap - EXIT INT TERM
sha256sum "$output_file"
