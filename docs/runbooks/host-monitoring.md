# Host monitoring

Metrics from the three PVE nodes and the two DGX Sparks. The lab's metrics backend runs in `k8s-prod`; these hosts push to it. Each host runs ordinary exporters and a `vmagent` in Docker Compose, deployed from `main` by a host-local doco-cd as on the [NAS](../../synology/README.md). `vmagent` scrapes the exporters over loopback and pushes to `https://metrics-ingest.home.kelch.io`, queueing up to 1 GiB on disk while the endpoint is unreachable. Nothing on a host listens beyond loopback except doco-cd on PVE (see [PVE](#pve-node)). The Proxmox API is read separately by an in-cluster exporter. As on the NAS, doco-cd holds the host's Docker socket, so whoever can merge to `main` controls every enrolled host.

## State

No host is enrolled and the Proxmox API exporter is suspended. Merging this configuration creates the ingestion endpoint, the dashboards and the rules, and changes nothing on any host.

Verified in disposable fixtures on 2026-10-03, not on the production hosts: identity labels bound to tokens through the operator's VMAuth, buffering and replay across an endpoint outage, both Compose projects under doco-cd 0.123.0, the PVE enrollment steps below on Debian 13 with bridged guests, and the dashboards and rules against the resulting series. Not yet verified: any production host, the GPU exporter under sustained inference load, and the Proxmox API rules against live exporter output.

## Identity

| Label | Meaning | Set by |
|---|---|---|
| `instance` | Physical node, such as `spark-1` | The host's ingestion token |
| `platform` | Node type: `pve` or `spark` | The host's ingestion token |
| `job` | Collector, such as `spark-node` or `pve-vmagent` | The scrape configuration |
| `model`, `runtime`, `recipe` | What a Spark is serving | SparkRun's container labels |

Each host has one `VMUser` in [metrics-ingest](../../kubernetes/apps/observability/metrics-ingest/app/vmusers.yaml). VMAuth adds that user's `instance` and `platform` to every sample and a host cannot override them, so the Compose and scrape files are identical on every host of a type. Host series carry no `cluster` label, and every Kubernetes rule group is restricted to `cluster="k8s-prod"`, so the two never mix. The **Hosts** dashboard and the stock **Node Exporter Full** dashboard filter by type and node; **Sparks / TensorFold performance** filters by model, recipe and node.

## Enroll a Spark

Run from a workstation that holds the cluster age key. Do one node, verify it, then do the next. Substitute the node name and its key (`SPARK_1_TOKEN`, `SPARK_2_TOKEN`).

```sh
sops decrypt --extract '["stringData"]["SPARK_1_TOKEN"]' kubernetes/apps/observability/metrics-ingest/app/tokens.sops.yaml \
  | ssh kelchm@spark-1 'sudo install -d -m 0755 /etc/monitoring && sudo install -m 0600 /dev/stdin /etc/monitoring/token'
scp sparks/platform/doco-cd/compose.yaml kelchm@spark-1:/tmp/doco-cd.yaml
ssh kelchm@spark-1 'sudo install -D -m 0644 /tmp/doco-cd.yaml /opt/doco-cd/compose.yaml && cd /opt/doco-cd && sudo docker compose up -d'
```

doco-cd deploys the `monitoring` and `monitoring-gpu` projects within three minutes. The GPU exporter needs NVIDIA CDI devices in Docker (`nvidia-ctk cdi list` shows `nvidia.com/gpu=all`); if it cannot start, the other collectors are unaffected and `HostCollectorDown` names it. The inference container is not touched. Whichever Spark runs SparkRun's head rank scrapes the serving endpoint every second and labels the series with the model, runtime and recipe SparkRun reports; see [Spark monitoring](../../sparks/monitoring/README.md#inference). Stop the workstation pilot once those samples arrive so the two do not overlap.

## PVE node

Docker's default networking drops traffic between bridged guests the moment the daemon starts. Write the daemon configuration **before** installing the package, and enroll one node first.

