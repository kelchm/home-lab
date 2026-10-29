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
| MCPHub Hacker News | `https://mcphub.home.kelch.io/mcp/hacker-news` through the existing TCP 443 allow |
| State | `/srv/hermes` in the guest, mounted at `/opt/data` in the container |
| TLS state | `/srv/caddy`, with the DNS credential in root-only `/srv/caddy/.env` |
| Backup | Included in cluster job `daily-backups` |

The VM uses the official Debian 13 genericcloud image with SHA-512 `184761b0dad0f9ace02f9298050ca96ce3caa39a461a47706d47ff9698b59933918b91b40177fbd4d392f6446af8b4d18ecb94caca988169b19641606bf34003`. `compose.yaml` pins the Hermes image by release tag and multi-architecture digest; Renovate proposes updates.

## Configuration

Hermes configuration has two owners. [`managed/config.yaml`](managed/config.yaml) is mounted read-only at `/etc/hermes-managed` and named by `HERMES_MANAGED_DIR`, which makes it Hermes's managed layer: its keys override the user file and cannot be changed from the dashboard, Telegram, or `hermes config set`. It holds what the deployment depends on: the model endpoint and its prompt guidance, the MCPHub connection, dashboard sign-in, and gateway streaming. Everything else is Hermes's own state in `/srv/hermes/config.yaml`, covered by VM backups and not kept in Git: the theme, reasoning level, Telegram DM topics, and the schema migrations Hermes applies on upgrade. The managed layer settles ownership; it is not a security boundary. Hermes loads `/srv/hermes/.env` over the container environment, so anything that can write that file, including the agent's terminal tool, can repoint `HERMES_MANAGED_DIR` and lift the pins. `hermes doctor` reports the resolved directory. MCPHub's principal authorization, not this file, keeps the Hacker News connection away from the `browser` group.

Hermes uses the Sparks' OpenAI-compatible endpoint on `spark-1:8888` under the fixed model name `spark`. The Sparks serve one model at a time and answer any requested name with it. Hermes reads the context window from the endpoint's `/v1/models`, which lists only the served model, and sends the configured reasoning level as-is for the server to map onto the model's own levels, with no output cap. Switching Spark profiles therefore needs no Hermes change. Hermes also picks some behaviour by model family, which `spark` doesn't name. The managed file switches on the two prompt blocks that every family the Sparks have served would get: `tool_use_enforcement` and `execution_guidance`. Two family choices are still lost: the edit-format hint in coding sessions, and the advice Hermes adds when a long reasoning phase loses its connection. With `spark` and default timeout settings, local streaming uses a 900-second stale threshold and the non-stream stale detector is disabled; separate request deadlines still apply. Recognized model names can select different stale thresholds. Scheduled jobs should leave their model unpinned so they follow this default. spark-1's firewall admits this guest on port 8888; see the [SparkRun README](../../../sparks/inference/sparkrun/README.md).

MetaMCP is retired and has no client connection. The existing MCPHub Hacker News connection remains enabled; additional capability connections require explicit configuration. UniFi permits this guest alone to reach the shared admin gateway at `10.32.130.1` on TCP 443, ordered immediately above the broader Workloads-to-`admin-prod` deny.

The read-only Hacker News pilot is a separate untrusted MCP connection through MCPHub's `hacker-news` capability group. Its `hermes-personal` bearer token comes from the SOPS-encrypted `kubernetes/apps/ai/mcphub/app/secret.sops.yaml` source of truth and is copied only into `/srv/hermes/.env` as `MCPHUB_HERMES_PERSONAL_TOKEN`; the managed configuration contains only the environment reference. Hacker News content is hostile user-generated text, so this connection must remain isolated from the `browser` group and every write-capable MCP connection. The `hermes-personal` principal is not authorized for `browser`, and no additional firewall rule is needed for this endpoint.

The Hermes runtime `.env` is intentionally not in Git. It contains the dashboard break-glass authentication values, API-server token, MCPHub workload token, and messaging-adapter credentials and allowlists. A root-readable recovery copy of the dashboard and API client credentials is stored at `/root/hermes-access.txt` inside the backed-up VM; the authoritative MCPHub key remains in the SOPS source above. The separate Cloudflare token used by Caddy is stored in `/srv/caddy/.env` at runtime and is therefore also captured by VM backups; do not add this infrastructure credential to the client-access file. Its authoritative copy belongs in 1Password under `hermes - kelch.io`.

