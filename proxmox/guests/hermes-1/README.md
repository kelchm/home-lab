# Hermes Agent

`hermes-1` is the first persistent application guest on `pve-sbx`. It runs the official Hermes Agent container with its web dashboard available to LAN clients over HTTPS. Caddy terminates TLS on the VM; neither the dashboard nor the OpenAI-compatible API depends on Kubernetes ingress.

This directory is the source for the application deployment and its operating record. The PVE VM was provisioned manually for this first workload; it is not yet represented by an OpenTofu resource.

| Setting | Value |
|---|---|
| VMID | `200` |
| Node | `pve-sbx-1` initially; migratable |
| Address | `10.32.21.100/24` on VLAN 21 |
| Guest | Debian 13 genericcloud, 2 vCPU, 4 GiB RAM, 32 GiB `local-lvm` |
| Dashboard | `https://hermes.home.kelch.io` |
| Dashboard backend | `http://127.0.0.1:9119` in the guest |
| API | `http://127.0.0.1:8642` in the guest; not exposed to the LAN |
| MetaMCP | `https://metamcp.home.kelch.io/metamcp/hermes/mcp` through a host-specific TCP 443 allow |
| MCPHub Hacker News | `https://mcphub.home.kelch.io/mcp/hacker-news` through the existing TCP 443 allow |
| State | `/srv/hermes` in the guest, mounted at `/opt/data` in the container |
| TLS state | `/srv/caddy`, with the DNS credential in root-only `/srv/caddy/.env` |
| Backup | Included in cluster job `daily-backups` |

The VM uses the official Debian 13 genericcloud image with SHA-512 `184761b0dad0f9ace02f9298050ca96ce3caa39a461a47706d47ff9698b59933918b91b40177fbd4d392f6446af8b4d18ecb94caca988169b19641606bf34003`. The container image is pinned to the multi-architecture digest published for Hermes `v2026.8.27`.

The default model is the active OpenAI-compatible `deepseek-v4-flash-dspark` endpoint on `spark-1:8888`. The Qwen endpoint on `spark-1:8000` is mutually exclusive with that two-node deployment; change the Hermes model configuration when the operator changes Spark workloads.

MetaMCP is enabled through the dedicated `hermes` endpoint. Its API key is supplied at runtime as `METAMCP_HERMES_API_KEY`; the checked-in configuration contains only the environment reference. UniFi permits this guest alone to reach the MetaMCP listener at `10.32.130.1` on TCP 443, ordered immediately above the broader Workloads-to-`admin-prod` deny.

The read-only Hacker News pilot is a separate untrusted MCP connection through MCPHub's `hacker-news` capability group. Its `hermes-personal` bearer token comes from the SOPS-encrypted `kubernetes/apps/ai/mcphub/app/secret.sops.yaml` source of truth and is copied only into `/srv/hermes/.env` as `MCPHUB_HERMES_PERSONAL_TOKEN`; the checked-in Hermes configuration contains only the environment reference. Hacker News content is hostile user-generated text, so this connection must remain isolated from the `browser` group and every write-capable MCP connection. The `hermes-personal` principal is not authorized for `browser`, and no additional firewall rule is needed for this endpoint.

The Hermes runtime `.env` is intentionally not in Git. It contains the dashboard break-glass authentication values, API-server token, MetaMCP API key, MCPHub workload token, and messaging-adapter credentials and allowlists. A root-readable recovery copy of the dashboard and API client credentials is stored at `/root/hermes-access.txt` inside the backed-up VM; the authoritative MetaMCP key remains in the `Metamcp - hermes-1` 1Password item and the authoritative MCPHub key remains in the SOPS source above. The separate Cloudflare token used by Caddy is stored in `/srv/caddy/.env` at runtime and is therefore also captured by VM backups; do not add this infrastructure credential to the client-access file. Its authoritative copy belongs in 1Password under `hermes - kelch.io`.

