# MetaMCP server rollout plan

## Context

MetaMCP (`ghcr.io/metatool-ai/metamcp`, ns `ai`) is the cluster's MCP gateway, fronting
three backends today — `time` (stdio), `playwright-stealth` and `digikey` (both
streamable-http) — exposed as a single flat `default` namespace/endpoint at
`https://metamcp.home.kelch.io/metamcp/default/mcp`. Claude Code/Desktop connect via
native MCP OAuth (DCR + PKCE S256, token in the OS keychain); no static token lives in
`~/.claude.json`. Web-UI login is OIDC federated to kanidm. The server/namespace/endpoint
registry lives **only** in the `metamcp-db` CNPG Postgres — it is not GitOps-managed.

This plan adds a curated set of legitimately-useful MCP servers and, just as importantly,
establishes the organization, access, least-privilege, and reproducibility model needed
to run ~15 servers / 100+ tools through one gateway without (a) degrading the model's
tool-selection, (b) widening blast radius, or (c) losing the registry to a DB wipe.

The per-MCP deployment pattern is already proven by `digikey-mcp` and `playwright-mcp`:
a bjw-s `app-template` HelmRelease via OCIRepository, a `CiliumNetworkPolicy` that
ingress-locks the backend to the metamcp pod, a hardened `securityContext`
(`runAsNonRoot`, UID 1000, drop `ALL`, seccomp `RuntimeDefault`), ClusterIP-only Service,
reached over Service DNS at `http://<name>.ai.svc.cluster.local:<port>/mcp`. This plan
reuses that pattern verbatim and only documents the per-server deltas.

All facts below were checked against upstream source/registries; the load-bearing
corrections (image tags, transport flags, auth quirks) are called out inline.

## Cross-cutting architecture decisions

### Transport: HTTP-native first, stdio only by exception

Order of preference for getting a server behind MetaMCP:

1. **HTTP-native** (streamable-http or SSE) → own hardened Deployment, registered as
   `streamable_http`. This is the default and the only pattern for anything with secrets,
   meaningful egress, or a large tool surface.
2. **stdio, tiny + trusted + no secrets** → register as a `stdio` server entry directly in
   MetaMCP. MetaMCP runs it **inside the gateway pod** using the bundled `uvx`/`npx`
   toolchain (this is how `time` already runs). Acceptable only for read-only utilities
   whose egress is benign, because such a server shares the gateway's pod identity and
   egress — it gets no per-server NetworkPolicy.
3. **stdio that needs isolation** (secrets, egress control, SSRF surface) → own Deployment
   with a `supergateway` wrapper (`--stdio "<cmd>" --outputTransport streamableHttp
   --port <p> --streamableHttpPath /mcp`) converting stdio → streamable-http, then
   registered as `streamable_http`. This buys a per-server pod, NetworkPolicy and egress
   allowlist at the cost of a small custom image.

Never put a heavy/third-party/secret-bearing stdio server into the gateway pod — it would
force that server's toolchain and credentials into the gateway's blast radius.

### MetaMCP organization: namespaces, endpoints, middleware

MetaMCP's hierarchy is **MCP Servers → Namespaces → Endpoints**:

- A **Namespace** groups servers and is where curation lives: enable/disable whole servers,
  enable/disable individual tools, and apply per-namespace **Tool Overrides**
  (rename/retitle/redescribe + a JSON annotation blob like `readOnlyHint`). Tool-name
  collisions are auto-resolved by prefixing every tool `{ServerName}__{tool}` (e.g.
  `digikey__keyword_search`), so unrelated servers coexist safely. A server can live in
  multiple namespaces with a *different active-tool subset* in each.
- An **Endpoint** exposes exactly one namespace over a URL
  (`/metamcp/<endpoint>/mcp` | `/sse` | `/api`), keyed by **endpoint name** (today's
  `default` is the endpoint name, not the namespace). One namespace can have many
  endpoints (e.g. one OAuth, one API-key). Each endpoint sets `scope` (PUBLIC|PRIVATE) and
  `auth_type` (OAUTH|API_KEY) independently.
