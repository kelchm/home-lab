#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat >&2 <<'EOF'
usage: sn770-send-screen.sh start|worker|status|stop [options]

Required for start/worker:
  --node-serial SERIAL       Expected DMI product serial
  --nvme-serial SERIAL       Expected SN770 serial
  --firmware VERSION         Expected firmware revision
  --hmb-cap MB               Expected nvme max_host_mem_size_mb value
  --hmb-alloc MB             Expected allocation reported by the kernel
  --hmb-segments N           Expected live HMB descriptor count (optional)
  --logical-block-size BYTES Expected live namespace logical block size (optional)

Options:
  --run-dir PATH             Evidence directory (default: /var/lib/sn770-send-screen)
  --zfs-root PATH            Isolated ZFS userspace root (optional)
  --workload send|scrub|mixed
                              Workload to run (default: send)
  --count N                  Completed workload iterations required (default: 1)
  --send-count N             Backward-compatible alias for --count
  --composite-stop C         Stop at this composite temperature (default: 80)
  --sensor-stop C            Stop when any sensor reaches this value (default: 90)
  --poll-seconds N           Monitor interval (default: 2)
EOF
  exit 2
}

[ "$EUID" = 0 ] || { echo "must be root" >&2; exit 1; }
[ "$#" -gt 0 ] || usage
action=$1
shift

run_dir=/var/lib/sn770-send-screen
node_serial=
nvme_serial=
firmware=
hmb_cap=
hmb_alloc=
hmb_segments=
logical_block_size=
zfs_root=
workload=send
target_count=1
composite_stop=80
sensor_stop=90
poll_seconds=2

while [ "$#" -gt 0 ]; do
  case "$1" in
    --run-dir) run_dir=$2; shift 2 ;;
    --node-serial) node_serial=$2; shift 2 ;;
    --nvme-serial) nvme_serial=$2; shift 2 ;;
    --firmware) firmware=$2; shift 2 ;;
    --hmb-cap) hmb_cap=$2; shift 2 ;;
    --hmb-alloc) hmb_alloc=$2; shift 2 ;;
    --hmb-segments) hmb_segments=$2; shift 2 ;;
    --logical-block-size) logical_block_size=$2; shift 2 ;;
    --zfs-root) zfs_root=$2; shift 2 ;;
    --workload) workload=$2; shift 2 ;;
    --count|--send-count) target_count=$2; shift 2 ;;
    --composite-stop) composite_stop=$2; shift 2 ;;
    --sensor-stop) sensor_stop=$2; shift 2 ;;
    --poll-seconds) poll_seconds=$2; shift 2 ;;
    *) usage ;;
  esac
done

if [ -n "$zfs_root" ]; then
  [ -x "$zfs_root/sbin/zfs" ] || { echo "missing isolated zfs: $zfs_root/sbin/zfs" >&2; exit 1; }
  [ -x "$zfs_root/sbin/zpool" ] || { echo "missing isolated zpool: $zfs_root/sbin/zpool" >&2; exit 1; }
  export PATH="$zfs_root/sbin:$zfs_root/bin:$PATH"
  export LD_LIBRARY_PATH="$zfs_root/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

pool=sn770test
snapshot=sn770test/payload@seed
state_file=$run_dir/state
stop_file=$run_dir/stop.request
outcome_file=$run_dir/outcome
unit_file=$run_dir/unit
workload_log=$run_dir/workload.log
thermal_log=$run_dir/thermal.tsv
kernel_log=$run_dir/kernel.log

timestamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

atomic_write() {
  local target=$1 value=$2 tmp
  tmp=$(mktemp "$target.tmp.XXXXXX")
  printf '%s\n' "$value" >"$tmp"
  mv -f "$tmp" "$target"
}

controller_value() {
  nvme id-ctrl /dev/nvme0 2>/dev/null |
    awk -F: -v field="$1" '$1 ~ "^" field "[[:space:]]*$" {gsub(/[[:space:]]/, "", $2); print $2; exit}'
}

observed_hmb() {
  journalctl -k -b --no-pager 2>/dev/null |
    sed -nE 's/.*allocated ([0-9]+) MiB host memory buffer.*/\1/p' | tail -1
}

require_parameters() {
  local name
  for name in node_serial nvme_serial firmware hmb_cap hmb_alloc; do
    [ -n "${!name}" ] || { echo "missing --${name//_/-}" >&2; return 1; }
  done
  [ "$workload" = send ] || [ "$workload" = scrub ] || [ "$workload" = mixed ]
  [[ "$target_count" =~ ^[1-9][0-9]*$ ]]
  [[ "$composite_stop" =~ ^[1-9][0-9]*$ ]]
  [[ "$sensor_stop" =~ ^[1-9][0-9]*$ ]]
  [[ "$poll_seconds" =~ ^[1-9][0-9]*$ ]]
  [ -z "$hmb_segments" ] || [[ "$hmb_segments" =~ ^[1-9][0-9]*$ ]]
  [ -z "$logical_block_size" ] || [[ "$logical_block_size" =~ ^[1-9][0-9]*$ ]]
}

