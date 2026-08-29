# MCPHub pilot

This is a parallel, file-backed MCPHub evaluation. MetaMCP remains authoritative and no client has been cut over.

## Authorization model

MCPHub groups represent reusable capability and failure boundaries. They are not named after clients:

| Group | Backends | Intended boundary |
|---|---|---|
| `homelab-read` | Grafana, Flux Operator, Kubernetes | Read-only homelab observation; the backends also enforce read-only mode and credentials/RBAC. |
| `automotive-reference` | Lemon Manuals | Automotive reference data suitable for the friend-facing Flatrate persona. |
| `electronics-reference` | DigiKey, PCBParts | Electronics and component reference data. |
| `documents` | MarkItDown | Document conversion. |
| `weather` | Open-Meteo | Weather lookup. |
| `hacker-news` | Hacker News | Read-only HN feeds, threads, users, and full-text search. |
| `browser` | Playwright Stealth | High prompt-injection surface; isolated from every other capability and given a per-session upstream client. |

Static system bearer keys represent workload principals. The initial pilot matrix is:

| Principal | Allowed groups |
|---|---|
| `operator-interactive` | All seven groups. |
| `hermes-personal` | Every non-browser group. |
| `hermes-ops-cron` | `homelab-read` only. |
| `flatrate-discord` | `automotive-reference` only. |

These are service identities. Individual Discord members are authorized and audited by the Flatrate Hermes profile, not by MCPHub. Likewise, MCPHub does not turn a shared upstream identity into per-user authorization: a future client that needs different Kubernetes access must use a separately deployed backend with its own ServiceAccount and group.

Clients connect once per capability, for example `https://mcphub.home.kelch.io/mcp/homelab-read` and `https://mcphub.home.kelch.io/mcp/automotive-reference`, and may reuse their principal key across every allowed group. MCPHub has no separate endpoint-composition object. Keeping capabilities as separate client connections is intentional: it avoids duplicating group membership into client-specific bundles and prevents one Hermes MCP circuit breaker from disabling unrelated capabilities.

The global `/mcp` and `/sse` routes are disabled in MCPHub even though their prefixes reach the Gateway. A restricted key cannot call a direct server route; it must use an allowed group name.

## Management

The SOPS-encrypted settings Secret is the only source of truth. It includes an admin record with an unshared random password hash solely to prevent MCPHub's file-mode bootstrap from trying to write a default user into the read-only mount. The dashboard, management API, OAuth server, Better Auth, and discovery are unavailable externally, and read-only mode rejects accidental mutations from inside the cluster.

- Onboard a workload by adding a uniquely named system key with `accessType: groups` and the minimum `allowedGroups` set.
- Rotate without a flag day by adding a replacement key, updating the client, and removing the old key in a later change.
- Revoke a client by disabling or removing only its key.
- Do not use `accessType: all` for a deployed workload; it is reserved from this configuration.
- Do not configure upstream headers, OAuth, or `passthroughHeaders`. Client credentials terminate at MCPHub.

Authentication events identify the bearer-key principal in container logs, and MCP transport logs identify the selected group. MCPHub has no persistent activity store in file mode. `activityLog.storeToolPayload: false` remains set as defense in depth if database mode is evaluated later.