- **Middleware** runs per-namespace. The two supported built-ins:
  - **Filter Inactive Tools** — drops disabled tools from `tools/list` so they never enter
    the model's context. This is the *only* lever for tool-count control; there is **no**
    tool-search/RAG middleware.
  - **Tool Overrides & Annotations** — rename/redescribe/annotate per namespace.

Implication: the only two knobs protecting model tool-selection are **disable individual
tools** and **split into more namespaces+endpoints**. Each new namespace = one more URL the
client must add, so keep the namespace count small (2–4), not one-per-server.

#### Namespace design for this rollout

The memory's prior guidance ("one flat `default` namespace until a concrete trigger")
is now triggered on two of its three named conditions: tool-count will cross ~100, and the
browser tool's prompt-injection surface needs isolating. Therefore:

- **`default` namespace** (endpoint `default`, PRIVATE, OAuth) — the everyday read-only
  research/observability/utility set. Claude Code's primary URL stays
  `.../metamcp/default/mcp`. Hold each big server's active tools down with Filter Inactive
  Tools; target < ~50 active tools in this namespace.
- **`browser` namespace** (endpoint `browser`, PRIVATE, OAuth) — `playwright-stealth`
  **alone**. The browser ingests untrusted web content that can carry tool-call injection;
  isolating it means a poisoned page can reach only browser tools, never the Grafana/
  Kubernetes/GitHub tools. Claude Code gets a second MCP entry for `.../metamcp/browser/mcp`,
  used only when driving a browser.
- **`write` namespace** (endpoint `write`, PRIVATE, OAuth) — created **only when the first
  write-capable server actually lands** (e.g. a GitHub write instance, or a Kubernetes
  apply instance). Do not pre-create it empty. Read-scoped instances of Kubernetes/Flux/
  GitHub/UniFi live in `default`; if/when a write instance is wanted, the *same backend*
  is added to `write` with its mutating tools active. This is exactly what namespace-scoped
  tool toggles + the per-server prefix are for.

Enforce read-only **structurally** (backend flags + RBAC/token scope), not by trusting the
namespace boundary — the namespace split is defense-in-depth on top of a backend that is
already incapable of writing.

### Access, auth & permissions

- **Client → endpoint:** keep every endpoint `scope=PRIVATE`, `auth_type=OAUTH`. Native MCP
  OAuth (token in keychain) means no static token in client config. Reserve `sk_mt_` API
  keys for headless/cron callers only; if ever used, prefer a PRIVATE key and never enable
  query-param auth (tokens leak into logs).
- **Who can administer the registry:** MetaMCP does **not** map kanidm groups/roles to
  authorization — any account that can OIDC-login can edit the whole registry and see all
  PUBLIC objects. The controls are (1) kanidm gating *who* can log in (already scoped to
  `ops-admins` via `oauth2-client.yaml`), and (2) **disabling SSO self-registration** so a
  new kanidm user can't auto-provision a MetaMCP account. Set
  `BOOTSTRAP_DISABLE_REGISTRATION_SSO=true` (keep UI registration off too). The LAN-only
  HTTPRoute (`gateway-admin`, no Cloudflare/public DNS) is the outer perimeter.
- **Endpoint → backend (per-server auth):** the `mcpServers` JSON supports `bearerToken`
  and a `headers` map on `streamable_http` entries. This is how the gateway injects a
  backend credential — used below to solve GitHub's header-only auth. Backend-injected
  secrets live in the metamcp DB (LAN-only, backed up via `pg_dump`); a committed
  `mcpServers.json` must placeholder or SOPS-encrypt them.
- **Backend → upstream (least privilege):** every write-capable backend is scoped to
  read-only at the credential layer (Grafana Viewer token, fine-grained read-only GitHub
  PAT, Kubernetes read RBAC, UniFi policy-gate denies) AND, where available, a backend
  read-only flag. Belt-and-suspenders: the credential is the durable boundary, the flag
  keeps mutating tools out of the model's sight.

