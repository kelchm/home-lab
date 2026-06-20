# MetaMCP bootstrap & server-onboarding runbook

Operational steps for the parts of MetaMCP that are **not** GitOps-managed. The
backend MCP pods are GitOps (HelmRelease + NetworkPolicy per app); the server/
namespace/endpoint **registry lives only in `metamcp-db` Postgres** and is configured
through the web UI at `https://metamcp.home.kelch.io`. See
`docs/plans/20260620-metamcp-mcp-rollout.md` for the full rollout design.

## DR / backup

`metamcp-db` DR is the Longhorn snapshot policy on the CNPG data PVC (see
`kubernetes/apps/ai/metamcp/app/cluster.yaml`), shipped to the NFS BackupTarget. The
registry (servers, namespaces, per-tool toggles, endpoints, API keys) is recoverable
only from that PVC — JSON export covers servers alone.

- **Before bumping the MetaMCP image tag**, take an on-demand Longhorn snapshot of the
  `metamcp-db` data PVC. Schema migrations on upgrade mutate Postgres irreversibly.
- `tools/metamcp-config/mcpServers.json` is the reviewable, re-importable definition of
  the in-cluster server backends. It is a partial seed (servers only); reconcile it
  against the UI's **Export JSON** after registry changes.

## Access hardening (verify once)

MetaMCP does not map kanidm groups/roles to authorization — any account that can OIDC-
login can edit the whole registry. Controls:

1. kanidm gates who can log in (`oauth2-client.yaml`, group `ops-admins`).
2. SSO self-registration must stay disabled so a new kanidm user can't auto-provision a
   MetaMCP account. Confirm in the UI (Settings → registration) that SSO sign-up is off;
   upstream defaults it off, so this is a verification, not a change.

## Add an in-cluster HTTP MCP backend (general procedure)

1. Land the backend's GitOps app (HelmRelease + `CiliumNetworkPolicy` + ks, per the
   `digikey-mcp` pattern) and confirm the pod is healthy and the Service answers on
   `http://<name>.ai.svc.cluster.local:<port>/mcp`.
2. UI → **MCP Servers** → **Import JSON**, paste the server's `streamable_http` entry
   (matching `tools/metamcp-config/mcpServers.json`). Import is upsert-by-name.
3. UI → **Namespaces** → open the target namespace → add the server.
4. Use **Filter Inactive Tools** to deactivate tools you won't call, keeping the
   namespace under ~50 active tools.
5. Update `tools/metamcp-config/mcpServers.json` and commit.

## Namespace / endpoint layout

- **`default`** (endpoint `default`, PRIVATE, OAuth) — read-only research/observability
  set; Claude Code's primary URL `.../metamcp/default/mcp`.
- **`browser`** (endpoint `browser`, PRIVATE, OAuth) — `playwright-stealth` alone
  (prompt-injection isolation); add a second Claude Code entry for
  `.../metamcp/browser/mcp`, used only when driving a browser.
- **`write`** — create only when the first write-capable server lands.

### Browser-namespace split (one-time)

1. UI → **Namespaces** → **New** → name `browser`.
2. Add `playwright-stealth`; remove it from `default`.
3. UI → **Endpoints** → **New** → name `browser`, namespace `browser`, transport
   Streamable HTTP, scope PRIVATE, auth OAuth.
4. Add an MCP server to Claude Code/Desktop pointing at
   `https://metamcp.home.kelch.io/metamcp/browser/mcp` (native OAuth, no static token).

## Phase 1 — Grafana (read-only)

The `grafana-mcp` pod authenticates to Grafana with a **Viewer** service-account token,
and runs `--disable-write`. Mint the token and seed the secret before the pod can serve
tools:

1. Grafana UI → **Administration → Users and access → Service accounts** → **Add** →
   role **Viewer** → **Add service account token** → copy the token.
2. Write it into the SOPS secret (the committed value is a placeholder):
   ```sh
   sops set kubernetes/apps/ai/grafana-mcp/app/secret.sops.yaml \
     '["stringData"]["GRAFANA_SERVICE_ACCOUNT_TOKEN"]' '"<paste-token>"'
   ```
3. Commit and let Flux reconcile; confirm the pod is healthy.
4. Register in MetaMCP (Import JSON → add `grafana` to the `default` namespace), then
   verify from Claude Code with a real query (e.g. a PromQL/LogQL instant query).

Grafana fronts every datasource (Prometheus, VictoriaMetrics, Loki, VictoriaLogs), so
this one server is the read path into the whole telemetry stack during the VM migration.