Telegram is enabled for the private `@MaiaRelayBot` adapter. Its BotFather token is stored authoritatively in the `Telegram - hermes-1` 1Password item and copied at runtime to `TELEGRAM_BOT_TOKEN` in `/srv/hermes/.env`. `TELEGRAM_ALLOWED_USERS` contains the single operator's numeric Telegram user ID; neither `TELEGRAM_ALLOW_ALL_USERS` nor `GATEWAY_ALLOW_ALL_USERS` is set. Gateway streaming is enabled with the `auto` transport, which selects Telegram's native draft transport for direct messages and retains the adapter's edit-based fallback. Do not commit the token or numeric allowlist value.

Hermes Desktop for macOS and compatible native clients connect to the canonical HTTPS dashboard through Hermes's server-mediated native PKCE flow, while Safari on iOS uses the same authenticated web UI. Kanidm therefore needs the dashboard's `/auth/callback` redirect rather than a native-client redirect URI. The dashboard offers Kanidm through a public PKCE client and retains the local password provider for break-glass access. UniFi local DNS resolves `hermes.home.kelch.io` directly to the VM at `10.32.21.100`.

Kanidm and local-password sessions are distinct authentication principals: daily Kanidm sessions use the Kanidm subject UUID, while the break-glass principal is `basic:kelchm`. They are not separate Hermes tenants; conversations, projects, memory, credentials, and dashboard preferences belong to this single instance. Browser-controller authorization remains scoped to the principal that registered a controller session.

Caddy obtains and renews an exact-host Let's Encrypt certificate through Cloudflare DNS-01. The dedicated token has Zone Read and DNS Edit on `kelch.io`; it is not shared with Kubernetes cert-manager or PVE. Caddy runs as UID/GID 1000 with a read-only root filesystem and only `NET_BIND_SERVICE`; its writable `/data` and `/config` paths persist under `/srv/caddy` so container recreation does not create a new ACME account or reissue unnecessarily.

## Deployment verification

The initial deployment on 2026-08-27 passed dashboard basic-auth login, an authenticated API turn through the DeepSeek endpoint, a terminal-tool execution, and a full VM reboot. After reboot, QEMU Guest Agent and Docker were active, the Hermes container was running with zero restarts, and both the dashboard and API reported healthy.

The HTTPS and Kanidm deployment on 2026-08-27 passed both basic and Kanidm login, native-PKCE discovery, authenticated WebSocket upgrades for `/api/ws` and `/api/pty`, and a second full VM reboot. Caddy reused the persisted Let's Encrypt certificate after reboot, the dashboard backend and API returned on loopback only, and direct LAN connections to ports 9119 and 8642 were refused. The exact-host certificate issued during this deployment was valid through 2026-11-25; Caddy renews it automatically.

The MetaMCP connection test on 2026-08-27 successfully discovered the endpoint's cluster, observability, and reference tools. An unauthenticated request from the guest reached the MetaMCP listener and returned HTTP 401, proving the narrow UniFi path without exposing the API key.

The MCPHub Hacker News connection test on 2026-08-28 was held until fix PR #454 had merged and the deployed transport's readiness probe changed from HTTP 404 to 200. Authenticated initialization negotiated MCP protocol `2025-03-26`, Hermes discovered exactly `hackernews__hn_get_stories`, `hackernews__hn_get_thread`, `hackernews__hn_get_user`, and `hackernews__hn_search_content`, and a one-item `hn_get_stories` call completed without error. From the Hermes VM, initialization without a token returned HTTP 401 and the `hermes-personal` key also returned HTTP 401 for the isolated `browser` group. Test output excluded the bearer token and hostile story text.

The private Telegram adapter test on 2026-08-28 passed a normal `telegram-ok` reply, `/status`, and a harmless terminal-tool call whose persisted session record contained the `terminal` invocation, tool result, and final reply. A controlled response after enabling the `auto` streaming transport completed through the restarted gateway, which resolved Telegram streaming as enabled. The bot profile uses the cropped Maia avatar, the adapter reports `telegram` and `api_server` as its connected platforms, and the allowlist remains limited to the single operator ID.