guard() {
  local actual_node actual_nvme actual_fw actual_cap actual_alloc actual_segments actual_logical_block_size disk_path
  local pool_paths pool_health snapshots
  actual_node=$(cat /sys/class/dmi/id/product_serial)
  actual_nvme=$(controller_value sn)
  actual_fw=$(controller_value fr)
  actual_cap=$(cat /sys/module/nvme/parameters/max_host_mem_size_mb)
  actual_alloc=$(observed_hmb)

  [ "$actual_node" = "$node_serial" ] || { echo "node identity mismatch: $actual_node" >&2; return 1; }
  [ "$actual_nvme" = "$nvme_serial" ] || { echo "NVMe serial mismatch: $actual_nvme" >&2; return 1; }
  [ "$actual_fw" = "$firmware" ] || { echo "firmware mismatch: $actual_fw" >&2; return 1; }
  [ "$actual_cap" = "$hmb_cap" ] || { echo "HMB cap mismatch: $actual_cap" >&2; return 1; }
  if [ "$hmb_alloc" = 0 ]; then
    [ -z "$actual_alloc" ] || [ "$actual_alloc" = 0 ] || {
      echo "HMB allocation mismatch: $actual_alloc" >&2
      return 1
    }
  else
    [ "$actual_alloc" = "$hmb_alloc" ] || { echo "HMB allocation mismatch: ${actual_alloc:-none}" >&2; return 1; }
  fi
  if [ -n "$hmb_segments" ]; then
    actual_segments=$(nvme get-feature /dev/nvme0 -f 0x0d -H 2>/dev/null |
      sed -nE 's/.*Host Memory Descriptor List Entry Count \(HMDLEC\):[[:space:]]*([0-9]+).*/\1/p')
    [ "$actual_segments" = "$hmb_segments" ] || {
      echo "HMB descriptor-count mismatch: ${actual_segments:-none}" >&2
      return 1
    }
  fi
  if [ -n "$logical_block_size" ]; then
    actual_logical_block_size=$(cat /sys/block/nvme0n1/queue/logical_block_size)
    [ "$actual_logical_block_size" = "$logical_block_size" ] || {
      echo "logical-block-size mismatch: $actual_logical_block_size" >&2
      return 1
    }
  fi

  disk_path=/dev/disk/by-id/nvme-WD_BLACK_SN770_1TB_$nvme_serial
  [ "$(readlink -f "$disk_path")" = /dev/nvme0n1 ] || { echo "persistent disk identity mismatch" >&2; return 1; }
  pool_paths=$(zpool status -P "$pool") || { echo "pool does not use expected drive" >&2; return 1; }
  grep -Fq "$disk_path" <<<"$pool_paths" || { echo "pool does not use expected drive" >&2; return 1; }
  pool_health=$(zpool status -x "$pool") || { echo "pool is not healthy" >&2; return 1; }
  [ "$pool_health" = "pool '$pool' is healthy" ] || { echo "pool is not healthy" >&2; return 1; }
  snapshots=$(zfs list -H -t snapshot -o name) || { echo "snapshot is missing" >&2; return 1; }
  grep -Fxq "$snapshot" <<<"$snapshots" || { echo "snapshot is missing" >&2; return 1; }
}

json_field() {
  local input=$1 field=$2
  awk -F: -v field="\"$field\"" '
    $1 ~ "^[[:space:]]*" field "[[:space:]]*$" {
      gsub(/[^0-9-]/, "", $2)
      print $2
      exit
    }
  ' <<<"$input"
}

smart_sample() {
  local smart=$1 composite_k composite_c warning max_sensor_k max_sensor_c sensor
  composite_k=$(json_field "$smart" temperature)
  warning=$(json_field "$smart" critical_warning)
  max_sensor_k=0
  while IFS= read -r sensor; do
    [ -n "$sensor" ] || continue
    [ "$sensor" -le "$max_sensor_k" ] || max_sensor_k=$sensor
  done < <(awk -F: '$1 ~ /^[[:space:]]*"temperature_sensor_[0-9]+"[[:space:]]*$/ {gsub(/[^0-9-]/, "", $2); print $2}' <<<"$smart")
  composite_c=NA
  max_sensor_c=NA
  [ -z "$composite_k" ] || composite_c=$((composite_k - 273))
  [ "$max_sensor_k" -eq 0 ] || max_sensor_c=$((max_sensor_k - 273))
  printf '%s\t%s\t%s\n' "$composite_c" "$max_sensor_c" "${warning:-NA}"
}

