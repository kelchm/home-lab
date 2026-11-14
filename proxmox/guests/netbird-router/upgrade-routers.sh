#!/usr/bin/env bash
# Install the declared NetBird version on both routing peers, one at a time.
# Renovate bumps NETBIRD_ROUTER_VERSION; merging that PR changes no router until
# this runs. Run it from an admin device on Main that can SSH to the PVE nodes.
set -euo pipefail

# renovate: datasource=github-releases depName=netbirdio/netbird
NETBIRD_ROUTER_VERSION=v0.83.1
version=${NETBIRD_ROUTER_VERSION#v}

# PVE node, VMID and NetBird peer name of each routing peer.
routers=(
  "10.32.20.21 103 netbird-router-1"
  "10.32.20.22 104 netbird-router-2"
)

# Run a shell script in a router through the QEMU guest agent. Prints the
# guest's output and fails when the guest command fails or times out.
guest() {
  local node=$1 vmid=$2 timeout=$3 script=$4 result
  result=$(printf '%s\n' "$script" | ssh -o BatchMode=yes -o ConnectTimeout=10 "kelchm@$node" \
    "sudo qm guest exec $vmid --pass-stdin 1 --timeout $timeout -- sh -s")
  if ! jq -e 'has("exitcode")' <<<"$result" >/dev/null; then
    echo "VM $vmid is still running the command after ${timeout}s as PID $(jq -r .pid <<<"$result")." \
      "Wait for 'sudo qm guest exec-status $vmid <PID>' on $node to report it exited before retrying." >&2
    return 1
  fi
  jq -r '."out-data" // empty' <<<"$result"
  jq -r '."err-data" // empty' <<<"$result" >&2
  [ "$(jq -r '.exitcode' <<<"$result")" = 0 ]
}

# A router is serving when it reaches NetBird management and routes all three resources.
# shellcheck disable=SC2016 # it runs in the guest, which expands $s
serving='s=$(netbird status) && echo "$s" | grep -q "^Management: Connected" &&
  echo "$s" | grep "^Networks:" | grep -qF 10.32.130.1/32 &&
  echo "$s" | grep "^Networks:" | grep -qF 10.32.140.1/32 &&
  echo "$s" | grep "^Networks:" | grep -qF 10.32.30.1/32'

for router in "${routers[@]}"; do
  read -r node vmid name <<<"$router"

  # Never take down the last serving router.
  for other in "${routers[@]}"; do
    read -r other_node other_vmid other_name <<<"$other"
    [ "$other_vmid" = "$vmid" ] && continue
    if ! guest "$other_node" "$other_vmid" 30 "$serving" >/dev/null; then
      echo "$other_name is not serving; leaving $name unchanged" >&2
      exit 1
    fi
  done

  echo "$name: installing NetBird $version"
  guest "$node" "$vmid" 300 "set -e
apt-mark unhold netbird >/dev/null 2>&1 || true
apt-get -o Acquire::Retries=2 update -qq
DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=2 install --yes --allow-downgrades netbird=$version >/dev/null
for attempt in \$(seq 24); do if $serving; then break; fi; sleep 5; done
$serving
netbird status | grep -qx 'Daemon version: $version' || { echo 'the running daemon is not $version' >&2; exit 1; }
echo 'NetBird $version running'"
done