### Reproducibility & DR (the not-in-git problem)

The registry is Postgres-only; Flux reconciles the empty app + DB, not its contents. JSON
export/import covers **server definitions only** — not namespaces, per-tool enable/disable,
tool overrides, endpoints, API keys, or users. Close the gap three ways:

1. **DR is the existing Longhorn snapshot policy** on the `metamcp-db` data PVC, shipped to
   the NFS BackupTarget (documented in `cluster.yaml`). The registry is recoverable only
   from that PVC, so no separate `pg_dump` CronJob is added — it would duplicate a DR
   strategy the repo already chose. **Take an on-demand Longhorn snapshot before every
   MetaMCP image bump** (schema migrations mutate Postgres irreversibly). Add a logical
   `pg_dump` CronJob only if a portable, engine-version-independent dump is later wanted.
2. **Commit the canonical `mcpServers.json`** to the repo
   (`tools/metamcp-config/mcpServers.json`, SOPS-encrypted if any entry carries a token).
   This replaces the deleted `tools/mcpjungle-sync/` role: a reviewable, diffable,
   re-importable server list.
3. **A bootstrap runbook** (`docs/runbooks/metamcp-bootstrap.md`, replacing the deleted
   `mcpjungle-bootstrap.md`) documenting the irreproducible layer as ordered manual steps:
   namespace → servers → disabled tools → endpoint scope/auth.

SSO self-registration is already disabled by the upstream default, so this is a
**verification** in the UI, not a HelmRelease change — the working gateway isn't edited
speculatively.

### Baseline security posture (every backend)

- `securityContext`: `runAsNonRoot: true`, UID/GID 1000, `allowPrivilegeEscalation: false`,
  drop `ALL` caps, seccomp `RuntimeDefault`. Add `readOnlyRootFilesystem: true` for the
  stateless servers (all of these except where a cache/PVC is noted).
- Several upstream images run as **root** (no `USER` directive): GitHub (distroless base),
  Fetch (`mcp/fetch`), SearXNG, UniFi, Context7 self-build. The pod-level `runAsNonRoot`/
  UID-1000 override is mandatory for these, not inherited — verify the binary tolerates
  UID 1000 at first deploy.
- `CiliumNetworkPolicy`: copy the `digikey-mcp` shape (ingress only from the metamcp pod in
  ns `ai`). Egress stays default-allow except where SSRF/least-egress matters (Fetch), where
  an egress allowlist is added. Mirror the namespace split at the network layer for
  write-capable backends.
- Pin image tags by semver+digest; never `:latest`.

## Phased rollout

Sequence one server at a time; verify each end-to-end (pod healthy → MetaMCP discovers
tools → a real query from Claude Code) before starting the next. This mirrors the
one-node-at-a-time discipline used elsewhere in this cluster.

**Phase 0 — Groundwork (no new servers).**
Create the `browser` namespace + endpoint and move `playwright-stealth` into it (UI step,
see runbook). Add reproducibility scaffolding: `tools/metamcp-config/mcpServers.json` in git
and the `metamcp-bootstrap` runbook. Verify SSO self-registration is off. DR stays the
existing Longhorn snapshot of `metamcp-db`.

**Phase 1 — Grafana (read-only).** Highest payoff, HTTP-native, zero wrapping, and it fronts
*every* datasource (Prometheus, VictoriaMetrics, Loki, VictoriaLogs) through one server —
the right observability entry point given the in-flight VM migration. Template for all
HTTP-native backends.

