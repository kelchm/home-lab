# Hermes Agent

`hermes-1` is the first persistent application guest on `pve-sbx`. It runs the official Hermes Agent container with its web dashboard and OpenAI-compatible API enabled for LAN clients.

This directory is the source for the application deployment and its operating record. The PVE VM was provisioned manually for this first workload; it is not yet represented by an OpenTofu resource.

| Setting | Value |
|---|---|
| VMID | `200` |
| Node | `pve-sbx-1` initially; migratable |
| Address | `10.32.21.100/24` on VLAN 21 |
| Guest | Debian 13 genericcloud, 2 vCPU, 4 GiB RAM, 32 GiB `local-lvm` |
| Dashboard | `http://10.32.21.100:9119` |
| API | `http://10.32.21.100:8642` |
| State | `/srv/hermes` in the guest, mounted at `/opt/data` in the container |
| Backup | Included in cluster job `daily-backups` |

The VM uses the official Debian 13 genericcloud image with SHA-512 `184761b0dad0f9ace02f9298050ca96ce3caa39a461a47706d47ff9698b59933918b91b40177fbd4d392f6446af8b4d18ecb94caca988169b19641606bf34003`. The container image is pinned to the multi-architecture digest published for Hermes `v2026.8.27`.

The default model is the active OpenAI-compatible `deepseek-v4-flash-dspark` endpoint on `spark-1:8888`. The Qwen endpoint on `spark-1:8000` is mutually exclusive with that two-node deployment; change the Hermes model configuration when the operator changes Spark workloads.

The optional MetaMCP entry is checked in but disabled. Enabling it needs a dedicated MetaMCP API key and a narrow route from this guest to the service. Do that through a normal API or CLI administration path when one is available; interactive Kanidm login in an automation browser is deliberately not part of this deployment.

The runtime `.env` is intentionally not in Git. It contains the dashboard authentication values and API-server token. A root-readable recovery copy of the client credentials is stored at `/root/hermes-access.txt` inside the backed-up VM. These credentials have not been copied into 1Password or another external secret manager.

No messaging adapter is configured. Hermes Desktop for macOS connects to the dashboard URL on port `9119`; Safari on iOS can use the same authenticated web UI. A future native iOS client can use the authenticated API on port `8642`. The IP address is the canonical client endpoint until a DNS record is added through the normal network administration path.

## Deployment verification

The initial deployment on 2026-08-27 passed dashboard basic-auth login, an authenticated API turn through the DeepSeek endpoint, a terminal-tool execution, and a full VM reboot. After reboot, QEMU Guest Agent and Docker were active, the Hermes container was running with zero restarts, and both the dashboard and API reported healthy.

An on-demand QGA-assisted snapshot backup completed successfully and produced `backups-pve-sbx:backup/vzdump-qemu-200-2026_08_27-19_03_54.vma.zst`. The cluster's enabled `daily-backups` job also includes VMID `200` through its `all=1` selection.

## Operations

Deploy the checked-in configuration after copying this directory to `/opt/hermes` on the guest:

```sh
ssh kelchm@10.32.21.100 'cd /opt/hermes && sudo docker compose pull && sudo docker compose up -d'
```

Inspect the service without printing credentials:

```sh
ssh kelchm@10.32.21.100 'cd /opt/hermes && sudo docker compose ps && sudo docker logs --tail 100 hermes'
curl -sS http://10.32.21.100:9119/api/status | jq '{auth_required, auth_providers}'
```

Retrieve the dashboard username/password or API-server token only when configuring a client:

```sh
ssh -t kelchm@10.32.21.100 'sudo less /root/hermes-access.txt'
```
