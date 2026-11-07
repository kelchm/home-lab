#!/usr/bin/env bash
# Run the commands `talhelper gencommand <args>` generates, stopping at the
# first one that fails. Piping them into plain `bash` reports only the last
# command's status, so an earlier node's failure passed silently.

set -euo pipefail

commands="$(talhelper gencommand "$@")"
if [[ -z "${commands//[[:space:]]/}" ]]; then
    echo "talhelper gencommand $* generated no commands" >&2
    exit 1
fi
bash -euo pipefail <<<"${commands}"
