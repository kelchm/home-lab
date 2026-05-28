# mcpjungle Bootstrap

mcpjungle runs in `SERVER_MODE=enterprise`, which requires a one-time admin
init that mints credentials into the database. This step is intentionally
out-of-band — it writes state Flux can't reconcile.

## Endpoint

`https://mcpjungle.home.kelch.io` — LAN-only via `gateway-admin`. Resolves via
the cluster k8s-gateway internal DNS; not present in public DNS.

## Prerequisites

- `mcpjungle` CLI installed locally (`brew install mcpjungle/mcpjungle/mcpjungle`,
  or download from <https://github.com/mcpjungle/MCPJungle/releases>).
- LAN connectivity (or operator VPN into the home network).

## One-time: initialize the server

Run once after first deploy, or after a DR restore that lost the database:

```bash
mcpjungle init-server --registry-url https://mcpjungle.home.kelch.io
```

This creates the admin user inside the mcpjungle DB and writes the admin
access token to `~/.mcpjungle.conf` on the machine that ran it. Treat that
file as you would any other long-lived credential — it's the equivalent of
the cluster's admin kubeconfig for mcpjungle.

If the file is lost, you can't recover it from the server. Recovery path:
delete the admin row from the `mcpjungle` DB (see "DR" below) and re-run
`init-server`.

## Per-client tokens

Each MCP client (Claude Desktop, Claude Code, Cursor, etc.) needs its own
bearer token, scoped to the MCP servers it should see:

```bash
mcpjungle create mcp-client claude-code-laptop \
  --allow "filesystem, kubernetes, github"
```

Output includes the generated token. Paste it into the client's MCP server
config under `Authorization: Bearer <token>`. The token is opaque to
mcpjungle — store it in the client config file and don't commit it.

To revoke: `mcpjungle delete mcp-client claude-code-laptop`.

## Registering upstream MCP servers

HTTP-based (preferred — runs as a separate workload):

```bash
mcpjungle register --name context7 --url https://mcp.context7.com/mcp
```

stdio-based (runs inside the mcpjungle pod since we deployed the `-stdio`
image variant). Refer to upstream docs for the exact config-file schema.

## DR — Longhorn snapshot restore

mcpjungle's state lives in the CNPG-managed Postgres cluster `mcpjungle-db`.
The Cluster's data PVC is on Longhorn, covered by the cluster's recurring
snapshot policy and shipped to the NFS BackupTarget by the same machinery
documented in `longhorn-backup-restore.md`.

To restore:

1. `flux suspend ks mcpjungle -n ai` (stop reconcile)
2. `kubectl scale cluster.postgresql.cnpg.io/mcpjungle-db -n ai --replicas=0`
   — actually CNPG doesn't speak `scale`; use `cnpg.io/reconciliationLoop:
   disabled` annotation if you need it offline. For a pure PVC swap,
   delete the Cluster + restore the underlying PVC, then re-apply.
3. Restore the PVC from Longhorn snapshot per the longhorn runbook.
4. `flux resume ks mcpjungle -n ai` and let CNPG re-attach.
5. Verify connectivity: `kubectl logs -n ai deploy/mcpjungle` should show
   successful DB ping; the `/health` endpoint should return 200.

If the restore predates the most recent admin token issuance, re-run
`init-server` on a fresh machine and reissue client tokens. There's no
finer-grained PITR — that's the tradeoff for using Longhorn snapshots
instead of S3-backed Barman.
