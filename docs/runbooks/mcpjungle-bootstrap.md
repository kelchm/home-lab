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

If the file is lost, you can't recover it from the server — `init-server`
refuses to run twice. To re-bootstrap:

1. Take an ad-hoc Longhorn snapshot of the `mcpjungle-db` PVC first
   (Longhorn UI → Volume → Take Snapshot). Cheap insurance against
   typing the wrong `DELETE`.
2. Open a psql shell as the cnpg superuser. The Cluster generates a
   `mcpjungle-db-superuser` Secret with `username` (= `postgres`) and
   `password` keys; the pod hostname follows `<cluster>-<ordinal>`:

   ```bash
   kubectl -n ai exec -it mcpjungle-db-1 -c postgres -- \
     psql -U postgres -d mcpjungle
   ```

3. Inspect the schema to identify the admin/user table — mcpjungle's
   schema is version-specific, so confirm on the live DB before deleting
   (this runbook does not pin an exact SQL statement because the table
   name will drift across mcpjungle releases):

   ```sql
   \dt
   -- inspect candidate table, e.g.:
   SELECT * FROM <admin-table>;
   ```

4. `DELETE` the single admin row, `\q`, then re-run `init-server`. All
   previously-minted per-client tokens are invalidated (different admin
   == different signing context); reissue them per the next section.

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

Two transports. Pick by what the upstream supports:

- **Streamable HTTP** — server runs as its own workload (or is an external
  SaaS), mcpjungle proxies over HTTP. Higher isolation, separate resource
  budget. The default choice for anything browser-heavy or stateful.
- **stdio** — server runs as a subprocess of the mcpjungle pod (`-stdio`
  image bundles `npx` + `uvx`). Zero extra workloads. Good for thin REST
  wrappers and the small official servers. Subprocess crashes are scoped to
  the request, but a runaway subprocess shares the mcpjungle pod's limits.

Registration is a one-shot CLI call against the live server; the result lands
in the Postgres DB. Re-runs are idempotent on the `name` field (use
`mcpjungle update mcp-server ...` to change an existing one).

### server-time (official, stdio)

Trivial — no auth, no state, returns timezone-aware current time.

```bash
cat <<'EOF' > /tmp/time.json
{
  "name": "time",
  "transport": "stdio",
  "description": "Current time and timezone conversion",
  "command": "uvx",
  "args": ["mcp-server-time"]
}
EOF
mcpjungle register -c /tmp/time.json
```

### digikey_mcp (third-party, stdio, needs Digi-Key API creds)

Wraps the Digi-Key Product Information API for part lookups, datasheet
URLs, pricing, availability.

1. Create a Digi-Key developer account at <https://developer.digikey.com>,
   create an app under Production APIs → Product Information V4, capture
   the Client ID + Client Secret.
2. Register the MCP with creds embedded in the registration config (they
   end up in the mcpjungle DB, not in a k8s Secret):

```bash
cat <<EOF > /tmp/digikey.json
{
  "name": "digikey",
  "transport": "stdio",
  "description": "Digi-Key parts catalog lookups",
  "command": "uvx",
  "args": ["--from", "git+https://github.com/bengineer19/digikey_mcp", "digikey-mcp"],
  "env": {
    "DIGIKEY_CLIENT_ID": "${DIGIKEY_CLIENT_ID}",
    "DIGIKEY_CLIENT_SECRET": "${DIGIKEY_CLIENT_SECRET}"
  }
}
EOF
DIGIKEY_CLIENT_ID=... DIGIKEY_CLIENT_SECRET=... mcpjungle register -c /tmp/digikey.json
```

Treat the registration JSON as a secret while it exists on disk — delete
after registration. To rotate creds, re-register or use
`mcpjungle update mcp-server digikey -c <new-config>`.

### playwright-mcp (deployed as its own workload, HTTP)

Already running in-cluster (`kubernetes/apps/ai/playwright-mcp/`). Just
point mcpjungle at the Service:

```bash
mcpjungle register \
  --name playwright \
  --url http://playwright-mcp.ai.svc.cluster.local:8931/mcp \
  --description "Headless Chromium browser automation"
```

No bearer token — playwright-mcp doesn't auth, and the URL is only
reachable from inside the cluster. mcpjungle's per-client tokens are the
boundary.

## DR — Longhorn snapshot restore

mcpjungle's state lives in the CNPG-managed Postgres cluster `mcpjungle-db`.
The Cluster's data PVC is on Longhorn, covered by the cluster's recurring
snapshot policy and shipped to the NFS BackupTarget by the same machinery
documented in `longhorn-backup-restore.md`.

To restore:

1. Stop Flux reconciling the app so nothing fights us:

   ```bash
   flux suspend ks mcpjungle -n ai
   ```

2. Hibernate the Cluster — CNPG scales the PG pod to 0 and detaches
   the PVC, which is what Longhorn's in-place restore requires. Stock
   kubectl path (no krew plugin needed):

   ```bash
   kubectl -n ai annotate cluster.postgresql.cnpg.io/mcpjungle-db \
     cnpg.io/hibernation=on --overwrite
   # wait for the instance pod to terminate
   kubectl -n ai wait --for=delete pod/mcpjungle-db-1 --timeout=120s
   ```

   (Equivalent with the krew plugin:
   `kubectl cnpg hibernate on mcpjungle-db -n ai`.)

3. Restore the PVC from Longhorn snapshot per
   `docs/runbooks/longhorn-backup-restore.md` → "Restore in-place over
   the existing PVC". The PVC name is `mcpjungle-db-1`.
4. Bring the Cluster back:

   ```bash
   kubectl -n ai annotate cluster.postgresql.cnpg.io/mcpjungle-db \
     cnpg.io/hibernation=off --overwrite
   flux resume ks mcpjungle -n ai
   ```

5. Verify: the PG pod re-attaches to the restored PVC, mcpjungle
   reconnects, `/health` returns 200, and the `mcpjungle list mcp-servers`
   call from the operator laptop returns the expected registrations.

If the restore predates the most recent admin token issuance, re-run
`init-server` on a fresh machine and reissue client tokens. There's no
finer-grained PITR — that's the tradeoff for using Longhorn snapshots
instead of S3-backed Barman.
