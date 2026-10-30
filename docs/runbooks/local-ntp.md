# Local NTP clients

Use the two UPS Pis for IPv4 NTP without depending on Kubernetes: `ups-compute-rack` at `10.32.20.111` and `ups-office` at `10.32.20.112`, UDP destination port 123. Server configuration and recovery live in [rpi-nixos](https://github.com/kelchm/rpi-nixos#local-time-servers); [host monitoring](host-monitoring.md#pi) covers their existing chrony metrics and warnings. [#1031](https://github.com/kelchm/home-lab/issues/1031) tracks unfinished client activation and acceptance.

## Verified state and activation boundary

Both servers were accepted on 2026-10-30 at rpi-nixos merge `830d2cfa` (reviewed source `6d8fc477`), rack generation 14 and office generation 8. They use stock chrony, the dish at `192.168.100.1` and public sources, with no unconditional trust/preference, local-clock serving or NTS. Their host firewall and chrony ACL permit only `10.32.{1,5,10,20,21,25,30,90}.0/24` over IPv4 UDP 123. Guest (99), Remote Admin (19), IPv6 and public inbound NTP are excluded. Only the peer-Pi VLAN 20 client path has been tested. Controlled dish-only/public-only checks passed and were restored; automatic warm failover timing and real WAN-outage operation are unverified.

Read-only Talos checks on 2026-10-30 found all three nodes at v1.14.4, Ready, with only `162.159.200.1` and `162.159.200.123` in their effective time-server list, `useNTS: false` and `synced: true`. The [source patch](../../talos/patches/global/machine-time.yaml) adds both local addresses before those retained public alternatives. Merging it changes the next generated machine configurations; Flux does not apply Talos configuration. No gateway, DHCP or client activation is recorded here.

## UniFi current state and proposed change

Read on 2026-10-30 at 22:10–22:12 UTC through a separate signed-in browser tab on Gateway DMP, Network 10.6.109 / UniFi OS 5.1.34. The Networks and Policy Table UI plus their same-origin read-only GETs supplied the selected network, DHCP, policy, zone and route fields below. No settings were changed or probes run; the reader tab was closed, preserving the owner's original tab. This establishes controller configuration, not packet reachability or client adoption. Re-read these objects before activation and stop if their relevant fields/order differ.

### DHCP option 42

All listed networks are enabled with IPv6 interface type `none`. All seven intended DHCP servers have `dhcpd_enabled: true`, `dhcp_relay_enabled: false`, `dhcpd_ntp_enabled: false`, automatic DNS and a lease time of 86400 seconds. No NTP server-address fields were returned while the option was disabled. The Main network's existing editor exposes the NTP form model as `dhcpd.ntp = {enabled: false, servers: []}` without changing it.

| Network / subnet | Current network ID | DHCP pool | Proposed NTP option |
|---|---|---|---|
| Default / `10.32.1.0/24` | `659836616397306602ac70fb` | `.100`–`.254` | Enable; both Pi addresses |
| Cameras / `10.32.5.0/24` | `67683b4cd1af80275b925074` | `.100`–`.254` | Enable; both Pi addresses |
| Main / `10.32.10.0/24` | `67690cc9d1af80275b92a0a8` | `.100`–`.254` | Enable; both Pi addresses |
| Infra Mgmt / `10.32.20.0/24` | `67691489d1af80275b92a3c5` | `.100`–`.254` | Enable; both Pi addresses |
| Workloads / `10.32.21.0/24` | `6a88f7c4699faf3d96378d63` | `.200`–`.239` | Enable; both Pi addresses |
| K8s Prod / `10.32.30.0/24` | `69260c7e17fd1a27221f9662` | `.200`–`.254` | Enable; both Pi addresses; Talos remains explicitly configured |
| IoT / `10.32.90.0/24` | `676914f6d1af80275b92a3cf` | `.100`–`.254` | Enable; both Pi addresses |
| Storage / `10.32.25.0/24` | `69e7a2c642f12abb67e4ae36` | Disabled | Unchanged; static clients need explicit configuration |
| Remote Admin / `10.32.19.0/24` | `6a97a546a6bd0ee70a238b5c` | Disabled | Unchanged; excluded |
| Guest / `10.32.99.0/24` | `67699996d1af80275b92d821` | `.6`–`.254` | Unchanged; excluded |

After approval, use **Networks → selected network → DHCP Server → NTP Server** for the seven target networks: enable the predefined option and set the ordered server list to `10.32.20.111`, `10.32.20.112` (desired editor values: `dhcpd.ntp.enabled: true`, `dhcpd.ntp.servers: ["10.32.20.111", "10.32.20.112"]`). Preserve every other field, custom option and reservation. Do not enable DHCP on Storage/Remote Admin or add local NTP on Guest, K8s Sandbox, Services or other networks outside the Pi ACL. [Ubiquiti identifies this predefined setting as option 42](https://help.ui.com/hc/en-us/articles/360012097513-UniFi-DHCP-Server); [RFC 2132 §8.3](https://www.rfc-editor.org/rfc/rfc2132.html#section-8.3) defines its ordered IPv4-address list. Do not add a duplicate custom option 42.

The editor model above is **not** a private-API write payload. The serialized server-address field names after saving have not been observed because every current option is disabled and no write was authorized. Use the supported UI and read the resulting network object back after each approved save. The read endpoint is `GET /proxy/network/api/s/default/rest/networkconf`; select NTP/DHCP fields and omit unrelated secrets. Rollback is to disable NTP Server and restore the prior empty server list on each changed network, while preserving its other current settings. DHCP advertisement does not configure the gateway's own clock, static clients or clients that ignore option 42. The Pis provide public upstream alternatives; explicit Talos clients also retain their existing public addresses.

### One firewall exception

All intended client networks and the two Pi destinations belong to **Internal**, zone ID `677f0352a46263726567a989`. Current ordered Internal → Internal policies include:

| Policy | Current ID | Index / relevant match |
|---|---|---|
| Camera Access | `69265f0617fd1a27221fafd4` | `10000`; Main → Cameras, allow |
| Block Workloads to Protected Networks | `6a90602ad0acda27e31f9be6` | `10005`; Workloads → Infra Mgmt, K8s Prod, Storage, block all protocols/states |
| Block K8s Prod to Workloads | `6a906094d0acda27e31f9e54` | `10006`; K8s Prod → Workloads, block |
| Camera Access (Return) | `677f0352a46263726567a989677f0352a46263726567a98930000` | `30000`; established/related Cameras → Main; parent `69265f0617fd1a27221fafd4` |
| Isolated Networks | `677f0352a46263726567a989677f0352a46263726567a98930005` | `30005`; source `10.32.25.0/24`, `10.32.5.0/24` → any Internal destination, block |
| Allow All Traffic | `677f0352a46263726567a989677f0352a46263726567a9892147483647` | `2147483647`; Internal → Internal, allow |

Cameras and Storage have `network_isolation_enabled: true`; Workloads is blocked from Infra Mgmt by the custom rule above. Default, Main, K8s Prod and IoT have no matching initiation block to these Pi IPs and reach the final allow. Infra Mgmt clients use the same subnet. The relevant policy GETs are `/proxy/network/v2/api/site/default/firewall-policies` and `/proxy/network/v2/api/site/default/firewall/zone`; order by `index`, and verify selectors, enabled state and parent relationships rather than names alone.

The proposed live delta is **one new forward policy**, `Allow Cameras Storage Workloads to Pi NTP`, inserted immediately above the current `Block Workloads to Protected Networks` rule (`10005`), preserving all existing policies' relative order:

- Action **Allow**, enabled; IPv4 only; UDP; all connection states; no schedule restriction.
- Source zone **Internal**; networks **Cameras**, **Storage**, **Workloads** using their exact IDs above; source port **Any**.
- Destination zone **Internal**; specific IPs **`10.32.20.111/32`, `10.32.20.112/32`**; destination port **123**. The existing `NTP` port-group ID `69266b3b17fd1a27221fb3d6` contains only `123`, but a specific port avoids an extra object dependency.
- **Auto Allow Return Traffic off**: Pi replies originate in Infra Mgmt, match none of these initiation blocks and already reach the existing Internal → Internal `Allow All Traffic`. [UniFi's return guidance](https://help.ui.com/hc/en-us/articles/115003173168-Zone-Based-Firewalls-in-UniFi) does not require an extra return policy when an existing policy allows replies. Preserve every existing generated return, including Camera Access's parent relationship.

Do not widen the protected-network or isolation rules, add other source networks, permit all of Infra Mgmt, redirect/NAT NTP, or add TCP/IPv6/public exposure. Guest and Remote Admin receive no new exception; the existing Pi ACL/firewall continues to exclude them. The new rule's ID is allocated only on creation: record it after the approved save. Rollback removes/disables that new forward alone, after restoring affected clients/DHCP; no generated return is expected. Re-read the unchanged containment and return policies afterward.

### Preserve dish routing

The enabled `Starlink Management` policy-based route is ID `67159e448ebb2e76caeba50c`: `target_devices: [{type: "ALL_CLIENTS"}]`, IPv4 destination range **`192.168.100.1`–`192.168.100.255`**, `network_id: 659836616397306602ac70fa`, empty `next_hop`, and `kill_switch_enabled: false`. That network is **Starlink (WAN2)**, `wan_networkgroup: WAN2`. The enabled same-named static route is ID `671599758ebb2e76caeba251`, destination `192.168.100.1/32`, interface `WAN2`, distance `3`, type `interface-route`. The read endpoints are `/proxy/network/v2/api/site/default/trafficroutes`, `/proxy/network/api/s/default/rest/routing` and the network GET above. Preserve both routes and WAN2 unchanged; the historical shorthand `192.168.100.0/24` is not the traffic route's exact stored range. This read-back does not retest dish reachability.

## Talos behavior

The proposed list is `10.32.20.111`, `10.32.20.112`, `162.159.200.1`, `162.159.200.123`. For the pinned v1.14.4, the [SNTP implementation](https://github.com/siderolabs/talos/blob/v1.14.4/internal/pkg/ntp/ntp.go) tries the resolved list in order when choosing a server, then keeps querying the last successful server. A query failure triggers another search, skipping the just-failed server for that pass. A changed list resets that remembered server and wakes the loop. A working public server can therefore remain selected after local service recovers; list order is not strict ongoing preference or automatic failback. Talos does not run chrony's multi-source comparison. Public addresses remain alternatives if local queries fail, not concurrently cross-checked references.

The [time controller](https://github.com/siderolabs/talos/blob/v1.14.4/internal/app/machined/pkg/controllers/time/sync.go) reconciles a changed server list live. The existing `machine.time` form disables NTS ([provider implementation](https://github.com/siderolabs/talos/blob/v1.14.4/pkg/machinery/config/types/v1alpha1/v1alpha1_provider.go)); keep it for these plain-NTP Pis. No reboot, daemon restart or time step is requested by this procedure. Talos can itself step an offset over 400 ms, so stop on a large offset or unexpected clock jump. `TimeStatus.synced` is sticky once established (and can also be satisfied by a boot timeout); inspect fresh NTP responses and clock adjustments, not that flag alone. [`talosctl time`](https://github.com/siderolabs/talos/blob/v1.14.4/internal/app/machined/internal/server/v1alpha1/v1alpha1_time.go) issues a new query and defaults to the first configured server; it is not evidence of which server the background syncer selected.

## Activation and acceptance, after separate approval

1. Re-read the UniFi objects above, confirm they still match, and approve the one forward policy plus seven DHCP option changes with their rollback values. Confirm both Pi scrapes and tracking/source metrics are fresh and healthy through the existing monitoring. Activate only the approved forward policy, preserving existing returns and both WAN2 dish routes. Read back the resulting objects and order before changing clients. Do not combine this with a WAN outage or Pi fault test.
2. From a credential-bearing checkout of the reviewed, merged sources, handle one Talos node at a time: `10.32.30.11`, then `.12`, then `.13`. Record its effective list, `timestatus`, `ntpstatus` and recent `time.SyncController` log baseline. Run `task talos:apply-node IP=<node-ip> MODE=no-reboot`. This regenerates configuration and runs `scripts/talos/preserve-encryption.sh`; read the dry-run and answer its confirmation only when the sole semantic change is the intended server list. Preserve secretbox `key2` and the identity fallback with unchanged key material. Stop on any encryption, release, CNI, volume or other unrelated diff. Do not invoke the internal confirmed task or bypass regeneration.
3. Verify `talosctl -n <node-ip> get timeservers -o yaml`, `get timestatus -o yaml`, `get ntpstatus -o yaml` and `logs controller-runtime --tail=300`. Require a new successful NTP response and a non-spike clock adjustment via a Pi in `time.SyncController` after application, no recurring query errors or consecutive spike growth, and no unexpected clock jump. A rendered list, `synced: true`, a single discarded spike or a manual `talosctl time --check` reply alone is insufficient. If a fresh successful adjustment is not observed within two normal maximum poll intervals (about 69 minutes on this version), stop and diagnose; do not move to the next node or restart services to force evidence.
4. Before advancing, require etcd quorum, all Kubernetes nodes Ready and uncordoned, every in-use Longhorn volume attached and healthy with none degraded/faulted, and every Longhorn instance-manager Running/Ready with `longhorn-system/storage-network` on `lhnet1`. Use the [Talos rollout checks](../../.agents/skills/talos-rollout/SKILL.md) for the selected node. If the intended change cannot apply without a reboot, stop; a separately approved reboot needs that skill's full preflight, snapshot and one-node-at-a-time procedure. Do not drain/reboot for this client change as a workaround.
5. Advertise option 42 on one approved DHCP network first, then verify a representative client that supports it. Let its normal lease renewal occur, or approve a targeted renewal independently. On a systemd-networkd/timesyncd Linux client, confirm DHCP [`UseNTP`](https://www.freedesktop.org/software/systemd/man/262/systemd.network.html#UseNTP=) is enabled; read `networkctl status <interface>`, the received lease's NTP values, `timedatectl show-timesync --all` and the timesyncd journal. On a chrony client, inspect its DHCP-generated sources, `chronyc -n sources -v` and `chronyc tracking`. Record OS/daemon, VLAN/IP, receipt of **both** addresses, selected local server and fresh sync timestamp/offset. Confirm DNS, gateway, address/reservation and other options are unchanged. If the client ignores option 42, record that limitation and arrange separate explicit configuration; advertisement alone is not adoption. Repeat per approved DHCP network only after acceptance.
6. Keep #1031 open until representative infrastructure and DHCP client adoption and failure checks are recorded. A later separately approved, client-scoped single-server-loss test must record which server was selected, actual fallback timing and recovery; stop the test on the client rather than changing Pi service/source state. Real WAN-outage behavior remains a separate unverified condition. Do not infer either result from rendering or the earlier controlled Pi checks.

## Rollback

For a failing Talos node, restore the previous public-only list (`162.159.200.1`, `162.159.200.123`) in the source patch on the rollback branch, then regenerate and use the same `task talos:apply-node IP=<node-ip> MODE=no-reboot` confirmation path for that node. Require only that list difference, the same encryption preservation and a fresh successful public-source adjustment before advancing. Reverting the Git commit alone does not restore live Talos state; never apply an old generated machine configuration wholesale.

For DHCP, restore each changed network's recorded previous NTP option values and enable state, preserving the rest of the current object, then read back and check a representative client's next lease. Withdraw local advertisement and restore explicit clients before disabling any new gateway allowance they rely on. Remove/disable only the recorded new NTP forward policy; its proposed Auto Allow Return Traffic setting creates no generated return. Preserve all pre-existing policies and routes. Record acceptance or rollback evidence in #1031 and update this runbook's verified state after the operation.
