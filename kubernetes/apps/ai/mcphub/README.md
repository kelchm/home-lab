# MCPHub pilot

MCPHub is the file-backed MCP gateway. MetaMCP and MarkItDown have been removed. Clients must use their explicitly authorized MCPHub groups; former MetaMCP endpoints are no longer available.

## Authorization model

MCPHub groups represent reusable capability and failure boundaries. They are not named after clients:

| Group | Backends | Intended boundary |
|---|---|---|
| `homelab-read` | Grafana, Flux Operator, Kubernetes | Read-only homelab observation; the backends also enforce read-only mode and credentials/RBAC. |
| `automotive-reference` | Lemon Manuals | Automotive reference data suitable for the friend-facing Flatrate persona. |
| `electronics-reference` | DigiKey catalog tools, PCBParts | Electronics and component reference data. |
| `weather` | Open-Meteo | Weather lookup. |
| `hacker-news` | Hacker News | Read-only HN feeds, threads, users, and full-text search. |
| `digikey-lists` | DigiKey MyLists tools | Reads and changes saved lists on the operator's DigiKey account through its OAuth refresh token. |

Static system bearer keys represent workload principals. The current matrix is:

| Principal | Allowed groups |
|---|---|
| `operator-claude-code` | Every group except `digikey-lists`; Grok CLI imports Claude Code's MCP servers and shares this key. |
| `operator-codex` | Every group except `digikey-lists`; covers the Codex CLI and the ChatGPT desktop app, which share `~/.codex/config.toml`. |
| `operator-opencode` | Every group except `homelab-read` and `digikey-lists`. |
| `operator-claude-desktop` | `automotive-reference`, `electronics-reference`, `weather`, `hacker-news`, and `digikey-lists`. |
| `hermes-personal` | Every group except `digikey-lists`. |
| `hermes-ops-cron` | `homelab-read` only. |
| `flatrate-discord` | `automotive-reference` only. |
| `grafana-mcp-functional-probe` | `homelab-read` only, further restricted to Grafana’s `list_datasources` by a key-specific tool policy. |

The `operator-*` keys belong to agent clients on the operator workstation. `homelab-read` is the sensitive group: Kubernetes and Flux run under the `view` ClusterRole and cannot read Secrets, but Grafana's Viewer token queries every datasource, including all pod logs. `hacker-news` returns untrusted text. Claude Code, Codex, and Grok are the operator's interactive agents and receive every read-only group; `homelab-read` gives them cluster state and pod logs in every project, not only in this repository, where `.mise.toml` supplies an admin kubeconfig. OpenCode omits `homelab-read` because its `opencode-go` provider sends tool output to third-party model hosts. Claude Desktop is used for reference lookups, so it omits `homelab-read`, and it is the only client that manages DigiKey lists.

The five-minute functional probe follows the HTTPS admin gateway through `/mcp/homelab-read` with its own bearer Secret. Its system key stores a token hash in MCPHub settings; a `systemConfig.toolPolicies` allow rule selects that key, the existing group ID, a stable Grafana server ID and the upstream tool name `list_datasources`. Other tools fail closed for this key; the rule does not apply to existing clients. Keep the server ID stable when editing its configuration. See the [reliability runbook](../../../../docs/runbooks/ai-mcp-reliability-and-containment.md) for rollout checks.

Separate keys give each client its own log attribution, revocation, and default tool set. They do not isolate agents that run as the same macOS user: any client with shell access can read another client's configuration and key.

Agent runs delegated from one client to another use the delegate's normal configuration, so they receive the same groups as direct use of that client.

These are service identities. Individual Discord members are authorized and audited by the Flatrate Hermes profile, not by MCPHub. Likewise, MCPHub does not turn a shared upstream identity into per-user authorization: a future client that needs different Kubernetes access must use a separately deployed backend with its own ServiceAccount and group.

Clients connect once per capability, for example `https://mcphub.home.kelch.io/mcp/homelab-read` and `https://mcphub.home.kelch.io/mcp/automotive-reference`, and may reuse their principal key across every allowed group. MCPHub has no separate endpoint-composition object. Keeping capabilities as separate client connections is intentional: it avoids duplicating group membership into client-specific bundles and prevents one Hermes MCP circuit breaker from disabling unrelated capabilities.

Claude and Codex reject tool names longer than 64 characters, and they expose MCPHub group tools as `mcp__<connection>__<server>-<tool>`. Operator clients therefore name the `automotive-reference` and `electronics-reference` connections `automotive` and `electronics`. ChatGPT chat connectors call from OpenAI's cloud and cannot reach this LAN-only route.

The global `/mcp` and `/sse` routes are disabled in MCPHub even though their prefixes reach the Gateway. A restricted key cannot call a direct server route; it must use an allowed group name.

## Tool names and Grok compatibility