Telegram is enabled for the private `@MaiaRelayBot` adapter. Its BotFather token is stored authoritatively in the `Telegram - hermes-1` 1Password item and copied at runtime to `TELEGRAM_BOT_TOKEN` in `/srv/hermes/.env`. `TELEGRAM_ALLOWED_USERS` contains the single operator's numeric Telegram user ID; neither `TELEGRAM_ALLOW_ALL_USERS` nor `GATEWAY_ALLOW_ALL_USERS` is set. Gateway streaming is enabled with the `auto` transport, which selects Telegram's native draft transport for direct messages and retains the adapter's edit-based fallback. Do not commit the token or the numeric allowlist value.

## Retired Flatrate profile

Flatrate was retired from Hermes on 2026-09-20 because it is being rebuilt as a standalone bot. Its dedicated gateway was stopped, its `gateway-flatrate` s6 service was unregistered, and its profile and CLI wrapper were moved out of the Hermes container's mounted state. Boot reconciliation no longer discovers Flatrate. The personal Hermes gateway remains active.

The former `/srv/hermes/profiles/flatrate` state is preserved at `/srv/retired-hermes/flatrate-2026-09-20/profile` on the VM, with its wrapper alongside it as `wrapper`. The archive parent is root-owned with mode `0700`; it contains credentials and conversation history and must not be committed or copied into an active Hermes profile. It remains covered by VM backups. The checked-in `flatrate/SOUL.md` is historical reference for the rebuild, not an active deployment input.

The Discord application and the MCPHub `flatrate-discord` principal were left intact for the standalone bot migration. Do not restore or start the Hermes profile while the replacement uses the same Discord application.

## Dashboard access

Hermes Desktop for macOS and compatible native clients connect to the canonical HTTPS dashboard through Hermes's server-mediated native PKCE flow, while Safari on iOS uses the same authenticated web UI. Kanidm therefore needs the dashboard's `/auth/callback` redirect rather than a native-client redirect URI. The dashboard offers Kanidm through a public PKCE client and retains the local password provider for break-glass access. UniFi local DNS resolves `hermes.home.kelch.io` directly to the VM at `10.32.21.100`.

Kanidm and local-password sessions are distinct authentication principals: daily Kanidm sessions use the Kanidm subject UUID, while the break-glass principal is `basic:kelchm`. They are not separate Hermes tenants; conversations, projects, memory, credentials, and dashboard preferences belong to this single instance. Browser-controller authorization remains scoped to the principal that registered a controller session.

