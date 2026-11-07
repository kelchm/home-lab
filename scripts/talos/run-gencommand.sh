#!/usr/bin/env bash
# Run the commands `talhelper gencommand <args>` generates, stopping at the
# first one that fails. Piping them into plain `bash` reports only the last
# command's status, so an earlier node's failure passed silently.
#
# With --retry, generate once and rerun the commands every RETRY_INTERVAL
# seconds (default 10) until they succeed, for steps that wait on the cluster.
# A talhelper failure or an empty command list still fails at once.

set -euo pipefail

retry=""
if [[ "${1:-}" == "--retry" ]]; then
    retry=1
    shift
fi

commands="$(talhelper gencommand "$@")"
if [[ -z "${commands//[[:space:]]/}" ]]; then
    echo "talhelper gencommand $* generated no commands" >&2
    exit 1
fi
if [[ -z "${retry}" ]]; then
    bash -euo pipefail <<<"${commands}"
    exit
fi
until bash -euo pipefail <<<"${commands}"; do
    sleep "${RETRY_INTERVAL:-10}"
done
