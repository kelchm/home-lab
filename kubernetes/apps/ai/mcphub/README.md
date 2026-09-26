# MCPHub pilot

MCPHub is the file-backed MCP gateway. MetaMCP and MarkItDown have been removed. Clients must use their explicitly authorized MCPHub groups; former MetaMCP endpoints are no longer available.

## Authorization model

MCPHub groups represent reusable capability and failure boundaries. They are not named after clients:

| Group | Backends | Intended boundary |
|---|---|---|
| `homelab-read` | Grafana, Flux Operator, Kubernetes | Read-only homelab observation; the backends also enforce read-only mode and credentials/RBAC. |
| `automotive-reference` | Lemon Manuals | Automotive reference data suitable for the friend-facing Flatrate persona. |
| `electronics-reference` | DigiKey, PCBParts | Electronics and component reference data. |
| `weather` | Open-Meteo | Weather lookup. |
| `hacker-news` | Hacker News | Read-only HN feeds, threads, users, and full-text search. |
| `browser` | Playwright Stealth | High prompt-injection surface; isolated from every other capability and given a per-session upstream client. |

Static system bearer keys represent workload principals. The current matrix is:

| Principal | Allowed groups |
|---|---|
| `operator-interactive` | All six groups. |
| `operator-claude-code` | All six groups; Grok CLI imports Claude Code's MCP servers and shares this key. |
| `operator-codex` | All six groups; covers the Codex CLI and the ChatGPT desktop app, which share `~/.codex/config.toml`. |
| `operator-opencode` | Every group except `homelab-read`. |
| `operator-claude-desktop` | `automotive-reference`, `electronics-reference`, `weather`, and `hacker-news`. |
| `hermes-personal` | Every non-browser group. |
| `hermes-ops-cron` | `homelab-read` only. |
| `flatrate-discord` | `automotive-reference` only. |

The `operator-*` keys belong to agent clients on the operator workstation. `homelab-read` is the sensitive group: Kubernetes and Flux run under the `view` ClusterRole and cannot read Secrets, but Grafana's Viewer token queries every datasource, including all pod logs. `browser` and `hacker-news` return untrusted text, and `browser` can send data to any public host. Claude Code, Codex, and Grok already have shell, web, and cluster access, so MCPHub adds no new reach for them. OpenCode omits `homelab-read` because its `opencode-go` provider sends tool output to third-party model hosts. Claude Desktop is used for reference lookups and has no unrestricted outbound channel of its own, so it omits both `homelab-read` and `browser`. Delegated, unattended agent runs must not load MCPHub connections; they read untrusted input without an operator reviewing each tool call.

These are service identities. Individual Discord members are authorized and audited by the Flatrate Hermes profile, not by MCPHub. Likewise, MCPHub does not turn a shared upstream identity into per-user authorization: a future client that needs different Kubernetes access must use a separately deployed backend with its own ServiceAccount and group.

Clients connect once per capability, for example `https://mcphub.home.kelch.io/mcp/homelab-read` and `https://mcphub.home.kelch.io/mcp/automotive-reference`, and may reuse their principal key across every allowed group. MCPHub has no separate endpoint-composition object. Keeping capabilities as separate client connections is intentional: it avoids duplicating group membership into client-specific bundles and prevents one Hermes MCP circuit breaker from disabling unrelated capabilities.

Claude and Codex reject tool names longer than 64 characters, and they expose MCPHub group tools as `mcp__<connection>__<server>__<tool>`. Operator clients therefore name the `automotive-reference` and `electronics-reference` connections `automotive` and `electronics`. ChatGPT chat connectors call from OpenAI's cloud and cannot reach this LAN-only route.

The global `/mcp` and `/sse` routes are disabled in MCPHub even though their prefixes reach the Gateway. A restricted key cannot call a direct server route; it must use an allowed group name.

## Management

The SOPS-encrypted settings Secret is the only source of truth. It includes an admin record with an unshared random password hash solely to prevent MCPHub's file-mode bootstrap from trying to write a default user into the read-only mount. Each in-cluster server is owned by that inert admin so MCPHub's SSRF guard admits the explicitly configured Kubernetes Service endpoint; do not remove the owners or disable the guard globally. The dashboard, management API, OAuth server, Better Auth, and discovery are unavailable externally, and read-only mode rejects accidental mutations from inside the cluster.

- Onboard a workload by adding a uniquely named system key with `accessType: groups` and the minimum `allowedGroups` set.
- Rotate without a flag day by adding a replacement key, updating the client, and removing the old key in a later change.
- Revoke a client by disabling or removing only its key.
- Do not use `accessType: all` for a deployed workload; it is reserved from this configuration.
- Do not configure upstream headers, OAuth, or `passthroughHeaders`. Client credentials terminate at MCPHub.

Authentication events identify the bearer-key principal in container logs, and MCP transport logs identify the selected group. MCPHub has no persistent activity store in file mode. `activityLog.storeToolPayload: false` remains set as defense in depth if database mode is evaluated later.
