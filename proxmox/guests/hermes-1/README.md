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
| State | `/srv/hermes` in the guest, mounted at `/opt/data` in the container |
| TLS state | `/srv/caddy`, with the DNS credential in root-only `/srv/caddy/.env` |
| Backup | Included in cluster job `daily-backups` |

The VM uses the official Debian 13 genericcloud image with SHA-512 `184761b0dad0f9ace02f9298050ca96ce3caa39a461a47706d47ff9698b59933918b91b40177fbd4d392f6446af8b4d18ecb94caca988169b19641606bf34003`. The container image is pinned to the multi-architecture digest published for Hermes `v2026.8.27`.

The default model is the active OpenAI-compatible `deepseek-v4-flash-dspark` endpoint on `spark-1:8888`. The Qwen endpoint on `spark-1:8000` is mutually exclusive with that two-node deployment; change the Hermes model configuration when the operator changes Spark workloads.

MetaMCP is enabled through the dedicated `hermes` endpoint. Its API key is supplied at runtime as `METAMCP_HERMES_API_KEY`; the checked-in configuration contains only the environment reference. UniFi permits this guest alone to reach the MetaMCP listener at `10.32.130.1` on TCP 443, ordered immediately above the broader Workloads-to-`admin-prod` deny.

The Hermes runtime `.env` is intentionally not in Git. It contains the dashboard break-glass authentication values, API-server token, and MetaMCP API key. A root-readable recovery copy of the dashboard and API client credentials is stored at `/root/hermes-access.txt` inside the backed-up VM; the authoritative MetaMCP key remains in the `Metamcp - hermes-1` 1Password item. The separate Cloudflare token used by Caddy is stored in `/srv/caddy/.env` at runtime and is therefore also captured by VM backups; do not add this infrastructure credential to the client-access file. Its authoritative copy belongs in 1Password under `hermes - kelch.io`.

No messaging adapter is configured. Hermes Desktop for macOS and compatible native clients connect to the canonical HTTPS dashboard through Hermes's server-mediated native PKCE flow, while Safari on iOS uses the same authenticated web UI. Kanidm therefore needs the dashboard's `/auth/callback` redirect rather than a native-client redirect URI. The dashboard offers Kanidm through a public PKCE client and retains the local password provider for break-glass access. UniFi local DNS resolves `hermes.home.kelch.io` directly to the VM at `10.32.21.100`.

Kanidm and local-password sessions are distinct authentication principals: daily Kanidm sessions use the Kanidm subject UUID, while the break-glass principal is `basic:kelchm`. They are not separate Hermes tenants; conversations, projects, memory, credentials, and dashboard preferences belong to this single instance. Browser-controller authorization remains scoped to the principal that registered a controller session.

Caddy obtains and renews an exact-host Let's Encrypt certificate through Cloudflare DNS-01. The dedicated token has Zone Read and DNS Edit on `kelch.io`; it is not shared with Kubernetes cert-manager or PVE. Caddy runs as UID/GID 1000 with a read-only root filesystem and only `NET_BIND_SERVICE`; its writable `/data` and `/config` paths persist under `/srv/caddy` so container recreation does not create a new ACME account or reissue unnecessarily.

## Deployment verification

The initial deployment on 2026-08-27 passed dashboard basic-auth login, an authenticated API turn through the DeepSeek endpoint, a terminal-tool execution, and a full VM reboot. After reboot, QEMU Guest Agent and Docker were active, the Hermes container was running with zero restarts, and both the dashboard and API reported healthy.

The HTTPS and Kanidm deployment on 2026-08-27 passed both basic and Kanidm login, native-PKCE discovery, authenticated WebSocket upgrades for `/api/ws` and `/api/pty`, and a second full VM reboot. Caddy reused the persisted Let's Encrypt certificate after reboot, the dashboard backend and API returned on loopback only, and direct LAN connections to ports 9119 and 8642 were refused. The exact-host certificate issued during this deployment was valid through 2026-11-25; Caddy renews it automatically.

The MetaMCP connection test on 2026-08-27 successfully discovered the endpoint's cluster, observability, and reference tools. An unauthenticated request from the guest reached the MetaMCP listener and returned HTTP 401, proving the narrow UniFi path without exposing the API key.

An on-demand QGA-assisted snapshot backup completed successfully and produced `backups-pve-sbx:backup/vzdump-qemu-200-2026_08_27-19_03_54.vma.zst`. The cluster's enabled `daily-backups` job also includes VMID `200` through its `all=1` selection.

## Operations

Stage this directory at `/opt/hermes` on the guest. Hermes reads its persistent configuration from `/srv/hermes/config.yaml`, not the staged copy, so explicitly install a reviewed config change before recreating the containers. Dashboard-managed preferences such as the active theme can also mutate the persistent file; reconcile any preferences that should survive into Git before replacing it. `/srv/caddy/.env` must already contain `CLOUDFLARE_API_TOKEN` with mode `0600` and root ownership. Caddy's two state directories must remain owned by its numeric runtime identity:

```sh
ssh kelchm@hermes.home.kelch.io 'sudo install -d -o 1000 -g 1000 -m 0750 /srv/caddy/data /srv/caddy/config && sudo chown -R 1000:1000 /srv/caddy/data /srv/caddy/config && sudo install -o kelchm -g kelchm -m 0644 /opt/hermes/config.yaml /srv/hermes/config.yaml && cd /opt/hermes && sudo docker compose pull hermes && sudo docker compose build --pull caddy && sudo docker compose up -d'
```

Inspect the service without printing credentials:

```sh
ssh kelchm@hermes.home.kelch.io 'cd /opt/hermes && sudo docker compose ps && sudo docker logs --tail 100 hermes'
curl -sS https://hermes.home.kelch.io/api/status | jq '{auth_required, auth_providers, auth_flows}'
openssl s_client -connect hermes.home.kelch.io:443 -servername hermes.home.kelch.io -verify_hostname hermes.home.kelch.io </dev/null
```

Retrieve the dashboard username/password or API-server token only when configuring a client:

```sh
ssh -t kelchm@hermes.home.kelch.io 'sudo less /root/hermes-access.txt'
```

That recovery file deliberately excludes the Cloudflare DNS token. Retrieve the DNS token from its `hermes - kelch.io` 1Password item only for Caddy recovery or rotation, with an explicit warning before using `op` or opening the vault through automation.
