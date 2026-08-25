#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "usage: $0 HOST OUTPUT KNOWN_HOSTS SSH_KEY start|monitor|status|stop|failure" >&2; exit 2; }
[ "$#" -ge 5 ] || usage
host=$1; out=$2; known_hosts=$3; key=$4; action=$5; shift 5
poll_seconds=60
while [ "$#" -gt 0 ]; do
  case "$1" in --poll-seconds) poll_seconds=$2; shift 2;; *) break;; esac
done
remote_script=$(printenv REMOTE_SCRIPT || true)
[ -n "$remote_script" ] || remote_script=/usr/local/sbin/sn770-qualification-host.sh
mkdir -p "$out"
[ -r "$known_hosts" ] || { echo "known_hosts required: $known_hosts" >&2; exit 1; }
remote_command() {
  command_string=
  for arg in "$@"; do printf -v piece ' %q' "$arg"; command_string=$command_string$piece; done
  printf '%s' "$command_string" | sed 's/^ //'
}
raw_ssh() {
  printf '%s raw-ssh %s\n' "$(date -u +%FT%TZ)" "$(remote_command "$@")" >>"$out/observer.log"
  ssh -i "$key" -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" "root@$host" "$(remote_command "$@")"
}
harness_ssh() {
  printf '%s harness %s\n' "$(date -u +%FT%TZ)" "$(remote_command "$remote_script" "$@")" >>"$out/observer.log"
  ssh -i "$key" -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" "root@$host" "$(remote_command "$remote_script" "$@")"
}
collect() {
  run_dir=$1
  for name in manifest smart-baseline.log state workload.log thermal.tsv outcome final-evidence.log failure.log count.sends count.scrubs count.fio; do
    if raw_ssh test -r "$run_dir/$name"; then raw_ssh cat "$run_dir/$name" >"$out/$name"; fi
  done
  raw_ssh journalctl -k --no-pager -n 500 >"$out/kernel-snapshot.log" || true
  raw_ssh zpool iostat -v sn770test 1 1 >>"$out/zpool-iostat.log" || true
  raw_ssh iostat -x 1 1 >>"$out/iostat.log" || true
}
stream_pids=()
start_stream() { stream_name=$1; shift; raw_ssh "$@" >"$out/$stream_name" 2>&1 & stream_pids+=("$!"); }
cleanup() { for pid in "${stream_pids[@]}"; do kill "$pid" 2>/dev/null || true; done; wait 2>/dev/null || true; }
requested_run_dir() {
  requested=/run/sn770-qualification
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --run-dir ]; then
      requested=$2
      shift 2
    else
      shift
    fi
  done
  echo "$requested"
}
case "$action" in
  start) run_dir=$(requested_run_dir "$@"); harness_ssh start "$@" | tee "$out/start.log"; collect "$run_dir" ;;
  monitor)
    trap cleanup EXIT INT TERM
    start_stream kernel.log journalctl -kf -o short-iso-precise
    start_stream zpool-iostat.log zpool iostat -v sn770test 5
    start_stream iostat.log iostat -x 5
    run_dir=$(requested_run_dir "$@")
    while :; do collect "$run_dir"; grep -Eq '^(complete|failed|stopped)' "$out/state" 2>/dev/null && break; sleep "$poll_seconds"; done
    ;;
  status|stop|failure) run_dir=$(requested_run_dir "$@"); harness_ssh "$action" "$@" | tee "$out/$action.log"; collect "$run_dir" ;;
  raw) raw_ssh "$@" ;;
  *) usage ;;
esac