Grok CLI 1.0.77 still rejects `grafana__query_prometheus` after adding its own connection prefix: `homelab-read__grafana__query_prometheus` has an ambiguous server/tool boundary. The number of backends is irrelevant: a credential-free fixture reproduces the failure with one tool, and MCPHub 1.2.4 qualifies tools even in single-member groups. On November 15, all five real Grok connections completed their handshakes but admitted none of their 139 tools. The earlier single-server success in [#617](https://github.com/kelchm/home-lab/issues/617) is no longer the current behavior.

The compatibility configuration sets only `systemConfig.nameSeparator` from `__` to `-`, producing gateway names such as `grafana-query_prometheus` and client names such as `mcp__homelab-read__grafana-query_prometheus`. It preserves backend prefixes; removing them could make tools from different backends collide. Changing the separator renames tools for every client, so refresh client discovery and verify existing consumers before accepting the rollout. The local qualification below does not establish deployed acceptance.

The exact six-group catalog contains 150 tools, including the 11 tools in `digikey-lists`. Hyphen projection has no within-group collisions, and the longest Claude/Codex qualified name is 60 characters. Installed Grok accepts the proposed names in local fixtures; its own qualified-name limit is 256 characters, while the shared 64-character limit still governs these clients. Native Claude Code and Codex discovery are checked without sending a model turn. Hermes v0.23.1's actual naming functions retain unique, provider-valid names, including all four tools under its managed `mcphub_hacker_news` connection; this source check does not replace running-Hermes discovery after rollout. Grok's principal remains excluded from `digikey-lists`.

Dispatch was also checked on the pinned MCPHub 1.2.4 image in a disposable local gateway. Six dummy backends included hyphenated IDs and competing shorter prefixes: all eight listed tools reached the expected backend with their original tool names and arguments, including hyphens inside the tool name. The wrapped `call_tool` path also passed. The group resolver matches the full cached tool identity and strips the exact backend prefix; it does not split a successful call at the first hyphen.

The separator change deployed on 2026-11-15 through [#1528](https://github.com/kelchm/home-lab/pull/1528). Live Grok, Claude Code and Codex discovery each admitted all 139 tools across their five configured connections; Claude Desktop admitted all 63 tools across its five configured connections, including 11 `digikey-lists` tools. The Grok principal remained excluded from `digikey-lists` (HTTP 401). After idle checks and six identifier updates in the two active private references, one supported Hermes gateway restart replaced the gateway process while preserving the container, dashboard and Caddy processes. The resident Hermes registry contained all four renamed Hacker News handlers and four utilities, with no old names; a separate native Hermes read and an actual `hermes-ops-cron` Grafana read succeeded. Discovery checks sent no model turns. The Grafana functional probe succeeded at 20:05:04 UTC using the new tool name, with its alert clear. Long-lived provider sessions can retain stale tool definitions; reconnect or start a fresh provider session to refresh discovery after a rename.

Run the credential-free [name qualification harness](../../../../scripts/monitoring/test-mcp-tool-names.py) against the installed clients:

```sh
python3 scripts/monitoring/test-mcp-tool-names.py \
  --claude "$(command -v claude)" --codex "$(command -v codex)"
```

It covers nested names, single and multiple backend prefixes, punctuation, leading underscores, Grok's 256/257-character boundary, and local client discovery. Grok's doctor does not reject duplicate advertised names, so the harness separately checks projection collisions, including distinct backend/tool pairs that would become the same hyphenated name. `--catalog /private/path/catalog.json` checks a current private catalog with `{connection, backend, tool}` records; only counts are printed. `--hermes-schema /path/to/tools/mcp_tool_schema.py` additionally checks the pinned Hermes naming functions. Catalog and fixture files stay outside the repository. For subsequent name changes, require complete authorized live catalogs and recheck Claude, Codex and running Hermes discovery after refreshing cached registrations.

## Management

The SOPS-encrypted settings Secret is the only source of truth. It includes an admin record with an unshared random password hash solely to prevent MCPHub's file-mode bootstrap from trying to write a default user into the read-only mount. Each in-cluster server is owned by that inert admin so MCPHub's SSRF guard admits the explicitly configured Kubernetes Service endpoint; do not remove the owners or disable the guard globally. The dashboard, management API, OAuth server, Better Auth, and discovery are unavailable externally, and read-only mode rejects accidental mutations from inside the cluster.

- Onboard a workload by adding a uniquely named system key with `accessType: groups` and the minimum `allowedGroups` set.
- Rotate without a flag day by adding a replacement key, updating the client, and removing the old key in a later change.
- Revoke a client by disabling or removing only its key.
- Copy each `operator-*` key into a 1Password item named `MCPHub - <key name>` so clients can be configured on devices without this repository. The Secret stays authoritative; update or delete the item in the same change that rotates or revokes the key. Give a new or portable device its own key rather than reusing another client's.
- Do not use `accessType: all` for a deployed workload; it is reserved from this configuration.
- Do not configure upstream headers, OAuth, or `passthroughHeaders`. Client credentials terminate at MCPHub.

MCPHub 1.2.2 retries any disconnected backend in the background, including one that fails its first connect, with jittered exponential backoff from 5–10 seconds up to 150–300 seconds and no retry limit; a successful connection resets it. `lemon` also sets `enableKeepAlive`, so MCPHub health-checks it every 60 seconds and notices an outage without waiting for a failed request. The other backends have no health check, so an outage is detected when a request fails or the connection closes.

## PCBParts PDF links

MCPHub connects to `http://pcbparts-mcp.ai.svc.cluster.local:8080/mcp`. Cilium allows only MCPHub to reach PCBParts on TCP 8080. Client credentials terminate at MCPHub, and the local backend uses its own credentials for distributor API calls.

PCBParts sets `DATASHEET_LINK_BASE=https://pcbparts.dev`. Clients following `https://pcbparts.dev/mcp/datasheet/<lcsc>` reach the upstream resolver, which redirects to JLCPCB's PDF storage. The resolver has a shared signing budget of 20 fresh links per minute and reuses cached links until nearly expired. A 429 includes `Retry-After`, and a 404 indicates that the resolver found no JLCPCB PDF.

The explicit base replaces the host alias, fixed Service IP, and port-80 mapping introduced in [#1266](https://github.com/kelchm/home-lab/pull/1266). PCBParts 0.10.9's `direct` mode omits PDFs from catalog lookups, so the upstream resolver supplies those links.

Authentication events identify the bearer-key principal in container logs, and MCP transport logs identify the selected group. MCPHub has no persistent activity store in file mode. `activityLog.storeToolPayload: false` remains set as defense in depth if database mode is evaluated later.
