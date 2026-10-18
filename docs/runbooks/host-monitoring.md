# Host monitoring

Metrics from the three PVE nodes, the two DGX Sparks, the NAS, the UniFi network devices and the NixOS Pis. The lab's metrics backend runs in `k8s-prod`; these hosts push to it. Each host runs ordinary exporters and a `vmagent` in Docker Compose, deployed from `main` by a host-local doco-cd as on the [NAS](../../synology/README.md); a [Pi](#pi) runs them as NixOS services instead. `vmagent` scrapes the exporters over loopback and pushes to `https://metrics-ingest.home.kelch.io`, queueing up to 1 GiB on disk while the endpoint is unreachable. Nothing on a host listens beyond loopback except doco-cd on PVE (see [PVE](#pve-node)) and the sparkDash dashboard on `spark-1`, at `10.32.21.31:5555` behind an access token. The Proxmox API and the UniFi console are read separately by in-cluster exporters. As on the NAS, doco-cd holds the host's Docker socket, so whoever can merge to `main` controls every enrolled host.

## State

The PVE nodes and Sparks are enrolled and the Proxmox API exporter is running, since 2026-10-04. The NAS is enrolled since 2026-10-17 ([#839](https://github.com/kelchm/home-lab/issues/839)): its three jobs report, all eight bays and both the storage pool and volume report DSM status, and `diskID` arrives decoded as `Disk N`. UniFi devices are collected since 2026-10-17 ([#843](https://github.com/kelchm/home-lab/issues/843)): `up{job="unifi-api"}` is 1, the ten adopted devices (gateway, five switches, four access points) report with none disconnected, the job holds about 2,470 series after the client-keyed families are dropped (4,350 before), and the pod uses about 20 MiB and 10m CPU.

The deployer definitions enable native self-update. Its verified version, migration status, manual apply, and handover checks are maintained in the [self-update runbook](doco-cd-self-update.md).

Verified on the hosts that day:

- **Ingest:** from both Sparks and a PVE node, the endpoint resolves, presents a valid certificate, and refuses requests with no token, a wrong token, or a query path. Each host's series arrive with its own `instance` and `platform`.
- **Sparks:** the head rank's inference series arrive at exact one-second spacing, labelled from SparkRun's container labels; the worker has no inference target. Neither inference container was restarted. A short inference probe measured 93.0 tokens/s before enrollment and 92.8 after.
- **Replay:** with `spark-2`'s token made invalid for three minutes, vmagent queued 533 KB and dropped nothing; after the token was restored the backend held every sample from that window with its original timestamp.
- **PVE:** on each node, `ip_forward`, `br_netfilter`, the forward policy and the firewall rulesets were identical before and after installing Docker, and a guest on that node kept reaching its gateway.
- **Proxmox API:** quorum, node and guest state, start-on-boot flags, storage use and the two guests outside the backup job match what the PVE API reports directly.

Not yet verified: that Docker comes back with the same settings after a PVE node reboot, and the GPU exporter under sustained inference load.

## Identity

| Label | Meaning | Set by |
|---|---|---|
| `instance` | Physical node, such as `spark-1` | The host's ingestion token |
| `platform` | Machine type: `k8s`, `pve`, `spark`, `synology`, `pi`, `unifi` or `glkvm` | The host's ingestion token; the node-exporter scrape for the cluster nodes; the [UniFi](#unifi) and [GLKVM](#glkvm) scrapes |
| `job` | Collector or source, such as `spark-node`, `pve-vmagent` or `pve-backup` | Scrape configuration for scraped series; the webhook URL for PVE backup metrics |
| `model`, `runtime`, `recipe` | What a Spark is serving | SparkRun's container labels |

Each host has one `VMUser` in [metrics-ingest](../../kubernetes/apps/observability/metrics-ingest/app/vmusers.yaml). VMAuth adds that user's `instance` and `platform` to every sample and a host cannot override them, so the Compose and scrape files are identical on every host of a type. Host series carry no `cluster` label except for PVE backup completion metrics, which carry `cluster=pve-sbx`, and every Kubernetes rule group is restricted to `cluster="k8s-prod"`, so the two never mix. The k8s-prod nodes follow the same convention on their node-exporter series, where `instance` is the Kubernetes node name and `platform` is `k8s`. The **Hosts** dashboard lists every machine and filters by platform and node, and the stock **Node Exporter Full** dashboard gives the detail for one. **Proxmox via Prometheus** covers the PVE cluster and its guests, **Sparks / GPU and host health** the Sparks themselves, **Synology / Storage and hardware health** the NAS's drives, pools and UPS, and **AI / Inference serving** filters by model, recipe and serving host.

## Enroll a Spark

Run from a checkout of `main` on a workstation that holds the cluster age key. Do one node, verify it, then do the next. Substitute its address (`10.32.21.31` or `10.32.21.32`) and token key (`SPARK_1_TOKEN`, `SPARK_2_TOKEN`). On `spark-1`, prepare the [sparkDash host state](../../sparks/monitoring/README.md#sparkdash) before starting the deployer.

```sh
sops decrypt --extract '["stringData"]["SPARK_1_TOKEN"]' kubernetes/apps/observability/metrics-ingest/app/tokens.sops.yaml \
  | ssh kelchm@10.32.21.31 'sudo install -d -m 0755 /etc/monitoring && sudo install -m 0600 /dev/stdin /etc/monitoring/token'
```

Then use the matching [manual bootstrap/migration apply](doco-cd-self-update.md#bootstrap-or-migrate). Both Sparks use one deployer definition; supply the correct `SPARK_HOST` on a manual apply. Git self targets supply it for native updates, and a missing identity fails interpolation.

doco-cd deploys the `monitoring` and `monitoring-gpu` projects within three minutes. The GPU exporter needs NVIDIA CDI devices in Docker (`nvidia-ctk cdi list` shows `nvidia.com/gpu=all`); if it cannot start, the other collectors are unaffected and `HostCollectorDown` names it. The inference container is not touched. Whichever Spark runs SparkRun's head rank scrapes the serving endpoint every second and labels the series with the model, runtime and recipe SparkRun reports; see [Spark monitoring](../../sparks/monitoring/README.md#inference). Stop the workstation pilot once those samples arrive so the two do not overlap.

`spark-1`'s second poll deploys sparkDash and doco-cd; `spark-2`'s second poll deploys doco-cd. Subsequent deployer updates use native self-update; verification and recovery are in the [self-update runbook](doco-cd-self-update.md).

## PVE node

Docker's default networking drops traffic between bridged guests the moment the daemon starts. Write the daemon configuration **before** installing the package, and enroll one node first.

```sh
scp proxmox/platform/doco-cd/daemon.json kelchm@10.32.20.23:/tmp/
ssh kelchm@10.32.20.23 'sudo install -D -m 0644 /tmp/daemon.json /etc/docker/daemon.json && sudo apt-get install --no-install-recommends -y docker.io docker-cli docker-compose'
```

Before going further, confirm on the node that guests still pass traffic, `cat /proc/sys/net/ipv4/ip_forward`, `sudo iptables -S FORWARD` and `sudo nft list ruleset` are unchanged from before the install, `lsmod` shows no `br_netfilter`, and `sudo docker network ls` shows no `bridge` network. If any check fails, `sudo apt-get purge docker.io` and stop. Then install the token as for a Spark, using the node's `PVE_SBX_N_TOKEN` key and PVE address. Apply the PVE deployer with the [bootstrap/migration procedure](doco-cd-self-update.md#bootstrap-or-migrate).

The deployer runs without Docker's default AppArmor profile: on PVE's kernel that profile denies Unix sockets, including the Docker socket. Containers on a PVE node use host networking only. doco-cd has no bind-address setting, so its health and metrics ports (8080, 9120) listen on every interface of the node; its webhook and API stay disabled without a secret.

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

A NixOS Pi enrolls through the opt-in metrics profile in [rpi-nixos](https://github.com/kelchm/rpi-nixos), which runs the stock node-exporter and vmagent services on loopback with jobs `pi-node` and `pi-vmagent`. There is no deployer: a Pi changes when its NixOS configuration is switched. Its queue limit is 512 MiB, the smallest the pinned vmagent supports.

The token lives in the host's rpi-nixos SOPS payload as `metrics-ingest-token`, and the same value sits here under the host's key, such as `STREAMER_BASEMENT_WORKBENCH_TOKEN`. Copy it from rpi-nixos rather than generating one here, and rotate both together:

```sh
sops decrypt --extract '["metrics-ingest-token"]' <rpi-nixos>/secrets/<host>.yaml | tr -d '\n' | jq -R . \
  | sops set --value-stdin kubernetes/apps/observability/metrics-ingest/app/tokens.sops.yaml '["stringData"]["<HOST>_TOKEN"]'
```

Then add the host's `VMUser` with `platform=pi`. The vendor kernel ships with pressure stall information off; the profile turns it on at boot, so `HostMemoryStalled` covers a Pi only after its first reboot with the profile. To remove a Pi, drop the profile from its configuration and delete its `VMUser`.

## GLKVM

The recovery console runs no collector of its own. The cluster's vmagent scrapes kvmd's native export directly, as for the Proxmox API, with `instance="glkvm"` and `platform="glkvm"`. The appliance changes, metrics, alerts and acceptance checks are in the [GLKVM README](../../devices/glkvm/README.md#monitoring).

## Verify a host

```promql
time() - tlast_over_time(up{instance="spark-1"}[1h])
count by (job) (up{instance="spark-1"} == 1)
```

The first is a few seconds on a healthy host. The second lists `spark-node`, `spark-gpu`, `spark-vmagent` and `spark-deployer`, plus `spark-inference` on the node serving the head rank. On the NAS it lists `synology-node`, `synology-snmp` and `synology-vmagent`, and on a Pi `pi-node` and `pi-vmagent`. On the host, `curl -s 127.0.0.1:8429/targets` shows each scrape target and `docker logs doco-cd` shows deployments.

Check memory with `docker stats --no-stream`, which matters on a Spark where a loaded model leaves little free. Every container keeps at most 30 MB of logs and has a memory limit: 256 MiB each for doco-cd and vmagent, 128 MiB each for node-exporter and the GPU exporter, so 768 MiB at most on a Spark and 640 MiB on a PVE node. sparkDash adds a 512 MiB limit on `spark-1`. Measured on 2026-10-04: a Spark used 90 to 110 MiB in total (doco-cd 35 to 44, vmagent 23 to 46, GPU exporter 10 to 17, node-exporter 10), leaving its available memory within 0.1 GiB of where it started; a PVE node used about 55 MiB.

## Change, pause, rotate, remove

- **Change:** merge to `main`. Each host applies it on its next poll; only the service whose files changed is recreated, and the queue survives. A commit whose images cannot be pulled leaves the running containers in place. A service that starts and then keeps failing makes doco-cd recreate its whole project on every poll until it is fixed. Either case raises `HostDeployFailing`. `scripts/ci/validate-host-monitoring.sh` renders every deployment and has the pinned vmagent parse its scrape files; run it before merging a change.
- **Pause:** `docker stop doco-cd` on the host. Collection continues.
- **Rotate a token:** replace the value with `sops`, merge, then rewrite `/etc/monitoring/token` as above. `vmagent` rereads the file; samples queue in between.
- **Remove a host:** on the host run `docker compose -p doco-cd down` and `docker compose -p monitoring down -v`, plus `docker compose -p monitoring-gpu down` on a Spark and `docker compose -p sparkdash down` on `spark-1`, then delete its `VMUser`. `-p` selects the project by name, so the commands work from any directory. `HostMetricsMissing` fires for a host that stops reporting until it has been silent for seven days; silence it for a planned removal.
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

[Unpoller](../../kubernetes/apps/observability/unpoller/app/) reads the UniFi console at `unifi.home.kelch.io` with a local account and exports gateway, WAN, switch, port, PoE and access-point metrics. Its series carry `job="unifi-api"`, `instance="unifi"` and `platform="unifi"`; the device is in the `name` label. It refreshes from the console once a minute, and the scrape drops the series keyed by client: `unpoller_client_*`, `unpoller_dhcp_*` and `unpoller_topology_link_*`. DPI, events, alarms, IDS and syslog collection stay off.

The account is `unpoller-sa`, a local admin with the View Only role, which UniFi OS grants as read-only on Network, Protect and user management; a settings write returns `403 api.err.NoPermission`. Its password is in 1Password at `op://Project - home-lab/unpoller-sa/password`. Write it into the SOPS Secret from a checkout:

```sh
op read --no-newline "op://Project - home-lab/unpoller-sa/password" \
  | kubectl create secret generic unpoller-credentials --from-file=password=/dev/stdin --dry-run=client -o yaml \
  | sops encrypt --filename-override kubernetes/apps/observability/unpoller/app/credentials.sops.yaml --input-type yaml --output-type yaml /dev/stdin \
  > kubernetes/apps/observability/unpoller/app/credentials.sops.yaml
```

To rotate, change the password in UniFi OS and 1Password, rerun the command and merge; Reloader restarts the pod. To remove, delete the Flux Kustomization from the observability `kustomization.yaml` and the account from UniFi OS.

## Alerts

Host rules are in [metrics-ingest](../../kubernetes/apps/observability/metrics-ingest/app/alerts.yaml) and Proxmox API rules beside the [exporter](../../kubernetes/apps/observability/pve-exporter/app/alerts.yaml). UniFi rules are beside [Unpoller](../../kubernetes/apps/observability/unpoller/app/alerts.yaml): stale UniFi data, a device the console counts as disconnected, a WAN that is not active (critical once it has been down for an hour; losing both WANs is caught by the Watchdog heartbeat instead), and a device uplink below its expected speed. That last rule lists each device's expected speed, with 1 Gb/s for any other device except the gateway, whose uplink is a WAN, so renaming a device or changing a link's speed means editing it. A host is covered from its first sample; there is no enrollment list to maintain. The NAS also has disk, RAID, hardware and UPS rules that read DSM's own status through SNMP; a disk DSM reports as not initialized, such as a new disk before it joins a pool, does not alert. They route like every other alert, described in the [alerting runbook](alerting.md).

Backup failure and staleness rules for PVE are installed: PVE's notification webhook pushes one `pve_backup_completed_timestamp_seconds` sample per finished backup run through the node's vmagent, as `job="pve-backup"` with `cluster="pve-sbx"`, the one host-pushed series that carries a cluster label. Unlike the host rules, `PVEBackupStale` lists the nodes it expects, so adding or removing a PVE node means editing it. See [backup completion metrics](../../proxmox/monitoring/backup-metrics/README.md).
