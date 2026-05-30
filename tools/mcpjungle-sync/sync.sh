#!/usr/bin/env bash
# Reconcile mcpjungle MCP clients (allow list + token) from declarative JSON.
#
# mcpjungle's `create mcp-client` is the only verb that can set --allow, and
# `update mcp-client` only rotates tokens. So the supported workflow for
# changing a client's allow list is delete + create with --access-token to
# preserve the token (otherwise every client config file has to be re-pasted).
#
# This script hides that dance behind one command. Per-client JSON in
# clients/<name>.json holds the desired allow list and a 1Password reference
# to the token. Tokens never sit in git.
#
# Workflow when adding a new MCP server:
#   1. mcpjungle register --name <new-server> ...
#   2. Edit each clients/*.json to add <new-server> to "allow"
#   3. ./sync.sh
#   Clients keep their existing tokens; Claude Code / Desktop don't need any
#   config change beyond /mcp reconnect.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CLIENTS_DIR=$SCRIPT_DIR/clients

usage() {
    cat <<EOF
Usage: $(basename "$0") [--dry-run] [--client NAME]

  --dry-run        Print the mcpjungle commands that would run; don't execute.
  --client NAME    Sync only clients/NAME.json. Default: all clients/*.json.
EOF
    exit "${1:-0}"
}

DRY_RUN=
ONLY_CLIENT=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)     DRY_RUN=1; shift ;;
        --client)      ONLY_CLIENT=${2:?--client needs a name}; shift 2 ;;
        -h|--help)     usage 0 ;;
        *)             echo "unknown arg: $1" >&2; usage 1 ;;
    esac
done

# --- preflight --------------------------------------------------------------

for cmd in jq op mcpjungle; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "missing required command: $cmd" >&2
        exit 1
    }
done

# `op whoami` prompts for biometric / touch ID if not signed in — surface the
# failure here rather than mid-loop, so a single Touch ID covers all clients.
op whoami >/dev/null || {
    echo "1Password CLI not signed in (run: eval \$(op signin))" >&2
    exit 1
}

[[ -d $CLIENTS_DIR ]] || {
    echo "no clients dir at $CLIENTS_DIR" >&2
    exit 1
}

# --- sync one client --------------------------------------------------------

sync_one() {
    local cfg=$1
    local name
    name=$(basename "$cfg" .json)

    local description allow_csv token_ref token
    description=$(jq -r '.description // ""' "$cfg")
    allow_csv=$(jq -r '.allow | join(",")' "$cfg")
    token_ref=$(jq -r '.tokenRef' "$cfg")

    if [[ -z $token_ref || $token_ref == null ]]; then
        echo "  $name: missing tokenRef" >&2
        return 1
    fi

    # Resolve the token BEFORE delete — if 1Password fails we don't want to
    # leave the client deleted with no way to recreate it.
    token=$(op read "$token_ref") || {
        echo "  $name: op read failed for $token_ref" >&2
        return 1
    }

    if [[ -n $DRY_RUN ]]; then
        echo "  $name"
        echo "    delete mcp-client $name"
        echo "    create mcp-client $name --allow $allow_csv --access-token <from 1pw> --description $(printf %q "$description")"
        return 0
    fi

    # Delete is idempotent on mcpjungle's side (returns success even when the
    # client doesn't exist). Ignore failure either way; the create below is
    # the load-bearing step and will surface real errors.
    mcpjungle delete mcp-client "$name" >/dev/null 2>&1 || true

    mcpjungle create mcp-client "$name" \
        --allow "$allow_csv" \
        --access-token "$token" \
        ${description:+--description "$description"} \
        >/dev/null
    echo "  $name: synced (allow=$allow_csv)"
}

# --- main loop --------------------------------------------------------------

shopt -s nullglob
configs=("$CLIENTS_DIR"/*.json)
if [[ ${#configs[@]} -eq 0 ]]; then
    echo "no client configs found in $CLIENTS_DIR" >&2
    exit 1
fi

if [[ -n $ONLY_CLIENT ]]; then
    only_path=$CLIENTS_DIR/$ONLY_CLIENT.json
    [[ -f $only_path ]] || {
        echo "no config at $only_path" >&2
        exit 1
    }
    configs=("$only_path")
fi

echo "${DRY_RUN:+[DRY RUN] }syncing ${#configs[@]} client(s):"
exit_code=0
for cfg in "${configs[@]}"; do
    sync_one "$cfg" || exit_code=1
done
exit $exit_code
