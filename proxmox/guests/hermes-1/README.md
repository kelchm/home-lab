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

## Caddy image delivery

Preparation only: the guest still uses its manually built `localhost/hermes-caddy` image. [#1103](https://github.com/kelchm/home-lab/pull/1103) published and qualified the public image in [run 40870944297](https://github.com/kelchm/home-lab/actions/runs/40870944297): `ghcr.io/kelchm/home-lab/caddy-cloudflare@sha256:7c3876c8fc82df32c80d43a134d2f185a70e88c78a2d431071cb5241e29403f1` (Caddy 2.11.7, Cloudflare 0.2.4, Go 1.27.3). Adoption and live acceptance remain open in [#1074](https://github.com/kelchm/home-lab/issues/1074). Merging these files does not install a deployer on this guest.

[`compose.yaml`](compose.yaml) operates only Hermes, with its existing project identity. [`compose.caddy.yaml`](compose.caddy.yaml) defines only Caddy in the distinct `hermes-caddy` project. Always use `docker compose -f compose.caddy.yaml …` for Caddy; plain `docker compose …` continues to operate Hermes. Caddy has no cross-project `depends_on`; its existing HTTPS healthcheck exercises the Hermes backend. The immutable digest selects the artifact even while the `candidate` tag moves. Renovate discovers that tag through the normal Compose manager and proposes digest promotions with automerge disabled and tests enabled. Review the publication/qualification receipt for the proposed digest before merging.

The [image workflow](../../../.github/workflows/hermes-caddy-image.yaml) builds and qualifies on disposable GitHub-hosted native Linux amd64 runners without production credentials. The [qualifier](caddy/qualify.sh) exercises the non-root port-443 bind, curl health command, trusted TLS and WebSocket/PTY forwarding with synthetic state, and confirms that adding init breaks the current privileged-bind contract. Only `Dockerfile.caddy` or `.dockerignore` changes trigger automatic publication; configuration changes still trigger PR qualification. The publisher qualifies the returned immutable root before copying it unchanged to `candidate`, with source/run provenance. These checks do not prove guest acceptance, and branch protection does not currently enforce them. The Dockerfile's core/module pins remain Renovate-managed; this delivery change does not add BuildKit `driver-opts` tracking, which remains work in #1074.

### Standard doco-cd behavior

[`.doco-cd.yml`](.doco-cd.yml) deploys only `hermes-caddy` and the separate `doco-cd` self target. [`doco-cd/compose.yaml`](doco-cd/compose.yaml) follows the lab's pinned 0.130.0 bootstrap/self-update pattern and polls public `main` every three minutes. It uses the normal Docker-socket and `/var/lib/doco-cd` data mounts, plus a read-only bind of the existing `/srv/caddy/.env` file at the same absolute path so the Compose loader can read it. Keep the file root-owned and `0600`; the deployer has Docker-daemon authority. No token or ACME-state migration is required. Relative Caddyfile binds resolve from doco-cd's commit export; persistent `/data` and `/config` remain the host's existing `/srv/caddy/data` and `/srv/caddy/config`, owned by numeric 1000:1000.

Pinned doco-cd has [no service selector](https://github.com/kimdre/doco-cd/blob/5d001f9489b3aa97eb176e7eb80c2ba2fbe751c1/internal/config/deploy/deploy.go#L60-L95): failed deployments can force-recreate the whole project. The separate Caddy project keeps Hermes outside deployment, orphan removal and retries, following the [monitoring/GPU split](../../../sparks/monitoring/.doco-cd.yml). The Caddy target uses `timeout: 5m`, `retry: {limit: 1, delay: 5m}`, `reconciliation: false` and `prune_images: false`. One retry is eligible at least five minutes after failure on a subsequent poll. This is not a lifetime retry bound: a new revision/effective configuration, including an unrelated merge, or a deployer restart resets eligibility. See the pinned [retry settings](https://github.com/kimdre/doco-cd/blob/5d001f9489b3aa97eb176e7eb80c2ba2fbe751c1/wiki/docs/Deploy-Settings.md#retry-settings).

Images pull before recreation, so pull failure leaves the existing Caddy running. After recreation, the native health gate requires three consecutive running/healthy samples one second apart; the five-minute timeout accommodates the existing two-minute healthcheck start period, not a soak test or a downtime bound. An ordinary failure is logged and does **not** restore the previous Caddy image. There is no ongoing reconciliation or independent outage watchdog. HTTPS can remain unavailable until a fix or Git revert; Hermes stays running. Native Apprise notifications are not configured, so there is no claimed push-alert delivery. The deployer's native self-update recovery is separate from Caddy recovery; use the [self-update runbook](../../../docs/runbooks/doco-cd-self-update.md) for its handover/recovery behavior.

## Operations

Keep the manually operated application copy in `/opt/hermes`, with `managed/` root-owned and not writable by UID 1000. Keep a separate deployer bootstrap/recovery copy in `/opt/doco-cd`. doco-cd does not refresh these local copies; refresh them from a reviewed Git revision before manual operation. Never run a project-wide `down` or `--remove-orphans` against the old combined definition during adoption.

For a separately authorized Hermes image or managed-configuration update, stage its reviewed files and use plain Compose. Recreation loads the managed configuration without touching Caddy:

```sh
cd /opt/hermes
sudo docker compose pull hermes
sudo docker compose up -d --force-recreate hermes
```

### One-time attended Caddy adoption

Run only during separately authorized installation. Stage the merged files before starting the deployer. Expect a brief HTTPS interruption when replacing Caddy; leave Hermes running throughout.

1. Record the running Caddy image ID/reference and Compose project label, Hermes container ID/start time, and current served certificate fingerprint. Save the old combined `compose.yaml` as `/opt/hermes/compose.yaml.pre-caddy-adoption` before replacing it. Keep the old project name for recovery. Verify `/srv/caddy/.env` is still root-owned/0600 and both state directories remain 1000:1000; do not copy, print or replace credentials or ACME state.
2. Stage the reviewed `compose.yaml`, `compose.caddy.yaml`, unchanged `Caddyfile` and `managed/` in `/opt/hermes`, and `doco-cd/compose.yaml` as `/opt/doco-cd/compose.yaml`. Preserve the running image under a local recovery tag and pre-pull both new images **before** touching the old Caddy. Run on the guest:

   ```sh
   set -eu
   cd /opt/hermes
   sudo docker inspect -f '{{.Image}} {{.Config.Image}} {{index .Config.Labels "com.docker.compose.project"}}' hermes-caddy
   sudo docker inspect -f '{{.Id}} {{.State.StartedAt}}' hermes
   sudo docker image tag "$(sudo docker inspect -f '{{.Image}}' hermes-caddy)" localhost/hermes-caddy:recovery
   sudo docker compose -f compose.caddy.yaml pull caddy
   sudo docker compose -f /opt/doco-cd/compose.yaml pull
   ```

3. Only after successful pulls, remove just the old Caddy container to release its fixed name and port 443. Start Caddy under its new project identity without a build or another pull:

   ```sh
   cd /opt/hermes
   sudo docker stop hermes-caddy
   sudo docker rm hermes-caddy
   sudo docker compose -f compose.caddy.yaml up -d --no-build --pull never --wait --wait-timeout 300 caddy
   ```

4. Verify Caddy's exact image and `com.docker.compose.project=hermes-caddy`, UID/GID 1000, init unset, healthy status, existing certificate reuse, and unchanged Hermes ID/start time. Check a fresh trusted TLS handshake, dashboard sign-in, authenticated `/api/ws` and `/api/pty` upgrades and traffic. Confirm natural curl healthchecks do not accumulate zombie children. Record evidence in #1074 without credentials. On failure, use the recovery below before proceeding.
5. After Caddy passes, bootstrap the deployer with `sudo docker compose -f /opt/doco-cd/compose.yaml up -d`. Allow native self-takeover; inspect final deployer health, successful repository polls, and `cd.doco.deployment.target.sha`/`cd.doco.deployment.name` labels on both targets. A bootstrap `up` result alone does not prove takeover. Check logs with `sudo docker logs --since 10m doco-cd`, and confirm Hermes still has its original ID/start time. Later acceptance must include a real Renovate-originated Caddy update; this procedure alone does not close #1074.

### Git revert and break-glass recovery

For a bad promotion, merge a normal Git revert of the digest change (or restore the prior digest in a reviewed fix). The new revision becomes eligible on the next successful poll even after retry exhaustion. Revert any accompanying bad configuration as needed; this restores configuration, not persistent data. Record the prior image and retain it locally before an attended update; `prune_images: false` avoids deployer pruning but does not protect against manual Docker pruning.

For immediate manual recovery, stop doco-cd if installed so it cannot reapply the bad desired state. Preserve its data and let any native self-update handover finish before manual deployer recovery. Use the saved known-good Compose definition and the recorded image, with the same token/state binds. For first-adoption recovery, remove only the failed new Caddy with `sudo docker rm -f hermes-caddy`, then save this ordinary override as `/opt/hermes/compose.caddy-recovery.yaml`:

```yaml
services:
  caddy:
    image: localhost/hermes-caddy:recovery
```

Set `OLD_CADDY_PROJECT` to the recorded old project name (normally `hermes`) and use the saved pre-adoption definition. `--no-deps` leaves Hermes alone:

```sh
cd /opt/hermes
sudo docker compose -p "${OLD_CADDY_PROJECT:?set the recorded old project name}" -f compose.yaml.pre-caddy-adoption -f compose.caddy-recovery.yaml up -d --no-deps --no-build --pull never --wait --wait-timeout 300 caddy
```

After adoption, use a saved known-good `compose.caddy.yaml` and the same image override with `docker compose -f compose.caddy.yaml -f compose.caddy-recovery.yaml up -d --no-deps --no-build --pull never --wait --wait-timeout 300 caddy`. Recheck TLS/dashboard/WS/PTY and Hermes continuity. Keep the deployer stopped until Git declares the recovered image/configuration, then return Caddy to the `hermes-caddy` project if initial adoption was undone, refresh local recovery copies, remove the override and resume the deployer. Do not delete state directories or fetch a new DNS token for an image rollback.

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