Caddy obtains and renews an exact-host Let's Encrypt certificate through Cloudflare DNS-01. The dedicated token has Zone Read and DNS Edit on `kelch.io`; it is not shared with Kubernetes cert-manager or PVE. Caddy runs as UID/GID 1000 with a read-only root filesystem and only `NET_BIND_SERVICE`; its writable `/data` and `/config` paths persist under `/srv/caddy` so container recreation does not create a new ACME account or reissue unnecessarily. Caddy runs as PID 1 with no init. With `init: true`, this non-root image loses the capability to bind port 443; that broke the 2026-10-28 rollout, which was rolled back. The healthcheck uses curl from the `caddy` Alpine base image, which does TLS in its own process. The BusyBox `wget` it replaces started a separate `ssl_client` for each HTTPS check, and Caddy as PID 1 never reaped it, so the zombies filled the container's 4539-task limit in about 38 hours ([#1079](https://github.com/kelchm/home-lab/issues/1079)). If a base image update drops curl, the healthcheck fails.

## Deployment verification

The initial deployment on 2026-08-27 passed dashboard basic-auth login, an authenticated API turn through the DeepSeek endpoint, a terminal-tool execution, and a full VM reboot. After reboot, QEMU Guest Agent and Docker were active, the Hermes container was running with zero restarts, and both the dashboard and API reported healthy.

The HTTPS and Kanidm deployment on 2026-08-27 passed both basic and Kanidm login, native-PKCE discovery, authenticated WebSocket upgrades for `/api/ws` and `/api/pty`, and a second full VM reboot. Caddy reused the persisted Let's Encrypt certificate after reboot, the dashboard backend and API returned on loopback only, and direct LAN connections to ports 9119 and 8642 were refused. The exact-host certificate issued during this deployment was valid through 2026-11-25; Caddy renews it automatically.

Before its September retirement, the MetaMCP connection test on 2026-08-27 successfully discovered the endpoint's cluster, observability, and reference tools. An unauthenticated request from the guest reached the MetaMCP listener and returned HTTP 401, proving the narrow UniFi path without exposing the API key.

The MCPHub Hacker News connection test on 2026-08-28 was held until fix PR #454 had merged and the deployed transport's readiness probe changed from HTTP 404 to 200. Authenticated initialization negotiated MCP protocol `2025-03-26`, Hermes discovered exactly `hackernews__hn_get_stories`, `hackernews__hn_get_thread`, `hackernews__hn_get_user`, and `hackernews__hn_search_content`, and a one-item `hn_get_stories` call completed without error. From the Hermes VM, initialization without a token returned HTTP 401 and the `hermes-personal` key also returned HTTP 401 for the isolated `browser` group. Test output excluded the bearer token and hostile story text.

The private Telegram adapter test on 2026-08-28 passed a normal `telegram-ok` reply, `/status`, and a harmless terminal-tool call whose persisted session record contained the `terminal` invocation, tool result, and final reply. A controlled response after enabling the `auto` streaming transport completed through the restarted gateway, which resolved Telegram streaming as enabled. The bot profile uses the cropped Maia avatar, the adapter reports `telegram` and `api_server` as its connected platforms, and the allowlist remains limited to the single operator ID.

An on-demand QGA-assisted snapshot backup completed successfully and produced `backups-pve-sbx:backup/vzdump-qemu-200-2026_08_27-19_03_54.vma.zst`. The cluster's enabled `daily-backups` job also includes VMID `200` through its `all=1` selection.

## Caddy image lifecycle preparation

The [image workflow](../../../.github/workflows/hermes-caddy-image.yaml) prepares the first build layer of [#1074](https://github.com/kelchm/home-lab/issues/1074). No canonical owned artifact has been published or adopted yet; the guest still uses its manually built `localhost/hermes-caddy` image and the operations below. The build layer leaves the deployed Compose, Caddyfile, security settings, healthcheck and persistent TLS state unchanged.

Every relevant PR, including forks and stacked bases, builds and qualifies on a disposable GitHub-hosted native Linux amd64 runner with read-only repository access, no registry login and no production credentials. The [qualifier](caddy/qualify.sh) uses real port 443 with host `ip_unprivileged_port_start=1024`, UID/GID 1000, a read-only root, no-new-privileges, only `NET_BIND_SERVICE` and Caddy as PID 1. It tests a throwaway CA, trusted HTTPS and rejection of the wrong CA, the curl health command, and valid WebSocket upgrades with forwarded text echo on `/api/ws` and binary echo on `/api/pty`. Adding only `--init` must reproduce the privileged-bind failure. It also validates the production Caddyfile offline with a dummy credential and records actual linked core/plugin/Go/compiler/module inventory, comparing the core release with both pinned base payloads. Fixture state and containers are disposable; no guest or LAN runner participates.

Merging this build layer starts its first publication because it changes `Dockerfile.caddy`. Subsequent automatic publication requires a change to that file or `.dockerignore`; qualification scripts, workflow/tooling changes, Compose/Hermes updates and repository policy edits alone do not publish another image. The separate broad PR scope still qualifies those changes. A main-only manual workflow dispatch deliberately republishes after a workflow/qualification change or repairs a missed build.

The reusable image contains Caddy with the Cloudflare DNS module and a non-root UID/GID 1000 runtime; the deployment supplies the Hermes configuration and credentials. The publisher writes a unique `ghcr.io/kelchm/home-lab/caddy-cloudflare:sha-<full-source>-run-<id>-attempt-<attempt>` using short-lived `GITHUB_TOKEN` package permission and `mode=max` provenance. It pulls and qualifies the exact returned root digest before copying that root unchanged to `candidate`. Immediately before promotion it compares only the two image inputs against current main. If they changed, candidate stays unchanged and the run warns how to repair a missed newer build. An obsolete or failed build may retain its unique tag for diagnosis. Publication is serialized after scope selection; there is no Git writer, arbitrary-ref publisher, daily repair or automated deployment. First publication requires owner approval. New GitHub packages default to private; public visibility is a separate approved operation that this workflow does not perform. The real registry copy path first runs after merge, so it remains unproven until that publication succeeds.

The existing exact builder/runtime pins and Cloudflare version remain the initial inputs. Renovate tracks the annotated module version and groups builder/runtime core-release changes; same-release digest refreshes can proceed independently. Actual artifact checks reject release mismatches and module replacements. There is no separately regenerated module lock: exact direct versions and the immutable artifact inventory provide traceability, without claiming hermetic or byte-reproducible rebuilding. Owning this build does not remove upstream OS or Go dependency security lag. Dependency remediation remains a reviewed source change.

These CI results are validation and review evidence; main currently has no branch-protection rule enforcing them. The next stack layer will add digest delivery and the bounded guest updater **after a real canonical artifact exists**. It must preserve normal Compose commands, reviewed Git digest rollback, healthy certificate renewal and the current runtime contract. Promotion PRs must explicitly disable automerge and run tests; this preparation does not enable source-update automerge. Public visibility, a standing promotion-merge rollout policy, paused guest installation, and attended TLS/dashboard/WebSocket/PTY commissioning remain owner gates. No live acceptance box in #1074 is complete from a successful build alone.

## Operations

Stage this directory at `/opt/hermes` on the guest, keeping `managed/` root-owned and not writable by UID 1000. The deploy recreates both containers, so a managed-configuration change takes effect with the deploy. `/srv/caddy/.env` must already contain `CLOUDFLARE_API_TOKEN` with mode `0600` and root ownership. Caddy's two state directories must remain owned by its numeric runtime identity:

```sh
ssh kelchm@hermes.home.kelch.io 'sudo install -d -o 1000 -g 1000 -m 0750 /srv/caddy/data /srv/caddy/config && sudo chown -R 1000:1000 /srv/caddy/data /srv/caddy/config && cd /opt/hermes && sudo docker compose pull hermes && sudo docker compose build --pull caddy && sudo docker compose up -d --force-recreate'
```

### Recreate only Caddy from its running image

Use this procedure for a separately reviewed Caddy container-setting change, such as its healthcheck. First qualify the change locally against a real port-443 bind, with `net.ipv4.ip_unprivileged_port_start` set to 1024 as on the guest, under the same non-root user, capabilities and `no-new-privileges` setting. `init: true` fails that bind with the current image and must remain disabled. Merging the configuration does not change the guest. Recreating Caddy briefly interrupts HTTPS; obtain approval for that interruption. The full deployment command above also builds images and recreates Hermes, so use the targeted command below for this operation.

1. On the guest, record Caddy's image ID, init setting and container start time, Hermes's container ID and start time, and the served certificate's SHA-256 fingerprint. Stop if the guest differs unexpectedly from the reviewed rollout state. Keep `/srv/caddy/data`, `/srv/caddy/config`, `Caddyfile`, the managed Hermes configuration and both runtime environment files unchanged.
2. In `/opt/hermes`, save the current compose with `sudo cp -p compose.yaml compose.yaml.pre-caddy-recreate`. Stage the reviewed, merged `compose.yaml`, preserving its owner and mode. Check that the diff against the saved file contains only the reviewed Caddy change. Keep the saved file until acceptance passes.
3. Run on the guest. The image name in compose is `localhost/hermes-caddy`; this supplies its `latest` tag from the already-running image if needed. A conflicting tag requires reconciliation before proceeding. Builds and pulls are disabled, and dependencies are not recreated.

   ```sh
   set -eu
   cd /opt/hermes
   CADDY_IMAGE_ID=$(sudo docker inspect -f '{{.Image}}' hermes-caddy)
   CADDY_TAG_ID=$(sudo docker image inspect -f '{{.Id}}' localhost/hermes-caddy:latest 2>/dev/null || true)
   if [ -n "$CADDY_TAG_ID" ] && [ "$CADDY_TAG_ID" != "$CADDY_IMAGE_ID" ]; then
     printf '%s\n' 'Caddy latest tag differs from the running image; stop and reconcile.' >&2
     exit 1
   fi
   sudo docker image tag "$CADDY_IMAGE_ID" localhost/hermes-caddy:latest
   sudo docker compose up -d --no-deps --no-build --pull never caddy
   ```

4. Measure the HTTPS interruption by polling `/api/status`. Verify Caddy uses the same image ID, serves HTTPS on port 443 and becomes healthy. Record its actual init setting and PID 1; the current baseline has init unset and Caddy as PID 1. For a process-leak fix, require several natural 30-second healthchecks without accumulating zombie children and a process count near its thread count. The healthcheck has a two-minute start period. Confirm the served certificate fingerprint and Hermes's container ID and start time are unchanged. The ten-second stop timeout does not bound startup or recovery time if the new container fails. If Caddy logs a bind error or is restarting, or if HTTPS has not returned within 30 seconds, roll back at once rather than waiting through the restart loop.
5. If acceptance fails, restore the saved compose and recreate only Caddy from the same local image, with no build or pull:

   ```sh
   cd /opt/hermes
   sudo cp -p compose.yaml.pre-caddy-recreate compose.yaml
   sudo docker compose up -d --no-deps --no-build --pull never caddy
   ```

A Caddy-only restart clears existing zombies but does not change the healthcheck. Updating the Caddy image and automating its rollout remain separate work in [#1074](https://github.com/kelchm/home-lab/issues/1074).

### Hermes configuration and service checks

Before deploying a managed-configuration change or a new Hermes image, check the system prompt that the pinned image builds from `managed/`. [`check-model-guidance.py`](check-model-guidance.py) runs a turn against a fake Spark endpoint with no network, and fails unless the prompt has both guidance blocks and loses them when the `agent` pins are removed:

```sh
cd proxmox/guests/hermes-1 && docker run --rm --network none --user 1000:1000 -v "$PWD:/work:ro" --entrypoint /opt/hermes/.venv/bin/python "$(sed -n 's/^ *image: \(nousresearch\/hermes-agent:.*\)$/\1/p' compose.yaml)" /work/check-model-guidance.py
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
