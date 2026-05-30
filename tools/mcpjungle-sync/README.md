# mcpjungle-sync

Reconcile mcpjungle MCP clients from declarative JSON. Tokens stay in
1Password; allow lists live in git.

## Why this exists

mcpjungle's CLI exposes `create mcp-client` (which sets `--allow`) and
`update mcp-client` (which only rotates the token). There is no
in-place mutation of the allow list. The supported workflow is
delete-and-recreate; `--access-token <existing>` preserves the token so
client config files (Claude Code, Desktop) don't need to be re-pasted
on every change.

This script wraps that dance.

## Layout

```
clients/<name>.json   # one file per mcp-client; filename is the name
sync.sh               # the reconciler
```

`<name>.json` shape:

```json
{
  "description": "...",
  "allow": ["time", "playwright", "playwright-stealth", "digikey"],
  "tokenRef": "op://Personal/mcpjungle-<name>/credential"
}
```

Contains no secret material — safe to commit.

## Usage

```bash
./sync.sh                    # reconcile all clients
./sync.sh --dry-run          # print what would change
./sync.sh --client claude-code
```

Requires `mcpjungle`, `op` (1Password CLI), and `jq` on PATH. `op` must
be signed in (`eval $(op signin)`).

## Adding a new MCP server

1. Register the server with mcpjungle: `mcpjungle register ...`
2. Add the server name to each `clients/*.json` that should see it
3. `./sync.sh`

Clients keep their tokens. `/mcp reconnect` in Claude Code (and the
equivalent in Desktop) picks up the new tools.

## Adding a new client

1. Create a 1Password item, e.g. `mcpjungle-cursor-mac`, with a
   `credential` field holding the bearer token.
2. Drop `clients/cursor-mac.json` referencing that 1Password item.
3. `./sync.sh --client cursor-mac`.
4. Paste the token from 1Password into the client's MCP server config.

For Claude Code specifically, prefer `headersHelper` over a literal
token in `.mcp.json` — runs `op read` at connection time, so the token
never sits in any file.

## Rotating a token

`update mcp-client <name>` rotates the token in mcpjungle's DB. Update
the 1Password item with the new value. Done — sync isn't needed because
the allow list didn't change.