capture_final() {
  {
    echo '[identity]'
    uname -a
    cat /proc/cmdline
    echo '[hmb-feature]'
    timeout 5 nvme get-feature /dev/nvme0 -f 0x0d -H || true
    echo '[namespace]'
    timeout 5 nvme id-ns -H /dev/nvme0n1 || true
    timeout 5 cat /sys/block/nvme0n1/queue/logical_block_size || true
    echo '[zfs-version]'
    timeout 5 zfs --version || true
    timeout 5 nvme id-ctrl /dev/nvme0 || true
    echo '[smart]'
    timeout 5 nvme smart-log /dev/nvme0 || true
    echo '[error-log]'
    timeout 5 nvme error-log /dev/nvme0 -e 256 || true
    echo '[pool]'
    timeout 5 zpool status -v "$pool" || true
    echo '[kernel-tail]'
    journalctl -k -b --no-pager -n 500
  } >"$run_dir/final-evidence.log" 2>&1 || true
}

start_run() {
  local unit script_path
  local -a extra_args=()
  require_parameters
  guard
  if [ -e "$run_dir/manifest" ]; then
    echo "run directory already contains evidence: $run_dir" >&2
    return 1
  fi
  mkdir -p "$run_dir"
  unit=sn770-send-screen-$(date +%s)
  atomic_write "$unit_file" "$unit"
  [ -z "$hmb_segments" ] || extra_args+=(--hmb-segments "$hmb_segments")
  [ -z "$logical_block_size" ] || extra_args+=(--logical-block-size "$logical_block_size")
  [ -z "$zfs_root" ] || extra_args+=(--zfs-root "$zfs_root")
  script_path=$(readlink -f "${BASH_SOURCE[0]}")
  [ -x "$script_path" ] || { echo "harness path is not executable: $script_path" >&2; return 1; }
  systemd-run --unit="$unit" --collect --service-type=exec \
    --property=KillMode=control-group --property=TimeoutStopSec=20 \
    "$script_path" worker \
    --run-dir "$run_dir" --node-serial "$node_serial" --nvme-serial "$nvme_serial" \
    --firmware "$firmware" --hmb-cap "$hmb_cap" --hmb-alloc "$hmb_alloc" \
    --workload "$workload" --count "$target_count" --composite-stop "$composite_stop" \
    --sensor-stop "$sensor_stop" --poll-seconds "$poll_seconds" "${extra_args[@]}"
}

worker() {
  local completed_count=0 workload_pid='' secondary_pid='' sample smart composite max_sensor warning
  local workload_rc secondary_rc reason count_name
  require_parameters
  guard
  mkdir -p "$run_dir"
  rm -f "$stop_file" "$outcome_file"
  : >"$workload_log"
  journalctl -kf -o short-iso-precise >"$kernel_log" 2>&1 &
  local journal_pid=$!
  trap 'kill "$journal_pid" "$workload_pid" "$secondary_pid" 2>/dev/null || true' EXIT INT TERM

  case "$workload" in
    send) count_name=sends ;;
    scrub) count_name=scrubs ;;
    mixed) count_name=mixed ;;
  esac

  cat >"$run_dir/manifest" <<EOF
