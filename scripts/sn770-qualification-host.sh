#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "usage: $0 start|worker|status|stop|failure [options]" >&2; exit 2; }
[ "$EUID" = 0 ] || { echo must_be_root >&2; exit 1; }
action=; [ "$#" -gt 0 ] && action=$1; shift || true
run_dir=/run/sn770-qualification
node_serial='' nvme_serial='' firmware='' hmb_cap='' hmb_alloc=''
duration=172800 send_target=10 scrub_target=5 churn_runtime=14400
while [ "$#" -gt 0 ]; do
  case "$1" in
    --run-dir) run_dir=$2; shift 2;;
    --node-serial) node_serial=$2; shift 2;;
    --nvme-serial) nvme_serial=$2; shift 2;;
    --firmware) firmware=$2; shift 2;;
    --hmb-cap) hmb_cap=$2; shift 2;;
    --hmb-alloc) hmb_alloc=$2; shift 2;;
    --duration) duration=$2; shift 2;;
    --send-count) send_target=$2; shift 2;;
    --scrub-count) scrub_target=$2; shift 2;;
    --churn-runtime) churn_runtime=$2; shift 2; [ "$churn_runtime" = 4h ] && churn_runtime=14400;;
    *) usage;;
  esac
done
pool=sn770test
snapshot=sn770test/payload@seed
state_file=$run_dir/state
failure_file=$run_dir/failure.reason
stop_file=$run_dir/stop.request
unit_file=$run_dir/unit
log=$run_dir/workload.log

timestamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
atomic_write() {
  target=$1
  tmp=$(mktemp "$target.tmp.XXXXXX")
  printf '%s\n' "$2" >"$tmp"
  mv -f "$tmp" "$target"
}
count_file() { echo "$run_dir/count.$1"; }
read_count() { [ -r "$1" ] && cat "$1" || echo 0; }
write_count() { atomic_write "$(count_file "$1")" "$2"; }
request_failure() { [ -e "$failure_file" ] || atomic_write "$failure_file" "$1"; }
is_stopping() { [ -e "$failure_file" ] || [ -e "$stop_file" ]; }
controller_value() {
  nvme id-ctrl /dev/nvme0 2>/dev/null |
    awk -F: -v field="$1" '$1 ~ "^" field "[[:space:]]*$" {gsub(/[[:space:]]/,"",$2); print $2; exit}'
}
observed_hmb() {
  dmesg --color=never 2>/dev/null |
    sed -nE 's/.*allocated ([0-9]+) MiB host memory buffer.*/\1/p' | tail -1
}
guard() {
  actual_node=$(cat /sys/class/dmi/id/product_serial)
  actual_nvme=$(controller_value sn)
  actual_fw=$(controller_value fr)
  actual_cap=$(cat /sys/module/nvme/parameters/max_host_mem_size_mb)
  actual_alloc=$(observed_hmb)
  [ -n "$node_serial" ] && [ "$actual_node" = "$node_serial" ] || { echo identity_mismatch >&2; return 1; }
  [ -n "$nvme_serial" ] && [ "$actual_nvme" = "$nvme_serial" ] || { echo nvme_serial_mismatch >&2; return 1; }
  [ -n "$firmware" ] && [ "$actual_fw" = "$firmware" ] || { echo firmware_mismatch >&2; return 1; }
  [ "$actual_cap" = "$hmb_cap" ] || { echo hmb_cap_mismatch >&2; return 1; }
  if [ "$hmb_cap" != 128 ]; then
    grep -Eq '(^|[[:space:]])nvme\.max_host_mem_size_mb='"$hmb_cap"'([[:space:]]|$)' /proc/cmdline ||
      { echo hmb_cmdline_mismatch >&2; return 1; }
  fi
  if [ "$hmb_alloc" = 0 ]; then
    [ -z "$actual_alloc" ] || { echo hmb_allocation_mismatch >&2; return 1; }
  else
    [ "$actual_alloc" = "$hmb_alloc" ] || { echo hmb_allocation_mismatch >&2; return 1; }
  fi
  zpool status -x "$pool" 2>/dev/null | grep -Fqx "pool '$pool' is healthy" ||
    { echo pool_not_healthy >&2; return 1; }
  zfs list -H -t snapshot -o name | grep -Fxq "$snapshot" ||
    { echo snapshot_missing >&2; return 1; }
  mountpoint=$(zfs list -H -o mountpoint sn770test/churn)
  [ "$mountpoint" != / ] && [[ "$mountpoint" == /* ]] || { echo unsafe_mount >&2; return 1; }
  disk_path=/dev/disk/by-id/nvme-WD_BLACK_SN770_1TB_$nvme_serial
  [ -n "$disk_path" ] && [ "$(readlink -f "$disk_path")" = /dev/nvme0n1 ] || { echo exact_by_id_disk_missing >&2; return 1; }
  zpool status -P "$pool" | grep -Fq "$disk_path" || { echo pool_disk_identity_mismatch >&2; return 1; }
}
diagnostics() {
  mkdir -p "$run_dir"
  {
    echo "reason=$(cat "$failure_file" 2>/dev/null || echo unknown)"
    echo '[kernel]'; timeout 5 dmesg --color=never | tail -200 || true
    echo '[nvme-state]'; timeout 5 cat /sys/class/nvme/nvme0/state || true
    echo '[smart]'; timeout 5 nvme smart-log /dev/nvme0 || true
    echo '[smartctl]'; timeout 5 smartctl -x /dev/nvme0 || true
    echo '[pci]'; timeout 5 lspci -nnvv || true
    echo '[zpool]'; timeout 5 zpool status -v "$pool" || true
    echo '[zpool-events]'; timeout 5 zpool events -v || true
  } >"$run_dir/failure.log" 2>&1 || true
}
start_run() {
  mkdir -p "$run_dir"
  guard
  if [ -r "$unit_file" ] && systemctl is-active --quiet "$(cat "$unit_file")"; then
    echo run_already_active >&2; return 1
  fi
  rm -f "$state_file" "$failure_file" "$stop_file" "$run_dir/failure.log" "$log" "$run_dir"/count.*
  : >"$log"
  namespace_bytes=$(blockdev --getsize64 /dev/nvme0n1)
  smart_baseline=$(nvme smart-log /dev/nvme0 2>/dev/null || true)
  kernel=$(uname -r)
  cmdline=$(cat /proc/cmdline)
  scheduler=$(cat /sys/block/nvme0n1/queue/scheduler)
  smart_warning=$(printf '%s\n' "$smart_baseline" | awk '/critical_warning/ {print $3; exit}')
  smart_media=$(printf '%s\n' "$smart_baseline" | awk '/media_errors/ {print $3; exit}')
  warning_temperature=$(nvme id-ctrl /dev/nvme0 2>/dev/null | awk '/wctemp/ {print $3; exit}')
  hmb_boot_line=$(dmesg --color=never | grep -Ei 'host memory buffer|HMB' | tail -1 || true)
  printf 'run_started=%s\nnode_serial=%s\nnvme_serial=%s\nfirmware=%s\nhmb_cap_mb=%s\nhmb_alloc_mb=%s\nduration_seconds=%s\nsend_target=%s\nscrub_target=%s\nchurn_runtime_seconds=%s\nkernel=%s\ncmdline=%s\nscheduler=%s\nnamespace_bytes=%s\nsmart_critical_warning=%s\nsmart_media_errors=%s\nwarning_temperature=%s\nhmb_boot_line=%s\n' \
    "$(timestamp)" "$node_serial" "$nvme_serial" "$firmware" "$hmb_cap" "$hmb_alloc" "$duration" "$send_target" "$scrub_target" "$churn_runtime" "$kernel" "$cmdline" "$scheduler" "$namespace_bytes" "$smart_warning" "$smart_media" "$warning_temperature" "$hmb_boot_line" >"$run_dir/manifest"
  printf '%s\n' "$smart_baseline" >>"$run_dir/smart-baseline.log"
  unit=sn770-qualification-$(date +%s)
  atomic_write "$unit_file" "$unit"
  systemd-run --unit="$unit" --collect --service-type=exec \
    --property=KillMode=control-group --property=TimeoutStopSec=30 \
    "${BASH_SOURCE[0]}" worker --run-dir "$run_dir" --node-serial "$node_serial" --nvme-serial "$nvme_serial" \
    --firmware "$firmware" --hmb-cap "$hmb_cap" --hmb-alloc "$hmb_alloc" --duration "$duration" \
    --send-count "$send_target" --scrub-count "$scrub_target" --churn-runtime "$churn_runtime"
}
worker() {
  guard
  start_epoch=$(date +%s)
  finalized=0
  write_count sends 0; write_count scrubs 0; write_count fio 0
  atomic_write "$state_file" running
  finish() {
    [ "$finalized" = 1 ] && return
    if [ -e "$failure_file" ]; then
      diagnostics
      atomic_write "$state_file" "failed sends=$(read_count "$(count_file sends)") scrubs=$(read_count "$(count_file scrubs)") fio=$(read_count "$(count_file fio)") reason=$(cat "$failure_file")"
    else
      atomic_write "$state_file" "stopped sends=$(read_count "$(count_file sends)") scrubs=$(read_count "$(count_file scrubs)") fio=$(read_count "$(count_file fio)")"
    fi
    finalized=1
  }
  trap finish EXIT INT TERM
  send_worker() {
    count=0
    while ! is_stopping; do
      elapsed=$(($(date +%s) - start_epoch))
      [ "$elapsed" -ge "$duration" ] && [ "$count" -ge "$send_target" ] && break
      printf '%s send_start count=%s\n' "$(timestamp)" "$((count + 1))" >>"$log"
      zfs send "$snapshot" >/dev/null 2>>"$log" || { request_failure zfs_send_failed; return; }
      printf '%s send_complete count=%s\n' "$(timestamp)" "$((count + 1))" >>"$log"
      count=$((count + 1)); write_count sends "$count"
    done
  }
  scrub_worker() {
    count=0
    while ! is_stopping; do
      elapsed=$(($(date +%s) - start_epoch))
      [ "$elapsed" -ge "$duration" ] && [ "$count" -ge "$scrub_target" ] && break
      printf '%s scrub_start count=%s\n' "$(timestamp)" "$((count + 1))" >>"$log"
      zpool scrub -w "$pool" >>"$log" 2>&1 || { request_failure zpool_scrub_failed; return; }
    zpool status -xv "$pool" >>"$log" 2>&1 || { request_failure zpool_status_failed; return; }
    zpool status -xv "$pool" | grep -Eq 'errors: No known data errors' || { request_failure zpool_data_error; return; }
      printf '%s scrub_complete count=%s\n' "$(timestamp)" "$((count + 1))" >>"$log"
      count=$((count + 1)); write_count scrubs "$count"
    done
  }
  churn_worker() {
    next_churn=$start_epoch
    mountpoint=$(zfs list -H -o mountpoint sn770test/churn)
    count=0
    while ! is_stopping; do
      now=$(date +%s)
      remaining=$((duration - (now - start_epoch)))
      [ "$remaining" -le 0 ] && break
      if [ "$now" -lt "$next_churn" ]; then sleep 5; continue; fi
      runtime=$churn_runtime; [ "$runtime" -gt "$remaining" ] && runtime=$remaining
      fio --name=sn770-churn --directory="$mountpoint" --size=16G --numjobs=4 \
        --rw=randrw --rwmixread=70 --bs=128K --ioengine=io_uring --iodepth=16 \
        --direct=1 --time_based --runtime="$runtime" --fsync=64 --group_reporting >>"$log" 2>&1 ||
        { request_failure fio_failed; return; }
      count=$((count + 1)); write_count fio "$count"
      next_churn=$((next_churn + 86400))
    done
  }
  watch_worker() {
    initial_warning=$(nvme smart-log /dev/nvme0 2>/dev/null | awk '/critical_warning/ {print $3; exit}')
    initial_media=$(nvme smart-log /dev/nvme0 2>/dev/null | awk '/media_errors/ {print $3; exit}')
    warning_temp=$(nvme id-ctrl /dev/nvme0 2>/dev/null | awk '/wctemp/ {print $3; exit}')
    [ "$warning_temp" -gt 200 ] 2>/dev/null && warning_temp=$((warning_temp - 273)) || true
    journal_since=$start_epoch
    while ! is_stopping; do
      if journalctl -k --since="@$journal_since" --no-pager 2>/dev/null |
          grep -Eqi 'nvme.*(timeout|reset|reset failed|controller (down|not ready)|not ready|CSTS[ =:]*(0xffffffff|all ones)|ENODEV|namespace|abort|I/O error)|((AER|PCIe|pcie).*(error|link|down|reset))'; then
        request_failure kernel_nvme_pcie_error; return
      fi
      journal_since=$(( $(date +%s) - 1 ))
      [ -e /dev/nvme0n1 ] || { request_failure namespace_disappeared; return; }
      current_bytes=$(blockdev --getsize64 /dev/nvme0n1 2>/dev/null || echo 0)
      expected_bytes=$(cat "$run_dir/namespace_bytes" 2>/dev/null || echo 0)
      [ "$current_bytes" = "$expected_bytes" ] || { request_failure namespace_capacity_changed; return; }
      pool_status=$(zpool status "$pool" 2>&1) || { request_failure zpool_not_online_or_error; return; }
      printf '%s\n' "$pool_status" | grep -Eq 'state: +ONLINE' || { request_failure zpool_not_online_or_error; return; }
      printf '%s\n' "$pool_status" | grep -Eqi 'permanent error|suspended' && { request_failure zpool_permanent_or_suspended; return; }
      printf '%s\n' "$pool_status" | grep -Eq 'errors: No known data errors' || { request_failure zpool_data_error; return; }
      current_warning=$(nvme smart-log /dev/nvme0 2>/dev/null | awk '/critical_warning/ {print $3; exit}')
      current_media=$(nvme smart-log /dev/nvme0 2>/dev/null | awk '/media_errors/ {print $3; exit}')
      current_temp=$(nvme smart-log /dev/nvme0 2>/dev/null | awk '/temperature/ {print $3; exit}')
      [ "$current_warning" = 0 ] && [ "$initial_warning" = 0 ] || { request_failure smart_critical_warning; return; }
      [ -n "$current_media" ] && [ "$current_media" = "$initial_media" ] || { request_failure smart_media_errors_increased; return; }
      [ -z "$current_temp" ] || [ -z "$warning_temp" ] || [ "$current_temp" -lt "$warning_temp" ] ||
        { request_failure temperature_at_warning; return; }
      sleep 1
    done
  }
  atomic_write "$run_dir/namespace_bytes" "$(blockdev --getsize64 /dev/nvme0n1)"
  send_worker & send_pid=$!; scrub_worker & scrub_pid=$!; churn_worker & churn_pid=$!; watch_worker & watch_pid=$!
  while :; do
    is_stopping && break
    elapsed=$(($(date +%s) - start_epoch))
    [ "$elapsed" -ge "$duration" ] &&
      [ "$(read_count "$(count_file sends)")" -ge "$send_target" ] &&
      [ "$(read_count "$(count_file scrubs)")" -ge "$scrub_target" ] && break
    sleep 1
  done
  if [ -e "$failure_file" ] || [ -e "$stop_file" ]; then
    kill "$send_pid" "$scrub_pid" "$churn_pid" "$watch_pid" 2>/dev/null || true
    wait || true
    finish
    trap - EXIT INT TERM
    [ -e "$failure_file" ] && exit 1 || exit 0
  fi
  kill "$watch_pid" 2>/dev/null || true
  wait "$watch_pid" 2>/dev/null || true
  wait "$send_pid" "$scrub_pid" "$churn_pid" || true
  if [ "$(($(date +%s) - start_epoch))" -ge "$duration" ] &&
     [ "$(read_count "$(count_file sends)")" -ge "$send_target" ] &&
     [ "$(read_count "$(count_file scrubs)")" -ge "$scrub_target" ]; then
    finalized=1; trap - EXIT INT TERM
    atomic_write "$state_file" "complete sends=$(read_count "$(count_file sends)") scrubs=$(read_count "$(count_file scrubs)") fio=$(read_count "$(count_file fio)")"
  else
    request_failure worker_incomplete; finish; trap - EXIT INT TERM; exit 1
  fi
}
case "$action" in
  start) start_run;;
  worker) worker;;
  status) [ -r "$state_file" ] && cat "$state_file" || echo no_run;;
  stop) atomic_write "$stop_file" "$(timestamp)"; [ -r "$unit_file" ] && systemctl stop "$(cat "$unit_file")" || true;;
  failure) [ -e "$failure_file" ] || request_failure manual_failure; diagnostics; cat "$run_dir/failure.log";;
  *) usage;;
esac