An on-demand QGA-assisted snapshot backup completed successfully and produced `backups-pve-sbx:backup/vzdump-qemu-200-2026_08_27-19_03_54.vma.zst`. The cluster's enabled `daily-backups` job also includes VMID `200` through its `all=1` selection.

## Operations

Stage this directory at `/opt/hermes` on the guest. Hermes reads its persistent configuration from `/srv/hermes/config.yaml`, not the staged copy, so explicitly install a reviewed config change before recreating the containers. Dashboard-managed preferences such as the active theme can also mutate the persistent file; reconcile any preferences that should survive into Git before replacing it. `/srv/caddy/.env` must already contain `CLOUDFLARE_API_TOKEN` with mode `0600` and root ownership. Caddy's two state directories must remain owned by its numeric runtime identity:

```sh
ssh kelchm@hermes.home.kelch.io 'sudo install -d -o 1000 -g 1000 -m 0750 /srv/caddy/data /srv/caddy/config && sudo chown -R 1000:1000 /srv/caddy/data /srv/caddy/config && sudo install -o kelchm -g kelchm -m 0640 /opt/hermes/config.yaml /srv/hermes/config.yaml && cd /opt/hermes && sudo docker compose pull hermes && sudo docker compose build --pull caddy && sudo docker compose up -d'
```

Inspect the service without printing credentials:

```sh
ssh kelchm@hermes.home.kelch.io 'cd /opt/hermes && sudo docker compose ps && sudo docker logs --tail 100 hermes'
curl -sS https://hermes.home.kelch.io/api/status | jq '{auth_required, auth_providers, auth_flows}'
openssl s_client -connect hermes.home.kelch.io:443 -servername hermes.home.kelch.io -verify_hostname hermes.home.kelch.io </dev/null
```

After changing only Hermes runtime values in `/srv/hermes/.env`, restart only the Hermes service. This reloads the mounted persistent environment without pulling an image, rebuilding Caddy, or restarting unrelated services:

```sh
ssh kelchm@hermes.home.kelch.io 'cd /opt/hermes && sudo docker compose restart hermes'
```

Validate Telegram configuration without printing the token or allowlist value:

```sh
ssh kelchm@hermes.home.kelch.io 'set -e; test "$(grep -c "^TELEGRAM_BOT_TOKEN=" /srv/hermes/.env)" -eq 1; test "$(grep -c "^TELEGRAM_ALLOWED_USERS=" /srv/hermes/.env)" -eq 1; ! grep -Eq "^(TELEGRAM_ALLOW_ALL_USERS|GATEWAY_ALLOW_ALL_USERS)=" /srv/hermes/.env; stat -c "%a %U:%G" /srv/hermes/.env; cd /opt/hermes && sudo docker compose ps hermes'
```

Validate the MCPHub runtime reference and HN discovery without printing any bearer-key fragment:

```sh
ssh kelchm@hermes.home.kelch.io 'set -e; test "$(grep -c "^MCPHUB_HERMES_PERSONAL_TOKEN=" /srv/hermes/.env)" -eq 1; test "$(stat -c "%a" /srv/hermes/.env)" = 600; sudo docker exec --user hermes hermes /opt/hermes/.venv/bin/hermes mcp test mcphub_hacker_news 2>&1 | grep -E "Connected|Tools discovered"'
```

Retrieve the dashboard username/password or API-server token only when configuring a client:

```sh
ssh -t kelchm@hermes.home.kelch.io 'sudo less /root/hermes-access.txt'
```

That recovery file deliberately excludes the Cloudflare DNS token. Retrieve the DNS token from its `hermes - kelch.io` 1Password item only for Caddy recovery or rotation, with an explicit warning before using `op` or opening the vault through automation.