started=$(timestamp)
node_serial=$node_serial
nvme_serial=$nvme_serial
firmware=$firmware
kernel=$(uname -r)
cmdline=$(cat /proc/cmdline)
hmb_cap_mb=$hmb_cap
hmb_alloc_mb=$hmb_alloc
hmb_segments_expected=${hmb_segments:-not-checked}
logical_block_size_expected=${logical_block_size:-not-checked}
logical_block_size_observed=$(cat /sys/block/nvme0n1/queue/logical_block_size)
zfs_root=${zfs_root:-system}
zfs_versions=$(zfs --version 2>&1 | paste -sd ';' -)
workload=$workload
target_count=$target_count
composite_stop_c=$composite_stop
sensor_stop_c=$sensor_stop
poll_seconds=$poll_seconds
EOF
  nvme smart-log /dev/nvme0 >"$run_dir/smart-baseline.log"
  printf 'timestamp\tcomposite_c\tmax_sensor_c\tcritical_warning\n' >"$thermal_log"
  atomic_write "$state_file" running

  while [ "$completed_count" -lt "$target_count" ]; do
    reason=
    secondary_pid=
    printf '%s %s_start count=%s\n' "$(timestamp)" "$workload" "$((completed_count + 1))" >>"$workload_log"
    case "$workload" in
      send)
        zfs send "$snapshot" >/dev/null 2>>"$workload_log" &
        workload_pid=$!
        ;;
      scrub)
        zpool scrub -w "$pool" >>"$workload_log" 2>&1 &
        workload_pid=$!
        ;;
      mixed)
        zfs send "$snapshot" >/dev/null 2>>"$workload_log" &
        workload_pid=$!
        zpool scrub -w "$pool" >>"$workload_log" 2>&1 &
        secondary_pid=$!
        ;;
    esac
    while kill -0 "$workload_pid" 2>/dev/null ||
        { [ -n "$secondary_pid" ] && kill -0 "$secondary_pid" 2>/dev/null; }; do
      if [ -e "$stop_file" ]; then
        reason=operator_stop
        break
      fi
      if [ ! -e /dev/nvme0n1 ]; then
        reason=namespace_disappeared
        break
      fi
      smart=$(timeout 5 nvme smart-log -o json /dev/nvme0 2>/dev/null) || { reason=smart_unreadable; break; }
      sample=$(smart_sample "$smart")
      IFS=$'\t' read -r composite max_sensor warning <<<"$sample"
      printf '%s\t%s\n' "$(timestamp)" "$sample" >>"$thermal_log"
      if [ "$warning" != 0 ] && [ "$warning" != 0x0 ]; then
        reason=thermal_critical_warning
        break
      fi
      if [ "$composite" != NA ] && [ "$composite" -ge "$composite_stop" ]; then
        reason=thermal_composite_${composite}C
        break
      fi
      if [ "$max_sensor" != NA ] && [ "$max_sensor" -ge "$sensor_stop" ]; then
        reason=thermal_sensor_${max_sensor}C
        break
      fi
      kernel_window=$(journalctl -k -b --since=-5seconds --no-pager 2>/dev/null || true)
      if grep -Eqi 'nvme.*(timeout|reset|reset failed|controller (down|not ready)|CSTS[ =:]*(0xffffffff|all ones)|ENODEV|I/O error)|((AER|PCIe|pcie).*(error|link down))' <<<"$kernel_window"; then
        reason=kernel_nvme_or_pcie_error
        break
      fi
      sleep "$poll_seconds"
    done

    if [ -n "${reason:-}" ]; then
      case "$reason" in
        thermal_*|operator_stop) atomic_write "$state_file" "stopped reason=$reason $count_name=$completed_count" ;;
        *) atomic_write "$state_file" "failed reason=$reason $count_name=$completed_count" ;;
      esac
      atomic_write "$outcome_file" "$reason"
      if [[ "$reason" = thermal_* || "$reason" = operator_stop ]]; then
        kill "$workload_pid" "$secondary_pid" 2>/dev/null || true
        [ "$workload" = send ] || zpool scrub -s "$pool" >>"$workload_log" 2>&1 || true
      fi
      wait "$workload_pid" "$secondary_pid" 2>/dev/null || true
      capture_final
      kill "$workload_pid" "$secondary_pid" 2>/dev/null || true
      kill "$journal_pid" 2>/dev/null || true
      trap - EXIT INT TERM
      case "$reason" in
        thermal_*|operator_stop) return 0 ;;
        *) return 1 ;;
      esac
    fi

    set +e
    wait "$workload_pid"
    workload_rc=$?
    secondary_rc=0
    if [ -n "$secondary_pid" ]; then
      wait "$secondary_pid"
      secondary_rc=$?
    fi
    set -e
    if [ "$workload_rc" -ne 0 ] || [ "$secondary_rc" -ne 0 ]; then
      atomic_write "$outcome_file" "${workload}_failed"
      atomic_write "$state_file" "failed reason=${workload}_failed $count_name=$completed_count"
      capture_final
      return 1
    fi
    completed_count=$((completed_count + 1))
    atomic_write "$run_dir/count.$count_name" "$completed_count"
    printf '%s %s_complete count=%s\n' "$(timestamp)" "$workload" "$completed_count" >>"$workload_log"
  done

  pool_health=$(zpool status -x "$pool" 2>/dev/null || true)
  [ "$pool_health" = "pool '$pool' is healthy" ] || {
    atomic_write "$outcome_file" pool_not_healthy
    atomic_write "$state_file" "failed reason=pool_not_healthy $count_name=$completed_count"
    capture_final
    return 1
  }
  atomic_write "$outcome_file" completed
  atomic_write "$state_file" "complete $count_name=$completed_count"
  capture_final
  kill "$journal_pid" 2>/dev/null || true
  wait "$journal_pid" 2>/dev/null || true
  trap - EXIT INT TERM
}

case "$action" in
  start) start_run ;;
  worker) worker ;;
  status)
    [ -r "$state_file" ] && cat "$state_file" || echo no_run
    [ -r "$thermal_log" ] && tail -2 "$thermal_log" || true
    ;;
  stop)
    mkdir -p "$run_dir"
    atomic_write "$stop_file" "$(timestamp)"
    ;;
  *) usage ;;
esac
