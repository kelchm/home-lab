#!/bin/sh
set -eu

output=${1:-/tmp/kvm-snap.jpg}
tmp=$(mktemp "${output}.tmp.XXXXXX")
trap 'rm -f "$tmp"' EXIT INT TERM

# Uses kvmd's streamer while a viewer keeps it running; otherwise runs a short-lived ustreamer of its own.
if ! ssh root@10.32.20.10 sh -s >"$tmp" <<'EOF'; then
set -u
kvmd_sock=/run/kvmd/ustreamer.sock
own_sock=/tmp/capture-screen.sock
kvmd_live() { curl -fsS -m 2 --unix-socket "$kvmd_sock" http://localhost/state >/dev/null 2>&1; }
if kvmd_live; then
  exec curl -fsS -m 5 --unix-socket "$kvmd_sock" http://localhost/snapshot
fi
pkill -USR1 -f /usr/sbin/lt86102sxe_setup 2>/dev/null || true
/usr/bin/ustreamer --device=/dev/video0 -r 1920x1080 --venc-format=2 --vi-buffer-num=4 --vi-format=4 \
  --unix="$own_sock" --unix-rm --unix-mode=0600 >/dev/null 2>&1 &
pid=$!
frame=$(mktemp)
rc=1
deadline=$(($(date +%s) + 8))
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 1
  if curl -fsS -m 2 --unix-socket "$own_sock" http://localhost/snapshot -o "$frame" 2>/dev/null; then rc=0; break; fi
done
if [ "$rc" -ne 0 ] && curl -fsS -m 2 --unix-socket "$own_sock" http://localhost/state 2>/dev/null | tr -d ' \n' | grep -q '"hdmi":{"signal":false'; then
  echo 'no HDMI signal on the selected port' >&2
fi
kill "$pid" 2>/dev/null; sleep 1; kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
kvmd_live || pkill -USR2 -f /usr/sbin/lt86102sxe_setup 2>/dev/null || true
[ "$rc" -eq 0 ] && cat "$frame"
rm -f "$frame" "$own_sock"
exit "$rc"
EOF
  printf '%s\n' 'screen capture failed' >&2
  exit 1
fi

mv -f "$tmp" "$output"
trap - EXIT INT TERM

printf '%s\n' "$output"