```sh
scp proxmox/platform/doco-cd/daemon.json proxmox/platform/doco-cd/compose.yaml kelchm@pve-sbx-3:/tmp/
ssh kelchm@pve-sbx-3 'sudo install -D -m 0644 /tmp/daemon.json /etc/docker/daemon.json && sudo apt-get install --no-install-recommends -y docker.io docker-cli docker-compose'
```

Before going further, confirm on the node that guests still pass traffic, `sysctl net.ipv4.ip_forward` and `sudo nft list ruleset` are unchanged from before the install, and `docker network ls` shows no `bridge` network. If any check fails, `sudo apt-get purge docker.io` and stop. Then install the token and deployer as for a Spark, using the node's `PVE_SBX_N_TOKEN` key and `/tmp/compose.yaml`.

Containers on a PVE node use host networking only. doco-cd has no bind-address setting, so its health and metrics ports (8080, 9120) listen on every interface of the node; its webhook and API stay disabled without a secret.

## Verify a host

```promql
time() - tlast_over_time(up{instance="spark-1"}[1h])
count by (job) (up{instance="spark-1"} == 1)
```

The first is a few seconds on a healthy host. The second lists `spark-node`, `spark-gpu`, `spark-vmagent` and `spark-deployer`, plus `spark-inference` on the node serving the head rank. On the host, `curl -s 127.0.0.1:8429/targets` shows each scrape target and `docker logs doco-cd` shows deployments.

Check memory with `docker stats --no-stream`, which matters on a Spark where a loaded model leaves little free. Every container keeps at most 30 MB of logs and has a memory limit: 256 MiB each for doco-cd and vmagent, 128 MiB each for node-exporter and the GPU exporter, so 768 MiB at most on a Spark and 640 MiB on a PVE node. In an ARM64 fixture on 2026-10-03 they used about 50 MiB (doco-cd, 65 MiB peak), 20 MiB (vmagent, while queueing with the endpoint unreachable), 8 MiB (node-exporter) and 8 MiB (GPU exporter without its `nvidia-smi` child process). These have not been measured on a Spark; record the real figures at the first enrollment.

## Change, pause, rotate, remove

- **Change:** merge to `main`. Each host applies it on its next poll; only the service whose files changed is recreated, and the queue survives. A commit whose images cannot be pulled leaves the running containers in place. A service that starts and then keeps failing makes doco-cd recreate its whole project on every poll until it is fixed. Either case raises `HostDeployFailing`. `scripts/ci/validate-host-monitoring.sh` renders every deployment and has the pinned vmagent parse its scrape files; run it before merging a change.
- **Pause:** `docker stop doco-cd` on the host. Collection continues.
- **Rotate a token:** replace the value with `sops`, merge, then rewrite `/etc/monitoring/token` as above. `vmagent` rereads the file; samples queue in between.
- **Remove a host:** on the host run `docker compose -p doco-cd down` and `docker compose -p monitoring down -v`, plus `docker compose -p monitoring-gpu down` on a Spark, then delete its `VMUser`. `-p` selects the project by name, so the commands work from any directory. `HostMetricsMissing` fires for a host that stops reporting until it has been silent for seven days; silence it for a planned removal.
- **Add a host:** add a key to the token Secret and a `VMUser`, then enroll it.

## Proxmox API

Create a read-only user and token on any node, store the token value as `PVE_TOKEN_VALUE` in a SOPS Secret named `pve-exporter-credentials` beside the [exporter](../../kubernetes/apps/observability/pve-exporter/app/), list it in that `kustomization.yaml`, and remove `suspend: true` from the Flux Kustomization.

```sh
sudo pveum user add metrics@pve --comment "Read-only metrics"
sudo pveum acl modify / --users metrics@pve --roles PVEAuditor
sudo pveum user token add metrics@pve exporter --privsep 0
```

The exporter reports quorum, node and guest state, storage use and which guests no backup job covers. It has no metric for whether a backup ran or succeeded.

## Alerts

Host rules are in [metrics-ingest](../../kubernetes/apps/observability/metrics-ingest/app/alerts.yaml) and Proxmox API rules beside the [exporter](../../kubernetes/apps/observability/pve-exporter/app/alerts.yaml). A host is covered from its first sample; there is no enrollment list to maintain. They route like every other alert, described in the [alerting runbook](alerting.md).
