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

The `operator-*` keys belong to agent clients on the operator workstation. `homelab-read` is the sensitive group: Kubernetes and Flux run under the `view` ClusterRole and cannot read Secrets, but Grafana's Viewer token queries every datasource, including all pod logs. `hacker-news` returns untrusted text. Claude Code, Codex, and Grok are the operator's interactive agents and receive every read-only group; `homelab-read` gives them cluster state and pod logs in every project, not only in this repository, where `.mise.toml` supplies an admin kubeconfig. OpenCode omits `homelab-read` because its `opencode-go` provider sends tool output to third-party model hosts. Claude Desktop is used for reference lookups, so it omits `homelab-read`, and it is the only client that manages DigiKey lists.

Separate keys give each client its own log attribution, revocation, and default tool set. They do not isolate agents that run as the same macOS user: any client with shell access can read another client's configuration and key.

Agent runs delegated from one client to another use the delegate's normal configuration, so they receive the same groups as direct use of that client.

These are service identities. Individual Discord members are authorized and audited by the Flatrate Hermes profile, not by MCPHub. Likewise, MCPHub does not turn a shared upstream identity into per-user authorization: a future client that needs different Kubernetes access must use a separately deployed backend with its own ServiceAccount and group.

Clients connect once per capability, for example `https://mcphub.home.kelch.io/mcp/homelab-read` and `https://mcphub.home.kelch.io/mcp/automotive-reference`, and may reuse their principal key across every allowed group. MCPHub has no separate endpoint-composition object. Keeping capabilities as separate client connections is intentional: it avoids duplicating group membership into client-specific bundles and prevents one Hermes MCP circuit breaker from disabling unrelated capabilities.

Claude and Codex reject tool names longer than 64 characters, and they expose MCPHub group tools as `mcp__<connection>__<server>__<tool>`. Operator clients therefore name the `automotive-reference` and `electronics-reference` connections `automotive` and `electronics`. ChatGPT chat connectors call from OpenAI's cloud and cannot reach this LAN-only route.

The global `/mcp` and `/sse` routes are disabled in MCPHub even though their prefixes reach the Gateway. A restricted key cannot call a direct server route; it must use an allowed group name.

## Management

The SOPS-encrypted settings Secret is the only source of truth. It includes an admin record with an unshared random password hash solely to prevent MCPHub's file-mode bootstrap from trying to write a default user into the read-only mount. Each in-cluster server is owned by that inert admin so MCPHub's SSRF guard admits the explicitly configured Kubernetes Service endpoint; do not remove the owners or disable the guard globally. The dashboard, management API, OAuth server, Better Auth, and discovery are unavailable externally, and read-only mode rejects accidental mutations from inside the cluster.

- Onboard a workload by adding a uniquely named system key with `accessType: groups` and the minimum `allowedGroups` set.
- Rotate without a flag day by adding a replacement key, updating the client, and removing the old key in a later change.
- Revoke a client by disabling or removing only its key.
- Copy each `operator-*` key into a 1Password item named `MCPHub - <key name>` so clients can be configured on devices without this repository. The Secret stays authoritative; update or delete the item in the same change that rotates or revokes the key. Give a new or portable device its own key rather than reusing another client's.
- Do not use `accessType: all` for a deployed workload; it is reserved from this configuration.
- Do not configure upstream headers, OAuth, or `passthroughHeaders`. Client credentials terminate at MCPHub.

`lemon` sets `enableKeepAlive`, so MCPHub pings it every 60 seconds and retries at that interval while it is disconnected. Other backends have not opted in: one that fails its first connect stays disconnected until the gateway restarts, because read-only mode blocks the reload API.

## PCBParts PDF links

PCBParts 0.8.x replaces public signed PDF URLs with links on the MCP request's host. The private Service hostname produces links clients cannot reach. As a temporary workaround for [#1281](https://github.com/kelchm/home-lab/issues/1281), MCPHub connects to `http://pcbparts.dev/mcp`, with a pod-only `hostAliases` entry mapping `pcbparts.dev` to the PCBParts Service's explicitly reserved `10.43.125.172`. Service port 80 targets the existing container port 8080, so Node sends `Host: pcbparts.dev` without an unwanted port. Configuring a static Host header does not work with MCPHub 1.2.0's Node fetch transport. The original Service port 8080 and internal hostname remain available.

MCP traffic stays on the internal deployment; distributor credentials continue to be used only by the local backends for their distributor API calls. No upstream headers, OAuth or credential passthrough are added; principal tokens still terminate at MCPHub. Cilium still permits only MCPHub to reach the PCBParts pods on TCP 8080 after Service translation. Only clients following returned `https://pcbparts.dev/mcp/datasheet/<lcsc>` links contact the public resolver, which redirects to JLCPCB's PDF storage without our credentials. The [upstream repository](https://github.com/Averyy/pcbparts-mcp) and [hosted site](https://pcbparts.dev/) identify each other as the official service.

PDF retrieval now depends on the hosted resolver's availability and shared signing budget: at most 20 fresh links per minute across its users, with held links reused until nearly expired. A 429 response includes `Retry-After`; a 404 means no JLCPCB PDF was found. These limits and hosted failures affect PDF retrieval even when our local tools are healthy. Four representative public PDFs were retrieved and identified during preparation; C919459 returned an explicit no-datasheet 404. This is a compatibility workaround, not an availability guarantee.

Treat the backend URL, pod alias and Service reservation as one change: without the alias, the backend URL would resolve publicly. Keep the alias and Service reservation equal. A rebuild must allocate `10.43.125.172`; Kubernetes rejects Service creation if another Service already holds it. Do not remove the reservation or change only the alias. The PCBParts Kustomization remains a dependency of MCPHub. When a reviewed upstream image supports keeping public PDF URLs directly, restore the internal backend URL and remove the alias, port-80 mapping, explicit `clusterIP` pin and `pcbparts.dev` allowlist entry together. Removing the pin during retirement preserves the existing Service allocation on update. The prepared upstream issue and patch remain unposted for later owner review.

The workaround deployed on 2026-11-06 through [#1266](https://github.com/kelchm/home-lab/pull/1266). Both Kustomizations applied the merge revision and both HelmReleases became Ready. The Service retained `10.43.125.172`, added port 80→8080, and the single Ready MCPHub pod had the matching alias. Node resolved the alias to the private IP and fetched the backend's health endpoint successfully. Both `jlc_get_part` and `jlc_find_alternatives.original` through the normal gateway returned `https://pcbparts.dev/mcp/datasheet/C15127`; the link redirected to a valid PDF identifying AO3401A. [#1281](https://github.com/kelchm/home-lab/issues/1281) records rollout acceptance. Retire the workaround when the upstream direct-link capability is available and reviewed.

Authentication events identify the bearer-key principal in container logs, and MCP transport logs identify the selected group. MCPHub has no persistent activity store in file mode. `activityLog.storeToolPayload: false` remains set as defense in depth if database mode is evaluated later.
