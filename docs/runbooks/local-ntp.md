# Local NTP clients

Use the UPS Pis for local time service over IPv4 UDP 123:

| Server | Address |
|---|---|
| ups-compute-rack | `10.32.20.111` |
| ups-office | `10.32.20.112` |

Both servers and their central alerts were accepted on 2026-10-30. See [rpi-nixos](https://github.com/kelchm/rpi-nixos#local-time-servers) for server operation and [host monitoring](host-monitoring.md#pi) for alerts. The client changes below are pending; [#1031](https://github.com/kelchm/home-lab/issues/1031) tracks activation and acceptance. Merging the [Talos patch](../../talos/patches/global/machine-time.yaml) does not apply it to running nodes.

## Allow access in UniFi

Create one policy named **Allow Cameras Storage Workloads to Pi NTP**:

| Setting | Value |
|---|---|
| Action / zones | Allow; Internal → Internal |
| Sources | Cameras (VLAN 5), Workloads (21), Storage (25); source port Any |
| Destinations | `10.32.20.111/32`, `10.32.20.112/32` |
| Protocol / destination port | IPv4 UDP / 123 |
| Connection states / schedule | All / Always |
| Auto Allow Return Traffic | Off; existing policies already permit replies |

Place it immediately above **Block Workloads to Protected Networks**, ahead of the Cameras/Storage isolation block. Confirm the current policy order before saving. Other intended networks already have access. Preserve existing isolation policies, return rules and the **Starlink Management** routes through WAN2.

## Configure Talos

The intended list is `10.32.20.111`, `10.32.20.112`, `162.159.200.1`, `162.159.200.123`. Talos tries the list when selecting a server, then keeps using a working server. It can remain on a public server after local service recovers; list order does not guarantee automatic failback.

After rollout approval, use the reviewed, merged sources and handle nodes `10.32.30.11`, `.12`, then `.13`, one at a time. Run the [Talos preflight and health checks](../../.agents/skills/talos-rollout/SKILL.md), then:

```sh
task talos:apply-node IP=<node-ip> MODE=no-reboot
```

The task regenerates configuration and preserves encryption keys. Confirm that the dry-run changes only the time-server list before applying. Stop on unrelated changes or a need to reboot.

Check the effective list and background synchronization:

```sh
talosctl -n <node-ip> get timeservers -o yaml
talosctl -n <node-ip> get ntpstatus -o yaml
talosctl -n <node-ip> logs controller-runtime --tail=300
```

Require a fresh successful Pi response and a normal clock adjustment in `time.SyncController`. A manual `talosctl time --check` response or `synced: true` alone does not prove background adoption. Stop on recurring errors, an offset above 400 ms or an unexpected clock jump. Before the next node, confirm etcd quorum, all nodes Ready, all in-use Longhorn volumes attached/healthy and all instance-managers Ready with `lhnet1`.

## Advertise time servers through DHCP

In **Networks → selected network → DHCP Server → NTP Server**, enable the predefined NTP option and enter both Pi addresses, rack first. This is DHCP option 42; do not add a duplicate custom option.

Apply to **Default, Cameras, Main, Infra Mgmt, Workloads, K8s Prod and IoT**. Start with one network and verify a capable client before repeating. Preserve its other DHCP settings and reservations. Storage has DHCP disabled; leave Guest and Remote Admin excluded.

After a normal lease renewal, confirm the client received both addresses and synchronized to a Pi with a fresh timestamp and sensible offset. For timesyncd use `timedatectl show-timesync --all` and its journal; for chrony use `chronyc -n sources -v` and `chronyc tracking`. Record the client and VLAN in #1031. DHCP advertisement does not configure static clients, the gateway's clock or clients that ignore option 42.

## Roll back

- **Talos:** restore the previous public-only list (`162.159.200.1`, `162.159.200.123`) and apply through the same task, one node at a time. Verify fresh synchronization. A Git revert alone does not change running nodes.
- **DHCP:** disable the NTP option and restore its previous empty list on each changed network. Verify the client's next lease.
- **Firewall:** after restoring clients, remove only the new Pi NTP policy. Preserve existing rules and WAN2 routes.

## Remaining acceptance

The Pis have served time using both the dish and public source groups. Only the peer-Pi VLAN 20 client path has been tested. Record client adoption before closing #1031; client failover timing and real WAN-outage behavior still need separate tests.
