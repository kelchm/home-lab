# Spark monitoring

Host, GPU and inference metrics for the two DGX Sparks. Enrollment, verification and removal are in the [host monitoring runbook](../../docs/runbooks/host-monitoring.md).

| File | Purpose |
|---|---|
| [compose.yaml](compose.yaml) | node-exporter and vmagent. |
| [gpu/compose.yaml](gpu/compose.yaml) | The NVIDIA GPU exporter, as its own project. |
| [scrape.yaml](scrape.yaml) | Host, GPU, vmagent, deployer and inference scrape jobs. |
| [.doco-cd.yml](.doco-cd.yml) | The two deployments every Spark's doco-cd applies. |
| [sparkdash/compose.yaml](sparkdash/compose.yaml) | The sparkDash dashboard, on `spark-1` only. |
| [.doco-cd.spark-1.yml](.doco-cd.spark-1.yml) | sparkDash and doco-cd with its `spark-1` override. |
| [.doco-cd.spark-2.yml](.doco-cd.spark-2.yml) | doco-cd on `spark-2`, using the base Compose definition. |
| [../platform/doco-cd](../platform/doco-cd/compose.yaml) | The deployer, bootstrapped by hand and then self-updated. `compose.spark-1.yaml` replaces the base `spark-2` poll with the `spark-1` poll. |

