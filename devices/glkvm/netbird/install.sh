#!/usr/bin/env bash
# Install or update the GLKVM's pinned NetBird client from a workstation, then
# restart the daemon on it. The peer identity in /etc/kvmd/user/netbird and the
# GL UI integration are unchanged. Restarting drops NetBird connections to the
# appliance for a few seconds; the local console is unaffected.
# Renovate bumps NETBIRD_VERSION; merging that PR changes nothing until this runs.
set -euo pipefail

# renovate: datasource=github-releases depName=netbirdio/netbird
NETBIRD_VERSION=v0.82.1
version=${NETBIRD_VERSION#v}

if (( $# > 1 )); then
    echo "usage: $0 [SSH_TARGET]" >&2
    exit 2
fi

target=${1:-root@10.32.20.10}
src=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

base="https://github.com/netbirdio/netbird/releases/download/v$version"
archive="netbird_${version}_linux_arm64.tar.gz"
fetch() { curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$1" --output "$2"; }
fetch "$base/$archive" "$stage/$archive"
fetch "$base/netbird_${version}_checksums.txt" "$stage/checksums.txt"
(cd "$stage" && grep "  $archive\$" checksums.txt | shasum -a 256 -c --quiet)
tar -xzf "$stage/$archive" -C "$stage" netbird
cp "$src/S40netbird-binary" "$stage/"

# shellcheck disable=SC2016 # expanded on the appliance
remote='
set -eu
stage=$(mktemp -d /tmp/glkvm-netbird.XXXXXX)
trap "rm -rf \"$stage\"" EXIT
tar -xf - -C "$stage"
test "$("$stage/netbird" version)" = "$VERSION"

bin=/etc/kvmd/user/bin/netbird
boot=/etc/kvmd/user/scripts/S40netbird-binary
mkdir -p /etc/kvmd/user/bin /etc/kvmd/user/scripts
cp "$stage/netbird" "$bin.new" && chmod 0755 "$bin.new"
cp "$stage/S40netbird-binary" "$boot.new" && chmod 0755 "$boot.new"

/etc/init.d/S99netbird stop
# The old daemon keeps shutting down for a few seconds after stop returns; a
# new one started meanwhile exits because the socket is still held.
for i in $(seq 1 20); do
    pgrep -x netbird >/dev/null || [ -S /var/run/netbird.sock ] || break
    sleep 1
done
if pgrep -x netbird >/dev/null; then
    echo "old NetBird daemon is still running; nothing replaced" >&2
    rm -f "$bin.new" "$boot.new"
    exit 1
fi
if grep -q " /usr/bin/netbird " /proc/mounts; then umount /usr/bin/netbird; fi
mv -f "$bin.new" "$bin"
mv -f "$boot.new" "$boot"
"$boot" start
/etc/init.d/S99netbird start

echo "running client: $(/usr/bin/netbird version)"
for i in $(seq 1 30); do
    timeout 5 netbird status 2>/dev/null | grep -q "^Management: Connected" && break
    sleep 1
done
timeout 10 netbird status | grep -E "^(Daemon version|Management|Signal|Interface type|Networks):"
'

COPYFILE_DISABLE=1 tar -C "$stage" -cf - netbird S40netbird-binary |
    ssh -o BatchMode=yes "$target" "VERSION=$version; $remote"
