#!/bin/sh
set -eu

output=${1:-/tmp/kvm-snap.jpg}
tmp=$(mktemp "${output}.tmp.XXXXXX")
trap 'rm -f "$tmp"' EXIT INT TERM

if ! ssh root@10.32.20.10 \
  'curl -fsS --unix-socket /run/kvmd/ustreamer.sock http://localhost/snapshot' \
  >"$tmp"; then
  printf '%s\n' \
    'screen capture failed; open the authenticated KVM viewer to start its demand-driven ustreamer, then retry' >&2
  exit 1
fi

mv -f "$tmp" "$output"
trap - EXIT INT TERM

printf '%s\n' "$output"