The collector files are identical on both Sparks; [sparkDash](#sparkdash) is the one project that runs on a single node. node-exporter runs its default collectors except `cpufreq`: on a Spark that collector never returns, which stalls every scrape. Without it a full scrape takes about 0.2 seconds. SparkRun owns inference; this project never starts, stops or restarts an inference container.

Native self-update uses a [one-time migration](../../docs/runbooks/doco-cd-self-update.md), where version, migration status, and acceptance checks are maintained. Each Spark polls its own self target, so a `spark-1` handover retains the dashboard poll. Local `/opt/doco-cd` files are bootstrap/recovery copies and may become stale after self-updates.

## Inference

Nothing here names a model. vmagent asks Docker for running containers that carry a `sparkrun.runtime` label and scrapes the serving endpoint of the head rank (`sparkrun.rank` 0, or a single-node run) every second. The series take their `model`, `runtime` and `recipe` labels from SparkRun's own container labels (`sparkrun.served_model_name`, `sparkrun.runtime`, and the recipe file name), so swapping a model or runtime with SparkRun needs no change in Git: vmagent rereads the container list every 30 seconds and follows it. The target exists for as long as a head container runs, whether or not the server inside it is answering. While a model loads, and for up to 30 seconds after a container stops, scrapes fail and `up{job="spark-inference"}` is 0; no alert is tied to that. With no SparkRun container running there is no target. Both TP ranks serve one deployment; the worker rank is not scraped.

Two things are still fixed. Port 8888 is the serving port every recipe uses; a recipe on another port shows as a down target. Reading container labels requires the Docker socket, so on a Spark vmagent can reach the Docker API like the deployer can.

All metrics the endpoint exposes are kept, whatever the runtime. The TensorFold dashboard reads the `tensorfold:*` families; another runtime's metrics are collected the same way but need their own dashboard.

doco-cd recreates every container of a project on each poll while one of its services cannot start. The GPU exporter depends on the driver and CDI, so it is a separate project: if it cannot start after a driver or OS update, host and inference collection keep running. It runs `nvidia-smi` through NVIDIA CDI; its NVML build is AMD64-only. GB10 has no dedicated framebuffer, so GPU memory is not reported: read host memory and memory pressure instead. A power-cap flag is ordinary operation; thermal slowdown is not.

## sparkDash

[sparkDash](https://github.com/MiaAI-Lab/sparkDash) is a live dashboard for both Sparks at `http://10.32.21.31:5555`. It asks for an access token on first use; read it with `ssh kelchm@spark-1 sudo cat /etc/sparkdash/token`. It has no agent mode: one instance reads its own host directly and every other unit over SSH, so it runs on `spark-1` and logs in to `spark-2`. Its series are not scraped into the metrics store.

The container is privileged, shares the host's PID namespace and network, and mounts `/` read-only, which is what its local collectors need. On `spark-2` it logs in as `sparkdash`, an account with no sudo rights and no Docker access, so it can read metrics there but cannot power the node off or start or stop a container. No model launcher is configured: SparkRun owns inference.

The image is built from an upstream release by the [kelchm/sparkDash](https://github.com/kelchm/sparkDash) fork, because upstream publishes none; [MiaAI-Lab/sparkDash#197](https://github.com/MiaAI-Lab/sparkDash/pull/197) proposes the same build upstream. To build a newer release, cherry-pick the fork's publish commit onto the upstream release commit and push a `v<version>-<n>` tag to the fork.

State outside Git, all on `spark-1` unless noted:

| Path | Content |
|---|---|
| `/etc/sparkdash/token` | The access token. Any value works; replace it and restart the container to rotate. |
| `/etc/sparkdash/id_ed25519` | The SSH key for `spark-2`, authorized for `sparkdash` in `/home/sparkdash/.ssh/authorized_keys` there. |
| `/var/lib/sparkdash` | Fleet, settings and history. The two units are registered in `sparks.json`. |
| `/opt/doco-cd/compose.spark-1.yaml` | Bootstrap/recovery copy installed by the migration procedure; older installs used `compose.override.yaml`. Manual applies explicitly list the base and this file; Git is authoritative after migration. |

To rebuild that state, run from a workstation:

```sh
ssh kelchm@spark-1 'sudo install -d -m 0700 /etc/sparkdash /var/lib/sparkdash \
  && sudo ssh-keygen -q -t ed25519 -N "" -C sparkdash@spark-1 -f /etc/sparkdash/id_ed25519 \
  && openssl rand -hex 24 | tr -d "\n" | sudo install -m 0600 /dev/stdin /etc/sparkdash/token'
ssh kelchm@spark-1 sudo cat /etc/sparkdash/id_ed25519.pub \
  | ssh kelchm@spark-2 'sudo useradd --system --create-home --shell /bin/bash sparkdash \
  && sudo install -d -m 0700 -o sparkdash -g sparkdash /home/sparkdash/.ssh \
  && sudo install -m 0600 -o sparkdash -g sparkdash /dev/stdin /home/sparkdash/.ssh/authorized_keys'
```

Bootstrap or migrate `spark-1` using [both deployer files](../../docs/runbooks/doco-cd-self-update.md#bootstrap-or-migrate). doco-cd starts the dashboard within three minutes. Then add `spark-1` as this host and `spark-2` as a remote unit with user `sparkdash` and key authentication.

## Reading the TensorFold dashboard

| Signal | Interpretation |
|---|---|
| Live output tokens/s | `irate(tensorfold_health:completion_tokens_total[10s])` uses the latest scrape interval and includes replies in progress. Aggregate across active streams, including idle time; not a per-request decode measurement. |
| Output throughput chart | The fast live trace uses the latest one-second scrape interval; separate one-minute averages compare live output with output booked only when a request finishes. A long reply can produce a completion-accounting spike without a real decode-speed increase. |
| TTFT and request latency | Native server histograms are updated on request completion. TTFT ends at the first generated token, not necessarily the first client-visible text. Mean uses the last five minutes. |
| Latency percentiles | Coarse histogram estimates, hidden with fewer than 20 completed samples in five minutes or any observations above the largest finite bucket (300 seconds). The mean still includes those observations. |
| Running / waiting | Native current request gauges. TP ranks are not extra requests. Queue-wait duration is not measured. |
| Shared pool used | `(pool_tokens - pool_free_tokens) / pool_tokens`; includes retained prompt states. It is not unified-memory utilization or the native per-stream context ratio. |
| Prompt reuse | Cached prompt tokens divided by full prompt tokens over completed requests. No value without recent completed input accounting. |
| Prompt throughput | Full and uncached prompt accounting booked on completion. Neither measures live prefill speed. |
| Speculative acceptance | Accepted / drafted tokens over completed requests. The `mtp` metric prefix does not identify the actual drafter or prove MTP is enabled. |
| Scrape status | Fresh successful metrics retrieval. It does not prove end-to-end inference readiness or detect a stalled generation. |

Idle counters produce zero throughput while fresh. Missing families, failed scrapes and stale collection remain absent. Rates handle ordinary counter resets; do not compare raw totals across restarts. One-second sampling does not provide per-token timing.