**Phase 2 — Cluster introspection pair (read-only).** `flux-operator-mcp` (traces Flux
GitOps state → workloads → pod logs; speaks the daily stack) + `containers/kubernetes-mcp-server`
(arbitrary CRDs Flux doesn't model: Cilium, Longhorn, Kanidm, Traefik). Both
`--read-only`, both bound to scoped read ServiceAccounts.

**Phase 3 — GitHub (read-only).** Triage Renovate PRs, read Actions logs, manage issues
(e.g. the Jellyfin VAAPI issue #63). Native `http` mode, mounted at `/readonly`, PAT
injected by MetaMCP via `bearerToken`.

**Phase 4 — No-auth utilities.** Hacker News (stdio-in-gateway), Open-Meteo weather
(HTTP-native), `markitdown` (file→Markdown, HTTP-native), `mcp-netutils` (DNS/TLS/ping
diagnostics, HTTP-native). All read-only, no secrets.

**Phase 5 — Electronics.** `pcbparts-mcp` — JLCPCB/Mouser/DigiKey component search; adds
JLCPCB stock+pricing the existing DigiKey MCP can't. Read-only; reuse the 1Password DigiKey
client for the DigiKey cross-ref.

**Phase 6 — UniFi (read-only).** 178-tool server → trim hard with `eager` registration +
`UNIFI_ENABLED_CATEGORIES` and deny all policy gates. Read network/port/LAG/VLAN/client
state during network debugging.

**Phase 7 — Search/docs (decision-gated).** SearXNG MCP (needs a SearXNG instance deployed
first) and a docs server (self-hosted `docs-mcp-server` preferred over hosted Context7 for
the GitOps ethos). Both optional — Claude Code already has WebSearch/WebFetch.

**Later / conditional** (own sub-decisions, see Discovery): DBHub (multi-DB SQL),
first-party VictoriaMetrics/VictoriaLogs MCPs, the security trio (Trivy/Semgrep/CVE),
HolmesGPT (agentic RCA), and the backend-gated apps (Home Assistant, MQTT, Obsidian,
Outline, Karakeep, Synology).

## Per-MCP deployment specs

Each entry lists only the deltas from the `digikey-mcp` template. Verified corrections are
**bold**.

### Grafana — `grafana-mcp` (Phase 1)

- **Image: `docker.io/grafana/mcp-grafana:0.16.0`** (no `v` prefix; `v0.16.0` 404s) — or
  `:0.16.0-alpine` for a smaller CVE surface. No ghcr mirror exists.
- Transport: streamable-http native, **no wrapper**. The published image's ENTRYPOINT
  defaults to SSE, so args must be overridden:
  `["-t","streamable-http","--address","0.0.0.0:8000","--endpoint-path","/mcp","--disable-write"]`.
  **`--endpoint-path /mcp` is mandatory** — the streamable-http default is `/`, which would
  404 against the Service-DNS convention.
- Scoping (both layers): a Grafana **Viewer** service-account token (caps blast radius at
  the Grafana API) **plus** `--disable-write` (keeps mutating tools out of the tool list).
  Optionally trim with `--enabled-tools dashboard,datasource,prometheus,loki,search,navigation,alerting,annotations`.
- Env: `GRAFANA_URL=http://grafana.observability.svc.cluster.local` (Grafana Service is
  port 80), `GRAFANA_SERVICE_ACCOUNT_TOKEN` via `envFrom` SOPS secret.
- Egress: cross-namespace to `grafana.observability` + DNS (default-allow egress covers it;
  ingress-only NetworkPolicy like digikey).
- securityContext: image already runs UID 1000; add `readOnlyRootFilesystem: true`.
- Health: tcpSocket on 8000 (no documented HTTP health path).

### Kubernetes — `kubernetes-mcp` (Phase 2)

- **Image: `quay.io/containers/kubernetes_mcp_server:v0.0.62`** (underscores in repo; tags
  **are** `v`-prefixed — opposite of Grafana). Official Helm chart also exists
  (`oci://ghcr.io/containers/charts/kubernetes-mcp-server`) if preferred over app-template.
- Transport: native streamable-http (`/mcp`) **and** SSE (`/sse`) on one port via `--port 8080`.
- Scoping: `--read-only` (exposes only `readOnlyHint=true` tools) + `--disable-destructive`;
  optionally `denied_resources` to block Secrets at the tool layer. The **durable** boundary
  is RBAC: a dedicated ServiceAccount bound to the built-in `view` ClusterRole (excludes
  Secrets, includes pods/log) plus a custom read rule for the CRDs you care about.
  Per the app-template v5 note, set `automountServiceAccountToken: true` + the RBAC objects.
- Image runs non-root per chart defaults; `/healthz` probe available.
- Keep this for arbitrary CRDs; keep Flux MCP for GitOps-state tracing (they overlap on core
  objects).

### Flux — `flux-operator-mcp` (Phase 2)

- Official ControlPlane chart `oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator-mcp`;
  HTTP transport (`serve --transport http`).
- Run `--read-only=true --mask-secrets`; bind a scoped read ServiceAccount (do **not** mount
  a kubeconfig). Traces ResourceSets/HelmReleases/Kustomizations/Git+OCIRepositories →
  Deployments → pod logs, and diffs Git vs cluster.
- Open a separate write instance later only if agent-driven reconcile is wanted.

### GitHub — `github-mcp` (Phase 3)

- Image: `ghcr.io/github/github-mcp-server:v1.4.0` (confirmed, multi-arch).
- Transport: native `http` subcommand → args `["http"]`. **The MCP endpoint is mounted at
  ROOT `/`, not `/mcp`** (`--base-path` is OAuth-metadata only, not a route prefix). Use the
  **`/readonly` route to force read-only**, so the MetaMCP backend URL is
  `http://github-mcp.ai.svc.cluster.local:8082/readonly`.
- **Auth quirk (load-bearing): in `http` mode the PAT is NOT read from env** — the server
  requires a per-request `Authorization: Bearer <PAT>` or returns 401. Inject it from
  MetaMCP via the server entry's `bearerToken` (or `headers`) field. The env-PAT model does
  not work for `http`.
- Credential: a **fine-grained PAT** (skips classic-scope filtering; the GitHub API is the
  boundary) with read-only perms: Metadata:Read (mandatory), Contents:Read, Issues:Read,
  Pull requests:Read, Actions:Read. Optionally `GITHUB_TOOLSETS` to add `actions,code_security`
  (defaults are `context,repos,issues,pull_requests,users`). Note: `--dynamic-toolsets` was
  **removed upstream** — do not use it.
- Distroless base runs as **root** → force `runAsNonRoot`/UID 1000 (static Go binary,
  tolerates it). TCP-socket probe on 8082; stateless.

### Hacker News — `hackernews-mcp` (Phase 4)

- Upstream `erithwik/mcp-hn` (Python, `uvx mcp-hn`), **stdio-only**, 4 read-only tools, no
  auth. No published streamable-http image.
- **Recommended pattern (a):** register as a `stdio` server in MetaMCP
  (`command: uvx`, `args: ["mcp-hn"]`) — runs in the gateway pod via the bundled toolchain,
  exactly like `time`. It's a tiny trusted read-only utility; egress to `hn.algolia.com`
  (80+443) is benign.
- Alternative pattern (b) if pod-level isolation/GitOps-visibility is wanted: own Deployment
  with a `supergateway --stdio "uvx mcp-hn" --outputTransport streamableHttp --port 8000
  --streamableHttpPath /mcp` wrapper image, registered as `streamable_http`.

### Open-Meteo — `open-meteo-mcp` (Phase 4)

- `cmer81/open-meteo-mcp`, HTTP-native (`TRANSPORT=http`, `/mcp`), prebuilt GHCR image,
  **no API key**. Forecast/historical/air-quality/marine/elevation/geocoding. Read-only.
- Egress allowlist: `api.open-meteo.com` only.

### markitdown — `markitdown-mcp` (Phase 4)

- `microsoft/markitdown` (markitdown-mcp), HTTP-native (streamable-http + SSE), one tool
  `convert_to_markdown(uri)`. First-party Microsoft.
- Set bind host `0.0.0.0` inside the pod (defaults to localhost); rely on the Cilium
  ingress-lock as the access control its docs call for. Stateless.

### netutils — `netutils-mcp` (Phase 4)

- `patrickdappollonio/mcp-netutils`, HTTP mode, static Go binary, official ghcr image.
  Read-only DNS (A/AAAA/CNAME/MX/NS/PTR/SOA/SRV/TXT, resolver or DoH), WHOIS, TLS cert
  inspection, HTTP/ICMP ping. Resolves Service DNS and inspects Traefik/Cloudflare TLS from
  inside the cluster.
- ICMP wants `NET_RAW`; keep drop-`ALL` and use the HTTP-ping/TLS/DNS tools instead of adding
  the cap.

### pcbparts — `pcbparts-mcp` (Phase 5)

- `Averyy/pcbparts-mcp`, streamable-http native (stateless), GHCR image + compose. 14
  read-only tools: JLCPCB (575k+ in-stock, no key), Mouser/DigiKey (optional keys), sensor
  recs, ~285 OSHW reference schematics, design-rule docs.
- Reuse the existing DigiKey client_id/secret (1Password/SOPS) to light up the DigiKey
  cross-ref; JLCPCB needs no secret. Egress allowlist to JLCPCB/Mouser/DigiKey.

### UniFi — `unifi-mcp` (Phase 6)

- `ghcr.io/sirkirby/unifi-network-mcp` (repo `sirkirby/unifi-mcp`, `apps/network`; pin a
  semver/sha tag, not `:latest`). Native streamable-http (`/mcp`); HTTP **auto-starts as
  PID 1** in-container, so no command override needed.
- **178 tools — trim aggressively.** Hard read-only via policy gates
  `UNIFI_POLICY_NETWORK_CREATE=false`, `..._UPDATE=false`, `..._DELETE=false` (these cover
  the entire 86-tool mutating surface; reads are never gated). To actually *remove* write
  tools from registration (not just deny them), also set
  `UNIFI_TOOL_REGISTRATION_MODE=eager` + `UNIFI_ENABLED_CATEGORIES=<read subset>` (the
  enable lists only apply in eager mode).
- Credential: a UniFi **local-admin account without MFA/SSO** (UniFi has no read-only admin
  role for the password flow; read-only is enforced by the MCP config) →
  `UNIFI_NETWORK_HOST/USERNAME/PASSWORD` in a SOPS secret. **`verify_ssl` defaults `false`**
  already (no override needed). Egress to the controller IP:443.
- Image runs as **root** → override to UID 1000. Lighter read-only alternative if the tool
  count is unwelcome: `claytono/go-unifi-mcp` (Go, read-oriented) — vet maturity.

### SearXNG — `searxng-mcp` (Phase 7, gated)

- `docker.io/isokoliuk/mcp-searxng:1.7.1` (note: GitHub org `ihor-sokoliuk`, Docker Hub
  namespace `isokoliuk` — deliberate asymmetry). Native streamable-http, **env-driven**:
  `MCP_HTTP_PORT=8000` enables HTTP, **`MCP_HTTP_HOST=0.0.0.0` is mandatory** (default
  `127.0.0.1` since v1.2.1 → otherwise unreachable). 4 read-only tools.
- **Requires a SearXNG instance** (`SEARXNG_URL`) — deploy SearXNG first or skip. Keep
  `MCP_HTTP_ALLOW_PRIVATE_URLS=false` (SSRF guard on `web_url_read`) and
  `MCP_HTTP_EXPOSE_FULL_CONFIG=false`. Image runs as root → override.

### Context7 / docs (Phase 7, decision)

- Self-hosting Context7's HTTP transport **requires Upstash Redis** (cloud) or it crashes at
  boot, and self-host is only a thin proxy to `context7.com` (docs are not local). So
  self-hosting buys little.
- **Preferred for the GitOps/self-hosted ethos:** `arabold/docs-mcp-server` — crawls and
  indexes library/framework docs **locally** (SSE transport), a true self-hosted Context7
  alternative.
- If Context7 specifically is wanted, register the **hosted remote**
  (`https://mcp.context7.com/mcp`) as a `streamable_http` server (optional API key in the
  DB), accepting the egress to a public endpoint — simpler than self-build+Redis.

### Fetch — de-prioritized

`mcp-server-fetch` overlaps Claude Code's built-in WebFetch, and its model-controlled URL +
`follow_redirects=True` is an SSRF surface that would need its own egress-deny-internal
NetworkPolicy (so it can't run in the gateway pod). Skip unless a concrete need appears; if
deployed, use pattern (b) own Deployment + supergateway + egress allowlist denying RFC1918/
link-local/cluster-CIDR.

## Discovery: other relevant MCPs

A 9-lens sweep surfaced 104 servers; curation kept 18 tier-1 / 23 tier-2. The standouts for
this profile, beyond the shortlist above:

### Highest-fit, deploy-soon (tier 1)

- **`flux-operator-mcp`** (ControlPlane) — folded into Phase 2 above; the single highest-fit
  server in the sweep.
- **DBHub (Bytebase)** — universal DB MCP gateway (Postgres/MySQL/MariaDB/SQL Server/SQLite)
  in one HTTP-native pod with read-only mode + row-limit/timeout guardrails. Introspect every
  CNPG cluster + SQLite app DB behind the *arr/Jellyfin/Kanidm stack from one server. DSNs in
  a SOPS secret; egress allowlist to the specific `-rw` Services.
- **`giantswarm/mcp-prometheus`** (18 read-only tools, own Helm chart) and **`lexfrei/mcp-loki`**
  (cosign-signed, multi-arch) — dedicated metrics/logs query servers. *But see the VM note
  below before picking these.*
- **`pcbparts-mcp`**, **`open-meteo-mcp`**, **`markitdown-mcp`**, **`mcp-netutils`** — folded
  into Phases 4–5 above.
- **Security trio:** **Trivy MCP** (official Aqua — scans the exact OCI images this cluster
  runs + manifests for misconfig/secrets, air-gapped), **Semgrep MCP** (official SAST, fully
  local), **`mcp-nvd`** (NIST NVD CVE lookup, SSE). High-value defensive set; all read-only.
  `mcp-virustotal` adds edge IP/hash/domain triage (free-tier key).
- **Backend-gated apps** (deploy only if you run the backend): **`ha-mcp`** (Home Assistant,
  the only HA server with a real read-only mode), **`mqtt-mcp`** (broker-agnostic, disable
  `publish` for monitoring-only), **`obsidian-mcp-server`** / **`mcp-outline`** /
  **Karakeep MCP** (notes/wiki/bookmarks, each with a read-only env toggle).

### Tier 2 (solid, situational)

Media: **`mcp-arr`** (widest *arr surface in one server), **`jmagar/overseerr-mcp`**
(Jellyseerr requests — the natural agent entrypoint), **OpenSubtitles** (official),
**`XDwanj/tmdb-mcp`**. Data: **Postgres MCP Pro** (index tuning/health beyond plain SQL),
**`pgmcp`** (lean NL→SQL, strict read-only). Electronics: **SPICEBridge** (ngspice, 28
tools). Observability: **Tempo embedded MCP** (if you complete LGTM with Tempo),
**`zekker6/mcp-alertmanager`** (silences/active alerts — closes the alerting loop for manual
rollouts). Utility: **`openapi-mcp-generator`** / **`mcp-openapi-proxy`** (turn any of your
self-hosted app OpenAPI specs into an MCP — high-leverage for the fleet),
**`mcp-pandoc`** (bidirectional doc conversion), **`package-registry-mcp`**. Security:
**OSV-Scanner MCP**, **`mcp-shodan`** (external attack-surface check).

### Profile-specific catches from the completeness critic

These correct gaps the lens sweep missed against your actual stack:

- **VictoriaMetrics / VictoriaLogs first-party MCPs** (`VictoriaMetrics/mcp-victoriametrics`,
  `VictoriaMetrics/mcp-victorialogs`). Verified: the observability namespace runs
  `victoria-metrics-k8s-stack` + `victoria-logs-single` + OpenObserve alongside
  kube-prometheus-stack + Loki — a live VM migration. These vendor MCPs speak MetricsQL/LogsQL
  natively (the generic Prometheus MCP only does PromQL). **If a dedicated metrics/logs MCP is
  wanted, pick these over the generic Prometheus/Loki servers** to match the migration target.
  For now, Grafana MCP already queries all of them via datasources, so a dedicated one is
  optional until the VM cutover settles.
- **CNPG MCP** (`helxplatform/cnpg-mcp`) — CloudNativePG cluster health/role/failover/backup
  status via the K8s API. Relevant because MetaMCP itself now depends on a CNPG cluster.
- **Synology MCP** (`atom2ueki/mcp-server-synology`) — inspect shares, ACLs/permissions,
  Download Station, DSM state. Directly relevant to the recurring `media`-group/775/
  protected-hardlinks import battles. Vet maturity.
- **1Password op MCP** (`goodwokdev/op-mcp`, proxies the `op` CLI) — resolve/rotate vaulted
  creds in-loop. Security-sensitive: scope tightly, prefer the proxy design (no secrets in
  model context).
- **HolmesGPT** (`robusta-dev/holmesgpt`) — agentic RCA that correlates K8s events + metrics
  + logs into a root cause; higher-leverage than raw kubectl access for the multi-system
  incidents in your memory (Talos node-NotReady, Longhorn CSI wedges). Explore as an
  "incident investigator" layer above the plain k8s server.
- **Talos MCP** (`qjoly/talos-mcp` / `5dlabs/talos-mcp`) — `talosctl` health/dmesg/service/
  member/staged-config. Talos is load-bearing and manual here (rollouts outside Flux), so
  re-tier from niche → medium — but the community servers are early; vet before in-cluster
  deploy.
- **Cilium Hubble** — no good MCP exists. When a NetworkPolicy silently drops traffic, the
  debugging tool is `hubble observe --verdict DROPPED`. A thin gRPC wrapper would be a genuine
  build target, not an off-the-shelf add.

## Decisions for the operator

1. **Browser isolation now?** Recommend yes — Phase 0 moves `playwright-stealth` to its own
   namespace/endpoint (adds a second client URL). The injection-surface trigger is real once
   Grafana/Kubernetes/GitHub tools share the gateway.
2. **Dedicated metrics/logs MCP, or Grafana-only?** Recommend Grafana-only until the VM
   migration settles; then add the first-party VictoriaMetrics/VictoriaLogs MCPs (not the
   generic ones).
3. **Docs server:** self-hosted `docs-mcp-server` vs hosted Context7 remote. Recommend the
   self-hosted one for the GitOps ethos.
4. **SearXNG:** deploy a SearXNG instance to unlock private search, or skip given built-in
   WebSearch. Recommend skip for now.
5. **Which backend-gated apps are actually in the stack** (Home Assistant? MQTT? Obsidian?
   Outline? Karakeep?) — those gate their MCPs.

## References

- Established pattern: `kubernetes/apps/ai/digikey-mcp/` (HTTP backend + PVC + secret),
  `kubernetes/apps/ai/playwright-mcp/` (HTTP backend, no secret).
- Gateway: `kubernetes/apps/ai/metamcp/app/` (helmrelease, httproute, oauth2-client).
- Memory: `project_metamcp_mcp_gateway` (gateway model), `project_app_template_v5_upgrade`
  (K8s-API-client apps need `automountServiceAccountToken: true` + RBAC).
