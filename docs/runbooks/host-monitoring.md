# Host monitoring

Metrics from the three PVE nodes, the two DGX Sparks, the NAS, the UniFi network devices, the two WAN devices and the NixOS Pis. The lab's metrics backend runs in `k8s-prod`; these hosts push to it. Each host runs ordinary exporters and a `vmagent` in Docker Compose, deployed from `main` by a host-local doco-cd as on the [NAS](../../synology/README.md); a [Pi](#pi) runs them as NixOS services instead. `vmagent` scrapes the exporters over loopback and pushes to `https://metrics-ingest.home.kelch.io`, queueing up to 1 GiB on disk while the endpoint is unreachable. Nothing on a host listens beyond loopback except doco-cd on PVE (see [PVE](#pve-node)) and the sparkDash dashboard on `spark-1`, at `10.32.21.31:5555` behind an access token. The Proxmox API, the UniFi console, the Starlink dish and the T-Mobile gateway are read separately by in-cluster exporters. As on the NAS, doco-cd holds the host's Docker socket, so whoever can merge to `main` controls every enrolled host.

## State

The PVE nodes and Sparks are enrolled and the Proxmox API exporter is running, since 2026-10-04. The NAS is enrolled since 2026-10-17 ([#839](https://github.com/kelchm/home-lab/issues/839)): its three jobs report, all eight bays and both the storage pool and volume report DSM status, and `diskID` arrives decoded as `Disk N`. UniFi devices are collected since 2026-10-17 ([#843](https://github.com/kelchm/home-lab/issues/843)): `up{job="unifi-api"}` is 1, the ten adopted devices (gateway, five switches, four access points) report with none disconnected, and the pod uses about 20 MiB and 10m CPU.

The [WAN devices](#wan-devices) are collected since 2026-10-18 ([#857](https://github.com/kelchm/home-lab/issues/857)): `up` is 1 for `starlink-api` and `tmobile-api` and `starlink_dish_up` is 1, with no gaps in the first half hour; the jobs hold 88 and 19 series, with no location or GPS series; vmalert has the five WAN rules loaded and inactive, and they have fired only in their promtool fixtures; and the pods use 15 MiB and 7 MiB at about 1m CPU. Both pods run on `k8s-prod-1`, which reached the dish only after the traffic route described there applied to all clients; afterwards every node reached it and `k8s-prod-1`'s internet flows still left by both WANs.

The first Pi, `streamer-basement-workbench`, is enrolled since 2026-10-18: `pi-node` and `pi-vmagent` report at 30-second spacing with `platform=pi`, `wlan0` reports traffic, and memory pressure arrives after the reboot that turns it on. The UPS monitors `ups-compute-rack` and `ups-office` are enrolled the same day: `pi-nut` reports each UPS with its `ups` label, its status flags, runtime, charge and load, upsd and upsmon kept running through the deploy and came back after the reboot, and LAN reads of port 3493 worked before and after. Both are Raspberry Pi 5s, whose Raspberry Pi device tree disables the memory cgroup by default; their configuration adds `cgroup_enable=memory` after it, so the profile's memory ceilings hold on every Pi, and vmagent's `vm_available_memory_bytes` reports its 96 MiB limit.

SMART collection covers the three cluster nodes, three PVE nodes and two Sparks with upstream smartctl_exporter v0.15.0. Athena retains DSM SNMP collection, including its SMART attribute table. On 2026-10-18, all five PVE/Spark scrape targets reported in VictoriaMetrics, fresh container smartctl reads returned healthy status, and runtime inspection confirmed the explicit NVMe mapping, only SYS_ADMIN, default AppArmor and privileged mode off. Initial exporter memory use was 6–8 MiB against the 128 MiB limit, with no OOMs or restarts. Cluster series use node names for `instance` and `platform="k8s"`. Repeat the [drive verification](#verify-drive-health) after changes.

The deployer definitions enable native self-update. Its verified version, migration status, manual apply, and handover checks are maintained in the [self-update runbook](doco-cd-self-update.md).

Verified on the hosts that day:

- **Ingest:** from both Sparks and a PVE node, the endpoint resolves, presents a valid certificate, and refuses requests with no token, a wrong token, or a query path. Each host's series arrive with its own `instance` and `platform`.
- **Sparks:** the head rank's inference series arrive at exact one-second spacing, labelled from SparkRun's container labels; the worker has no inference target. Neither inference container was restarted. A short inference probe measured 93.0 tokens/s before enrollment and 92.8 after.
- **Replay:** with `spark-2`'s token made invalid for three minutes, vmagent queued 533 KB and dropped nothing; after the token was restored the backend held every sample from that window with its original timestamp.
- **PVE:** on each node, `ip_forward`, `br_netfilter`, the forward policy and the firewall rulesets were identical before and after installing Docker, and a guest on that node kept reaching its gateway.
- **Proxmox API:** quorum, node and guest state, start-on-boot flags, storage use and the two guests outside the backup job match what the PVE API reports directly.

Verified since, as the events happened:

- **Model swaps:** across the GLM, Qwen and DeepSeek profiles and their recipe changes, the head rank's inference series moved to each new `model`, `runtime` and `recipe` label set with no configuration change. From enrollment on 2026-10-04 to 2026-10-25 the store holds 19 such label sets.
- **Sustained load:** on 2026-10-08 both GPUs averaged 95% utilization over an hour. The GPU exporter's target never went down that day, and its collections took a median of 50 ms (at most 0.68 s on `spark-1`). This shows collection kept up under load; it does not measure whether collection cost any throughput.
- **PVE reboot:** after the 2026-10-25 package updates (kernel `7.0.14-14-pve` to `7.0.14-25-pve`), pve-sbx-1 rebooted, and pve-sbx-2 powered off instead of rebooting and was powered back on. On both, Docker came back with the same settings: `ip_forward` 0, no `br_netfilter`, forward policy `ACCEPT`, an empty nft ruleset, only the `host` and `none` networks, and an unchanged daemon configuration. Guests reached their gateways, and all four collectors (`pve-node`, `pve-smartctl`, `pve-vmagent`, `pve-deployer`) resumed. The evidence is in [#721](https://github.com/kelchm/home-lab/issues/721).

## Identity

| Label | Meaning | Set by |
|---|---|---|
| `instance` | Physical node, such as `spark-1` | The host's ingestion token; node-name relabeling for cluster exporters |
| `platform` | Machine type: `k8s`, `pve`, `spark`, `synology`, `pi`, `unifi`, `glkvm`, `starlink` or `tmobile` | The host's ingestion token; the node-exporter and smartctl-exporter scrapes for the cluster nodes; the [UniFi](#unifi), [GLKVM](#glkvm) and [WAN device](#wan-devices) scrapes |
| `job` | Collector or source, such as `spark-node`, `pve-vmagent` or `pve-backup` | Scrape configuration for scraped series; the webhook URL for PVE backup metrics |
| `model`, `runtime`, `recipe` | What a Spark is serving | SparkRun's container labels |

Each host has one `VMUser` in [metrics-ingest](../../kubernetes/apps/observability/metrics-ingest/app/vmusers.yaml). VMAuth adds that user's `instance` and `platform` to every sample and a host cannot override them, so the Compose and scrape files are identical on every host of a type. Host series carry no `cluster` label except for PVE backup completion metrics, which carry `cluster=pve-sbx`, and the Kubernetes stack's default rule groups are restricted to `cluster="k8s-prod"`. Shared host and drive rule groups deliberately have no cluster restriction so they also match host-pushed series. The k8s-prod nodes follow the same convention on their node-exporter and smartctl-exporter series, where `instance` is the Kubernetes node name and `platform` is `k8s`. The **Hosts** dashboard lists every machine and filters by platform and node, and the stock **Node Exporter Full** dashboard gives the detail for one. **Proxmox via Prometheus** covers the PVE cluster and its guests, **Sparks / GPU and host health** the Sparks themselves, **Hosts / Drive health** SMART and DSM disk health across platforms, **Synology / Storage and hardware health** the NAS's drives, pools and UPS, and **AI / Inference serving** filters by model, recipe and serving host.

## Enroll a Spark

Run from a checkout of `main` on a workstation that holds the cluster age key. Do one node, verify it, then do the next. Substitute its address (`10.32.21.31` or `10.32.21.32`) and token key (`SPARK_1_TOKEN`, `SPARK_2_TOKEN`). On `spark-1`, prepare the [sparkDash host state](../../sparks/monitoring/README.md#sparkdash) before starting the deployer.

```sh
sops decrypt --extract '["stringData"]["SPARK_1_TOKEN"]' kubernetes/apps/observability/metrics-ingest/app/tokens.sops.yaml \
  | ssh kelchm@10.32.21.31 'sudo install -d -m 0755 /etc/monitoring && sudo install -m 0600 /dev/stdin /etc/monitoring/token'
```

Then use the matching [manual bootstrap/migration apply](doco-cd-self-update.md#bootstrap-or-migrate). Both Sparks use one deployer definition; supply the correct `SPARK_HOST` on a manual apply. Git self targets supply it for native updates, and a missing identity fails interpolation.

doco-cd deploys the `monitoring` and `monitoring-gpu` projects within three minutes. The GPU exporter needs NVIDIA CDI devices in Docker (`nvidia-ctk cdi list` shows `nvidia.com/gpu=all`); if it cannot start, the other collectors are unaffected and `NodeCollectorDown` names it. The inference container is not touched. Whichever Spark runs SparkRun's head rank scrapes the serving endpoint every second and labels the series with the model, runtime and recipe SparkRun reports; see [Spark monitoring](../../sparks/monitoring/README.md#inference). Stop the workstation pilot once those samples arrive so the two do not overlap.

`spark-1`'s second poll deploys sparkDash and doco-cd; `spark-2`'s second poll deploys doco-cd. Subsequent deployer updates use native self-update; verification and recovery are in the [self-update runbook](doco-cd-self-update.md).

## PVE node

Docker's default networking drops traffic between bridged guests the moment the daemon starts. Write the daemon configuration **before** installing the package, and enroll one node first.

```sh
scp proxmox/platform/doco-cd/daemon.json kelchm@10.32.20.23:/tmp/
ssh kelchm@10.32.20.23 'sudo install -D -m 0644 /tmp/daemon.json /etc/docker/daemon.json && sudo apt-get install --no-install-recommends -y docker.io docker-cli docker-compose'
```

Before going further, confirm on the node that guests still pass traffic, `cat /proc/sys/net/ipv4/ip_forward`, `sudo iptables -S FORWARD` and `sudo nft list ruleset` are unchanged from before the install, `lsmod` shows no `br_netfilter`, and `sudo docker network ls` shows no `bridge` network. If any check fails, `sudo apt-get purge docker.io` and stop. Then install the token as for a Spark, using the node's `PVE_SBX_N_TOKEN` key and PVE address. Apply the PVE deployer with the [bootstrap/migration procedure](doco-cd-self-update.md#bootstrap-or-migrate).

The deployer runs without Docker's default AppArmor profile: on PVE's kernel that profile denies Unix sockets, including the Docker socket. Containers on a PVE node use host networking only. doco-cd has no bind-address setting, so its health and metrics ports (8080, 9120) listen on every interface of the node; its webhook and API stay disabled without a secret.

CPU fan speed and chassis temperature come from the BIOS through HP's WMI sensor interface. PVE's kernel ships the `hp-wmi-sensors` driver but does not autoload it, so load it at boot; node-exporter then reports `node_hwmon_fan_rpm` and two more `node_hwmon_temp_celsius` sensors (CPU and chassis) on the chip named `hp_wmi_sensors`. All three nodes have it since 2026-10-24.

```sh
ssh kelchm@NODE 'echo hp-wmi-sensors | sudo tee /etc/modules-load.d/hp-wmi-sensors.conf && sudo modprobe hp-wmi-sensors'
```

The Talos nodes' BIOS offers the same interface, but Talos builds its kernel without `CONFIG_SENSORS_HP_WMI`.

## NAS

Athena runs the [NAS monitoring](../../synology/monitoring/README.md) project from its existing doco-cd, so enrollment is the host state the project expects, then a merge. Its instance is `nas`, from the address registry, and its jobs are `synology-node`, `synology-snmp` and `synology-vmagent`.

```sh
ssh kelchm@10.32.20.5 'sudo install -d -m 0755 /volume1/docker/monitoring /volume1/docker/monitoring/config && sudo install -d -m 0700 /volume1/docker/monitoring/queue'
sops decrypt --extract '["stringData"]["NAS_TOKEN"]' kubernetes/apps/observability/metrics-ingest/app/tokens.sops.yaml \
  | ssh kelchm@10.32.20.5 'sudo install -m 0600 /dev/stdin /volume1/docker/monitoring/token'
```

Once the `NAS_TOKEN` `VMUser` is live, merge the project's entry in [`synology/.doco-cd.yml`](../../synology/.doco-cd.yml); doco-cd deploys it within three minutes. The `publish` service exits after copying the config, which is expected. DSM needs no setting: SNMP comes from the `snmpd` DSM already runs on loopback.

Rotate the token as for any host, writing `/volume1/docker/monitoring/token`. To remove the NAS, delete its target from `synology/.doco-cd.yml`, run `docker compose -p monitoring down` on Athena, and delete its `VMUser`.

## Pi

A NixOS Pi enrolls through the opt-in metrics profile in [rpi-nixos](https://github.com/kelchm/rpi-nixos), which runs the stock node-exporter and vmagent services on loopback with jobs `pi-node` and `pi-vmagent`. A UPS monitor (`ups-compute-rack`, `ups-office`) adds `pi-nut`, the stock NUT exporter reading the local upsd, with one target per UPS labelled `ups`. There is no deployer: a Pi changes when its NixOS configuration is switched.

The token lives in the host's rpi-nixos SOPS payload as `metrics-ingest-token`, and the same value sits here under the host's key, such as `STREAMER_BASEMENT_WORKBENCH_TOKEN`. Copy it from rpi-nixos rather than generating one here, and rotate both together:

```sh
sops decrypt --extract '["metrics-ingest-token"]' <rpi-nixos>/secrets/<host>.yaml | tr -d '\n' | jq -R . \
  | sops set --value-stdin kubernetes/apps/observability/metrics-ingest/app/tokens.sops.yaml '["stringData"]["<HOST>_TOKEN"]'
```

Then add the host's `VMUser` with `platform=pi`. The vendor kernel ships with pressure stall information off; the profile turns it on at boot, so `NodeMemoryStalled` covers a Pi only after its first reboot with the profile. To remove a Pi, drop the profile from its configuration and delete its `VMUser`.

## GLKVM

The recovery console runs no collector of its own. The cluster's vmagent scrapes kvmd's native export directly, as for the Proxmox API, with `instance="glkvm"` and `platform="glkvm"`. The appliance changes, metrics, alerts and acceptance checks are in the [GLKVM README](../../devices/glkvm/README.md#monitoring).

## Verify a host

```promql
time() - tlast_over_time(up{instance="spark-1"}[1h])
count by (job) (up{instance="spark-1"} == 1)
```

The first is a few seconds on a healthy host. The second lists `spark-node`, `spark-gpu`, `spark-smartctl`, `spark-vmagent` and `spark-deployer`, plus `spark-inference` on the node serving the head rank. On the NAS it lists `synology-node`, `synology-snmp` and `synology-vmagent`, and on a Pi `pi-node` and `pi-vmagent`, plus `pi-nut` on a UPS monitor. On the host, `curl -s 127.0.0.1:8429/targets` shows each scrape target. `docker logs doco-cd` shows deployments on a Compose host, and `journalctl -u vmagent -u prometheus-node-exporter -u prometheus-nut-exporter` shows the collectors' logs on a Pi.

Check memory with `docker stats --no-stream`, which matters on a Spark where a loaded model leaves little free. Every container keeps at most 30 MB of logs and has a memory limit: 256 MiB each for doco-cd and vmagent, 128 MiB each for node-exporter, smartctl-exporter and the GPU exporter, so 896 MiB at most on a Spark and 768 MiB on a PVE node. These are configured limits; measure actual SMART collector use after deployment. sparkDash adds a 512 MiB limit on `spark-1`. Measured on 2026-10-04: a Spark used 90 to 110 MiB in total (doco-cd 35 to 44, vmagent 23 to 46, GPU exporter 10 to 17, node-exporter 10), leaving its available memory within 0.1 GiB of where it started; a PVE node used about 55 MiB.

## Drive health

| Machines | Installed SMART-capable disks | Collection |
|---|---|---|
| `k8s-prod-1`–`k8s-prod-3` | One WD_BLACK SN770 1 TB NVMe each, controller `nvme0`, namespace `nvme0n1` | Chart DaemonSet, v0.15.0 |
| `pve-sbx-1`–`pve-sbx-3` | One WD_BLACK SN770 1 TB NVMe each, controller `nvme0`, namespace `nvme0n1` | Compose, v0.15.0, `pve-smartctl` |
| `spark-1`, `spark-2` | One Samsung MZALC4T0HBL1-00B07 4 TB NVMe each, controller `nvme0`, namespace `nvme0n1` | Compose, v0.15.0, `spark-smartctl` |
| Athena (`instance="nas"`) | Eight Seagate ST14000NM001G-2KJ103 14 TB HDDs, `/dev/sata1`–`/dev/sata8` | DSM SNMP, `synology-snmp`, device `Disk 1`–`Disk 8` |

The hardware inventory includes the four Pis (`streamer-basement-workbench`, `ups-compute-rack`, `ups-office`, `adsb-office`): all have only SD storage (`mmcblk0`), with no NVMe disk. SD cards expose no SMART, so the Pis are excluded. Adding a disk requires checking its actual device/controller name and extending the explicit Compose device list. A new NVMe controller reported by node-exporter without health data raises `DriveMetricsMissing`.

### Device permissions

PVE and Spark exporters run as root with only `CAP_SYS_ADMIN` added after dropping all capabilities, and map only `/dev/nvme0` with device permission `r`. Linux's NVMe admin-log ioctl requires `CAP_SYS_ADMIN` even with a read-only open. This is narrower than `privileged`, but it is **not read-only authority over the drive**: the capability and controller permit admin ioctls, potentially including destructive commands if the container is compromised. The normal exporter invokes fixed read commands and exposes metrics over loopback only. The filesystem is read-only, `no-new-privileges` is set, and no host root, Docker socket, host PID namespace or other disk is mounted. Docker's default seccomp and AppArmor profiles remain enabled; `CAP_SYS_ADMIN` broadens Docker's default seccomp allowances, so these controls do not make the capability harmless. See [Linux's NVMe ioctl checks](https://github.com/gregkh/linux/blob/v7.0.14/drivers/nvme/host/ioctl.c) and [Docker seccomp](https://docs.docker.com/engine/security/seccomp/).

On PVE, host networking is mandatory. Only doco-cd disables AppArmor for its Unix socket problem; that exception is not applied to smartctl-exporter. Runtime device access must be checked on both PVE and Spark kernels. If a read fails, inspect the exporter and AppArmor logs before changing permissions; do not automatically enable privileged mode or disable AppArmor.

The Kubernetes chart explicitly collects `/dev/nvme0;nvme` on each node. Longhorn iSCSI volumes identify as `IET VIRTUAL-DISK` and expose synthetic SCSI health, so automatic device scanning would inflate the physical-disk inventory. Historical cluster missing-health checks likewise include only NVMe controllers. The stock chart hardcodes root, privileged mode and a host `/dev` mount; it exposes no values to narrow that security context. The DaemonSet retains those chart requirements and its 128 MiB memory limit. PVE/Spark Compose does not inherit them. All three platforms pin the same multi-architecture v0.15.0 image digest.

### Athena evidence and limits

The live SNMP series contain status, health, temperature, bad-sector counts and 18 SMART attributes per disk, including numeric IDs 5 (reallocated), 197 (pending) and 198 (offline uncorrectable). All eight disks reported healthy, those three raw attributes were zero, and temperatures were 32–37 °C during verification. Disks 7 and 8 were uninitialized but still reported SMART attributes. Remaining life is `-1` on these HDDs, so it is unavailable, not a useful endurance signal. The hour counter changed on all eight disks over 24 hours.

This supports disk-failure monitoring without another container holding raw disk authority. Keep DSM's collector, its dashboard, RAID/hardware rules and health-warning rule. Shared recording rules map its health, temperature and device identity into the drive dashboard, and sector rules evaluate every installed disk regardless of pool membership. Attribute joins use numeric IDs rather than vendor-specific names.

SNMP is not a lossless raw SMART archive. One packed command-timeout attribute was clamped to `2147483647` while native DSM `smartctl -A -d sat -n standby /dev/sata7` returned `4295032833`; that packed value is not a literal error count. Do not alert on arbitrary raw attributes or turn unsupported remaining life into zero. DSM extended-test results and detailed diagnostics remain in DSM or native `smartctl` with `-d sat`. Neither collector supplies a comprehensive self-test history to this dashboard.

### Rules and freshness

The [drive recording rules](../../kubernetes/apps/observability/smartctl-exporter/app/rules.yaml) expose `drive:health_status` (0 failed, 1 healthy, 2 DSM warning), `drive:temperature_celsius`, `drive:info`, HDD sector counts and missing-health indicators, keyed by `platform`, `instance`, `device`. DSM `diskHealthStatus=2` remains a warning, even if `diskStatus` is absent. `diskStatus=3` (uninitialized) alone is not failure; `diskHealthStatus>=3` or `diskStatus>=4` is failure. Healthy status requires both metrics; partial data without a warning or failure raises missing health. Protocol-specific panels show no data where a measurement is unsupported. The dashboard keeps its existing UID.

The [chart rule overrides](../../kubernetes/apps/observability/smartctl-exporter/app/helmrelease.yaml) alert on shared failed health, NVMe media errors, critical warnings, spare below the device threshold and endurance used ≥80%. They name `instance` and `device`, with no pod, namespace or node-label assumptions. Chart temperature and link-speed rules are disabled: existing thermal rules remain, and link speed is not a reliable disk-health alarm. Error-log entry counts also do not alert because unsupported commands can increment them. Additional rules cover HDD sectors, missing health and non-live NVMe controllers; `NodeCollectorDown` includes the host SMART scrape jobs. These groups must remain outside the Kubernetes stack's `cluster="k8s-prod"` filtering.

Upstream v0.15.0 can keep serving the last successful disk sample after a subsequent smartctl read fails. A local stock-image fixture test reproduced HTTP 200 and cached healthy metrics after an open failure; an initial failure instead emitted no disk health. `up=1`, `smartctl_devices`, exit-status metrics and sample timestamps therefore do not prove a fresh disk read. Missing-health and controller-state alerts catch some failures but cannot detect every cached reading while a controller remains live. The exporter has no last-success timestamp or cache-expiry setting; retain this limitation until an upstream release provides one. See [upstream cache handling](https://github.com/prometheus-community/smartctl_exporter/blob/v0.15.0/readjson.go). Do not claim comprehensive prediction of disk failure.

### Verify drive health

After merge, check all five PVE/Spark hosts. Their exporters listen at `127.0.0.1:9633`; vmagent scrapes each every 60 seconds with a 30-second timeout. Read `docker logs monitoring-smartctl-exporter-1`, `curl -fsS 127.0.0.1:9633/metrics`, `docker stats --no-stream`, and the kernel's AppArmor denial logs. Confirm fresh successful smartctl reads, one NVMe controller, no OOM/restart loop, root with only SYS_ADMIN, a read-only controller mapping, default AppArmor and no privileged flag (`docker inspect monitoring-smartctl-exporter-1`). No host runtime assertion is established by Compose rendering alone.

Check access independently of the exporter's cache by running its read command inside the container on each PVE/Spark host. Inspect `smartctl.exit_status` and `smartctl.messages` in the JSON: an open/command failure is a collection failure; health-related exit bits require disk investigation. Compare its device identity and health with the metrics endpoint. This performs no self-test or device configuration change.

```sh
sudo docker exec monitoring-smartctl-exporter-1 smartctl \
  --json --info --health --attributes --capabilities --tolerance=verypermissive \
  --nocheck=standby --format=brief --log=error --device=nvme /dev/nvme0
```

```promql
# Five healthy host targets: three PVE, two Sparks.
up{job=~"(pve|spark)-smartctl"}
# Eight NVMe disks: k8s=3, pve=3, spark=2; health=1 on each.
count by (platform) (smartctl_device_smart_status)
smartctl_device_smart_status{platform=~"k8s|pve|spark"}
# No missing canonical labels, no IP-valued instance, no missing controller health.
smartctl_device_smart_status{platform=""} or smartctl_device_smart_status{instance=~".*:9633"}
node_nvme_info{platform=~"k8s|pve|spark"} unless on (instance, platform, device) smartctl_device_smart_status
# Sixteen total disks: eight NVMe plus eight NAS HDDs, none missing health.
count by (platform) (drive:health_status)
drive:health_missing == 1
# Eight zero-valued series per HDD attribute; health must include Disk 7 and Disk 8.
drive:ata_reallocated_sectors
drive:ata_pending_sectors
drive:ata_uncorrectable_sectors
# Healthy health rules return no matches. Missing health may persist for 15m during rollout.
ALERTS{alertname=~"Drive.*|SmartCTL.*", alertstate="firing"}
```

Before deployment, the live label-transformation query confirmed all three cluster SMART series can map to their node names with `platform="k8s"`. Healthy values matched none of the failure/media/critical/spare/wear or HDD sector alert predicates. The missing-health expression correctly identified the five PVE/Spark controllers without SMART data. Render and fixture tests cover host series without a `cluster` label; the post-merge queries above establish actual collection and reconciliation.

## Pending updates

Each PVE node and Spark runs `apt-metrics` in its monitoring stack. Every 15 minutes it runs `apt-get -s dist-upgrade`, a simulation, against the host's package state, which is mounted read-only. It writes three values into node-exporter's textfile directory, so they arrive as `pve-node` or `spark-node` series:

| Metric | Meaning |
|---|---|
| `apt_upgrades_pending` | Packages with an upgrade available |
| `apt_security_upgrades_pending` | Those whose new version comes from a `-security` archive |
| `apt_package_lists_refreshed_timestamp_seconds` | When the host last refreshed its package lists: the time of `/var/lib/apt/lists/partial`, which apt touches on every refresh |

The container never refreshes the lists itself; the host does. On PVE, `pve-daily-update` refreshes them once a day in the early morning. NVIDIA's apt configuration on the Sparks turns the daily refresh off (`APT::Periodic::Update-Package-Lists "0"` in `99update-notifier-nvidia`). The Spark stack's one-shot `apt-list-refresh` service copies [`zz-monitoring-apt-refresh`](../../sparks/monitoring/apt/zz-monitoring-apt-refresh) into `/etc/apt/apt.conf.d` to turn it back on. That file only refreshes the lists. Unattended upgrades stay off, and the `unattended-upgrades` package isn't installed. If the service is retired, delete `/etc/apt/apt.conf.d/zz-monitoring-apt-refresh` on each Spark by hand.

`NodeSecurityUpdatesPending` warns once security upgrades have been waiting for three days. `NodePackageListsStale` warns when the lists haven't been refreshed for two days, which also covers `apt-metrics` stopping or failing once it has written the file, because the file keeps its last refresh time. A host whose `apt-metrics` has never written the file has no series and raises nothing, so check for the three series after deploying to a new host.

Limits:

- Security is judged by archive. Fixes that Proxmox or NVIDIA publish in their ordinary repositories count as ordinary upgrades.
- Reboot-required isn't collected.
- `apt-metrics` uses the stock image of the host's release (Debian 13 on PVE, Ubuntu 24.04 on the Sparks). Change it when a host moves to a new major release.
- The NixOS Pis aren't covered.

## Change, pause, rotate, remove

- **Change:** merge to `main`. Each host applies it on its next poll; only the service whose files changed is recreated, and the queue survives. A commit whose images cannot be pulled leaves the running containers in place. A service that starts and then keeps failing makes doco-cd recreate its whole project on every poll until it is fixed. Either case raises `NodeDeployFailing`. `scripts/ci/validate-host-monitoring.sh` renders every deployment and has the pinned vmagent parse its scrape files; run it before merging a change.
- **Pause:** `docker stop doco-cd` on the host. Collection continues.
- **Rotate a token:** replace the value with `sops`, merge, then rewrite `/etc/monitoring/token` as above. `vmagent` rereads the file; samples queue in between.
- **Remove a host:** on the host run `docker compose -p doco-cd down` and `docker compose -p monitoring down -v`, plus `docker compose -p monitoring-gpu down` on a Spark and `docker compose -p sparkdash down` on `spark-1`, then delete its `VMUser`. `-p` selects the project by name, so the commands work from any directory. `NodeMetricsMissing` fires for a host that stops reporting until it has been silent for seven days; silence it for a planned removal.
- **Add a host:** add a key to the token Secret and a `VMUser`, then enroll it.

## Proxmox API

Create a read-only user and token on any node, store the token value as `PVE_TOKEN_VALUE` in a SOPS Secret named `pve-exporter-credentials` beside the [exporter](../../kubernetes/apps/observability/pve-exporter/app/), list it in that `kustomization.yaml`, and remove `suspend: true` from the Flux Kustomization.

```sh
sudo pveum user add metrics@pve --comment "Read-only metrics"
sudo pveum acl modify / --users metrics@pve --roles PVEAuditor
sudo pveum user token add metrics@pve exporter --privsep 0
```

The exporter reports quorum, node and guest state, storage use and which guests no backup job covers. It has no metric for whether a backup ran or succeeded.

## UniFi

[Unpoller](../../kubernetes/apps/observability/unpoller/app/) reads the UniFi console at `unifi.home.kelch.io` with a local account and exports gateway, WAN, switch, port, PoE and access-point metrics. Its series carry `job="unifi-api"`, `instance="unifi"` and `platform="unifi"`; the device is in the `name` label. It refreshes from the console once a minute, and everything it exports is kept: client series (signal, retries, roaming and traffic per device), DHCP pools and leases, and the topology. Client series are labelled with the client's current access point, channel and IP, so a roam or a new lease starts new series. Ports that are down or disabled keep their series (`dead_ports`), and the neighbouring access points the console can see are exported as `unpoller_rogueap_*` (`save_rogue`). DPI, traffic per country, events, alarms, IDS and syslog collection stay off. Since v5.7.0 Unpoller exports per-WAN health from the gateway: `unpoller_device_wan_status` (`last_wan_status`, 1 when online), per-port `unpoller_device_wan_latency_seconds` and `unpoller_device_wan_availability_percent` (the gateway's checks against the first target in the WAN's health-check settings, `ping.ui.com` by default, over about the last 50 seconds, with availability rounded down to a whole percent), `unpoller_device_wan_uptime_stats_uptime_seconds` and `_downtime_seconds`, and per-monitor `unpoller_device_wan_monitor_*`. `unpoller_wan_interface_state` stayed `ACTIVE` through staged outages with the WAN's link up and down, so outage rules read `unpoller_device_wan_status`; monitor availability is not a reachability signal.

Series count and churn, measured over the first full day of this collection (2026-10-18 16:30 to 2026-10-19 16:30 UTC), as series present at the end of the day and distinct series seen during it:

| Family | Present | Distinct in the day |
|---|---:|---:|
| Whole `unifi-api` job | 5,720 | 8,034 |
| Clients (`unpoller_client_*`, 97 clients) | 1,479 | 3,135 |
| DHCP (`unpoller_dhcp_*`) | 167 | 171 |
| Neighbouring access points (`unpoller_rogueap_*`) | 414 | 801 |
| Topology (`unpoller_topology_*`) | 105 | 129 |

Of 63 wireless clients that day, 25 were seen on more than one access point, 26 on more than one channel and 14 with more than one IP. Those are clients whose series turned over, not a count of roams. On 2026-10-25 the job had 5,811 series present and 8,804 distinct over the previous day.

The account is `unpoller-sa`, a local admin with the View Only role, which UniFi OS grants as read-only on Network, Protect and user management; a settings write returns `403 api.err.NoPermission`. Its password is in 1Password at `op://Project - home-lab/unpoller-sa/password`. Write it into the SOPS Secret from a checkout:

```sh
op read --no-newline "op://Project - home-lab/unpoller-sa/password" \
  | kubectl create secret generic unpoller-credentials --from-file=password=/dev/stdin --dry-run=client -o yaml \
  | sops encrypt --filename-override kubernetes/apps/observability/unpoller/app/credentials.sops.yaml --input-type yaml --output-type yaml /dev/stdin \
  > kubernetes/apps/observability/unpoller/app/credentials.sops.yaml
```

To rotate, change the password in UniFi OS and 1Password, rerun the command and merge; Reloader restarts the pod. To remove, delete the Flux Kustomization from the observability `kustomization.yaml` and the account from UniFi OS.

## WAN devices

Each WAN's provider device has a local status API that needs no credential. Both APIs are undocumented, so a firmware update can break the exporter that reads it; each source has its own alert for that.

| WAN | Device | API | Exporter | `job` | `instance` / `platform` |
|---|---|---|---|---|---|
| WAN1, T-Mobile | Arcadyan KVD21 5G gateway | `GET http://192.168.12.1/TMI/v1/gateway?get=all` | [json-exporter](../../kubernetes/apps/observability/json-exporter/app/), `tmobile` module | `tmobile-api` | `tmobile-gateway` / `tmobile` |
| WAN2, Starlink | Starlink dish | gRPC at `192.168.100.1:9200` | [starlink-exporter](../../kubernetes/apps/observability/starlink-exporter/app/) | `starlink-api` | `starlink-dish` / `starlink` |

- **Starlink:** vmagent scrapes every 15 seconds and the exporter reads the dish on each scrape. `starlink_dish_up` reports whether the dish answered. Ping drop and latency to the point of presence are the dish's values for the current second, so rules average them. The scrape drops the location and GPS series. `starlink_dish_last_24h_obstructed_seconds` passes through the dish's `time_obstructed` field, which on 2026-10-18 read 3.0e-8 beside a 15-day `valid_s` window, so it is not seconds in a day; no rule uses it.
- **T-Mobile:** vmagent asks json-exporter to probe the gateway every 30 seconds. The `tmobile` module in the HelmRelease exports RSRP, RSRQ, RSSI, SINR, bars, cell and gNB IDs for the serving 5G cell, the band list, registration state (`tmobile_registration_info{registration}`), roaming, uptime and firmware version. json-exporter answers 503 when the gateway does not respond, so `up` is 0. The response has no LTE section while the gateway is on 5G, and the module reads none.

The KVD21 is on `192.168.12.0/24`, the subnet of WAN1's own address, so the gateway routes to it directly. The dish's address is on no connected subnet: the gateway sends traffic for `192.168.100.1` out whichever WAN load balancing picks unless a rule pins it. The UniFi traffic route **Starlink Management** pins `192.168.100.0/24` to WAN2 for all clients; it matches only that destination, so load balancing of other traffic is unchanged. Without it, only the clients whose flows happen to leave by WAN2 reach the dish. The `192.168.100.1/32` static route via WAN2 alone does not override load balancing.

To check a source by hand: `grpcurl -plaintext -d '{"get_status":{}}' 192.168.100.1:9200 SpaceX.API.Device.Device/Handle` for the dish, and `curl -s 'http://192.168.12.1/TMI/v1/gateway?get=all'` for the gateway. If a firmware update changes the gateway's JSON, edit the `tmobile` module to match.

## Alerts

Host rules are in [metrics-ingest](../../kubernetes/apps/observability/metrics-ingest/app/alerts.yaml) and Proxmox API rules beside the [exporter](../../kubernetes/apps/observability/pve-exporter/app/alerts.yaml). UniFi rules are beside [Unpoller](../../kubernetes/apps/observability/unpoller/app/alerts.yaml): stale UniFi data, a device the console counts as disconnected, a WAN the gateway's health check reports offline (critical once it has been offline for an hour; losing both WANs is caught by the Watchdog heartbeat instead), a WAN port answering under 90% of the gateway's pings for 15 minutes, a WAN port whose 15-minute average to them stays above 75 ms for 45 minutes (T-Mobile's latency rises most mornings around 10:00 UTC for up to half an hour, which this outlasts), a WAN taken out of load balancing, a device uplink below its expected speed, port errors, a port anomaly the console reports, a DHCP pool over 80% used, a device over 70 °C, 90% CPU or 90% memory for 15 minutes, and a device firmware or UniFi Network update that has been waiting for every sample of the last week. The uplink rule lists each device's expected speed, with 1 Gb/s for any other device except the gateway, whose uplink is a WAN, so renaming a device or changing a link's speed means editing it. WAN device rules are beside each exporter: for Starlink, metrics unavailable, ping drop over 10% sustained for 15 minutes, and obstruction for over 5% of an hour; for T-Mobile, metrics unavailable (including a response the module no longer matches), which is info and does not notify. Lost registration shows as `UniFiWANDown` for WAN1. Signal strength has no alert: in the first week (2026-10-18 to 2026-10-25), RSRP's median was −87 dBm and SINR's 26 dB, and its two sustained drops below −100 dBm, one with SINR near 2 dB for about an hour, came with no lost pings or added latency on WAN1. Home colours signal below −100 dBm and quality below 10 dB, and lists a 15-minute average below −100 dBm as worth a look. A host is covered from its first sample; there is no enrollment list to maintain. The NAS also has disk, RAID, hardware, UPS and DSM-update rules that read DSM's own status through SNMP, the update rule once DSM has offered one for a whole week; an uninitialized disk does not alert solely for that status, but its health and SMART sector attributes still alert. A Pi alerts when its firmware has reported undervoltage for 15 minutes. A UPS a Pi monitors alerts when it is on battery, when it is on battery with under five minutes of runtime left, when it reports a low battery, and when it has sent no status for five minutes; a failing NUT scrape counts as no status, so `NodeCollectorDown` leaves `pi-nut` out. They route like every other alert, described in the [alerting runbook](alerting.md).

Network path rules cover the links the lab depends on. [Host link rules](../../kubernetes/apps/observability/metrics-ingest/app/network-alerts.yaml) watch the wired physical NICs of the PVE nodes, Sparks, NAS and Pis: three or more flaps in an hour (a down/up pair is one flap; a host booted under two hours ago or first seen under an hour ago is skipped), or no carrier for five minutes on a link that was up in the last week, so a cable removed on purpose stops alerting after seven days. [Cilium's BGP rules](../../kubernetes/apps/kube-system/cilium/app/alerts.yaml) warn when a k8s-prod node's session to the gateway is down for five minutes and page as critical when none is up, because that takes every load-balancer IP off the LAN. A UniFi port alerts on errors only above 60 in an hour and above two per million frames; Core Aggregation port 25's receive-error trickle ([#853](https://github.com/kelchm/home-lab/issues/853)) peaks at 16 an hour and under one per million, and its diagnosis may let the threshold tighten. Unpoller exports no per-port link state: a down copper port reports a speed of 0, but an SFP cage reports its configured speed even when empty, and a DAC reports a receive power of 0 even with link up. Switch-to-switch links are covered by the uplink-speed and disconnected-device rules and host links by the host rules; other switch ports have no link-down alert. The rules' promtool fixtures run in CI through `scripts/ci/validate-network-alerts.sh`.

Backup failure and staleness rules for PVE are installed: PVE's notification webhook pushes one `pve_backup_completed_timestamp_seconds` sample per finished backup run through the node's vmagent, as `job="pve-backup"` with `cluster="pve-sbx"`, the one host-pushed series that carries a cluster label. Unlike the host rules, `PVEBackupStale` lists the nodes it expects, so adding or removing a PVE node means editing it. See [backup completion metrics](../../proxmox/monitoring/backup-metrics/README.md).

## Service probes

`ups-compute-rack` (`10.32.20.111`, VLAN 20) runs the stock nixpkgs blackbox exporter on `127.0.0.1:9115` through its host-level [rpi-nixos add-on](https://github.com/kelchm/rpi-nixos/blob/main/hosts/ups-compute-rack/probes.nix). Its vmagent scrapes nine destinations as `pi-blackbox` every 60 seconds, with a five-second probe timeout and ten-second scrape timeout. `pi-blackbox-exporter` scrapes the exporter's own metrics every 30 seconds. The other UPS Pi and the streamer run no probes. Modules use only blackbox's built-in HTTP, TCP, DNS and ICMP probers; HTTP status, response body and DNS answer checks are ordinary exporter configuration, with no scripts or credentials.

The targets come from the [address registry and LB pools](../architecture.md#lb-pool-allocation) and the live Traefik HTTPRoutes. Kanidm and Jellyfin use `gateway-services`; Grafana and Homepage use `gateway-admin`; Echo uses `gateway-public` behind the Cloudflare tunnel. Gateway TCP probes check the routed VIP listeners independently of DNS and application authentication.

| Probe | Target | Module | Expected result | Interval |
|---|---|---|---|---|
| Admin gateway | `10.32.130.1:443` | `tcp_connect` | TCP connection accepted | 60s |
| Services gateway | `10.32.140.1:443` | `tcp_connect` | TCP connection accepted | 60s |
| Kanidm | `https://auth.home.kelch.io/status` | `http_kanidm_status` | Health endpoint, HTTP 200 with body `true` and valid TLS; Kanidm does not log it | 60s |
| Jellyfin | `https://jellyfin.home.kelch.io/web/` | `http_2xx` | Public web client, HTTP 200 with valid TLS | 60s |
| Grafana | `https://grafana.home.kelch.io/` | `http_redirect` | HTTP 302 with `Location: /login`, valid TLS; do not follow | 60s |
| Homepage | `https://home.kelch.io/` | `http_401` | HTTP 401 from the authentication middleware, valid TLS; do not follow | 60s |
| Cloudflare tunnel | `https://echo.kelch.io/` | `http_2xx` | HTTP 200 through the public edge and tunnel, valid TLS | 60s |
| k8s-gateway | `10.32.130.2:53` | `dns_k8s_gateway` | UDP lookup of `auth.home.kelch.io` A: `NOERROR` and `10.32.140.1` | 60s |
| UniFi gateway | `10.32.1.1` | `icmp` | ICMP echo reply | 60s |

`instance="ups-compute-rack"` and `platform="pi"` come from the ingestion token. `target` preserves the destination, `probe` names the check and `module` identifies its configuration. Do not relabel the destination into `instance`: VMAuth replaces that label. The stock [Blackbox Exporter (HTTP prober)](https://grafana.com/grafana/dashboards/13659-blackbox-exporter-http-prober/) dashboard is provisioned in **Services / Observability**, with VictoriaMetrics as its datasource and `target` as its destination key. It shows the five HTTP probes; use the queries below for all nine. HTTP 302 and 401 are expected for Grafana and Homepage; `probe_success`, not a blanket HTTP-200 check, determines health.

These are unauthenticated reachability checks. Homepage verifies its authentication boundary, not the application behind it; the Grafana redirect does not verify a completed OAuth login. The public probe checks Cloudflare's edge certificate, while internal HTTPS probes check Traefik's certificate. VLAN 20 exercises the routed LB path, not VLAN 10's firewall policy. Firewall rules must allow this Pi to each target, including UDP 53 and ICMP, when restricting VLAN 20. The exporter has a 32 MiB Go soft limit plus `MemoryHigh=48M` and `MemoryMax=64M`, which systemd enforces because rpi-nixos enables the memory cgroup on this Pi. After a deployment, confirm that `memory` is listed in `/sys/fs/cgroup/cgroup.controllers` and that `systemctl show prometheus-blackbox-exporter -p MemoryCurrent -p MemoryMax` reports numbers. An HTTP response body over 1 MB fails its probe.

### Verify and diagnose

```sh
ssh kelchm@10.32.20.111 'curl -fsSG http://127.0.0.1:9115/probe --data-urlencode module=http_401 --data-urlencode target=https://home.kelch.io/'
ssh kelchm@10.32.20.111 'curl -fsS http://127.0.0.1:8429/targets'
ssh kelchm@10.32.20.111 'sudo journalctl -u prometheus-blackbox-exporter -u vmagent -u prometheus-node-exporter -u prometheus-nut-exporter --since "15 minutes ago"'
```

Add `--data-urlencode debug=true` to a manual `/probe` request for the probe log. Use the table's module and target to reproduce any check. Before and after switching this Pi's configuration, confirm `upsc compute-rack@localhost ups.status`, `systemctl show upsd upsmon -p MainPID -p ActiveState`, and the three existing `pi-node`, `pi-vmagent` and `pi-nut` scrape targets remain healthy.

```promql
probe_success{job="pi-blackbox",instance="ups-compute-rack"}
up{job=~"pi-blackbox(-exporter)?",instance="ups-compute-rack"}
time() - tlast_over_time(probe_success{job="pi-blackbox"}[1h])
probe_duration_seconds{job="pi-blackbox"}
(probe_ssl_earliest_cert_expiry{job="pi-blackbox"} - time()) / 3600
```

All nine success values and ten scrape `up` values should be 1, with probe samples roughly one minute apart. The [probe rules](../../kubernetes/apps/observability/metrics-ingest/app/probe-alerts.yaml) use the existing vmalert → VMAlertmanager → Pushover path:

- `ProbeFailed`: a failed probe or probe scrape for three minutes while the exporter scrape is up; critical priority.
- `ProbeSlow`: successful probes with a five-minute average duration above two seconds for ten minutes; warning priority.
- `ProbeCertificateExpiring`: a successful HTTPS probe sees less than 36 hours of certificate validity for an hour; warning priority. The internal Let's Encrypt certificates use the short-lived profile: cert-manager issues them for 160 hours and renews them with about 53 hours left, so a healthy one never drops below that. At 36 hours renewal has been failing for about 17 hours, past cert-manager's retries one, three, seven and fifteen hours after a failure.
- `NodeCollectorDown`: the exporter scrape is down for ten minutes. `pi-blackbox` is excluded from this generic rule, and probe failures require a healthy exporter scrape, so an exporter outage does not also page for every destination. The NUT collector remains owned by its UPS rules.

If the Pi stops sending metrics, `NodeMetricsMissing` covers it. If the cluster or its alerting path fails, the [healthchecks.io Watchdog](alerting.md#independent-dead-man-heartbeat) covers that outage. There is no separate Pushover sender on the Pi.

### Pushover drill

Run this after both probe PRs are merged and the rules are loaded. Start from a healthy baseline: nine successful probes, the exporter scrape up and NUT healthy. Confirm the drill target is closed; this should print `probe_success 0`:

```sh
ssh kelchm@10.32.20.111 'curl -fsSG http://127.0.0.1:9115/probe --data-urlencode module=tcp_connect --data-urlencode target=127.0.0.1:1' | grep '^probe_success '
```

In a local rpi-nixos checkout, change only the `gateway-admin` target in `hosts/ups-compute-rack/probes.nix` from `10.32.130.1:443` to `127.0.0.1:1`, keeping its `probe` and `module` labels, and deploy only `ups-compute-rack` as in rpi-nixos's [day-to-day deploys](https://github.com/kelchm/rpi-nixos#day-to-day-deploys). Do not commit the change. The check fails without touching the real gateway or any other service.

Within about five minutes, `probe_success{probe="gateway-admin"}` reads 0 and `ProbeFailed` fires for `gateway-admin`: three minutes of `for`, plus scrape, evaluation and Alertmanager grouping delays. Confirm that one high-priority notification from **k8s-prod Alerts** reaches Pushover naming the probe and the Pi, that `NodeCollectorDown` stays quiet and that NUT stays healthy. Then restore the original target and deploy again. Confirm nine successful probes, that the alert resolves, and that one quiet-priority resolved notification arrives. Check the [alert delivery counters](alerting.md) for Pushover errors and record the result in [#629](https://github.com/kelchm/home-lab/issues/629). A rule that evaluates correctly is not a completed drill; the notification has to arrive.
