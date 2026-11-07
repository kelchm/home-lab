# Spark monitoring

Host, GPU and inference metrics for the two DGX Sparks. Enrollment, verification and removal are in the [host monitoring runbook](../../docs/runbooks/host-monitoring.md).

| File | Purpose |
|---|---|
| [compose.yaml](compose.yaml) | node-exporter, smartctl-exporter, apt-metrics, vmagent and fluent-bit. |
| [logs/](logs/) | fluent-bit's configuration and journal mapping; see [Host logs](../../docs/runbooks/logging.md#host-logs). Identical to the PVE nodes'. |
| [gpu/compose.yaml](gpu/compose.yaml) | The NVIDIA GPU exporter, as its own project. |
| [scrape.yaml](scrape.yaml) | Host, SMART, GPU, vmagent, deployer, log collector and inference scrape jobs. |
| [.doco-cd.yml](.doco-cd.yml) | The two deployments every Spark's doco-cd applies. |
| [sparkdash/compose.yaml](sparkdash/compose.yaml) | The sparkDash dashboard, on `spark-1` only. |
| [.doco-cd.spark-1.yml](.doco-cd.spark-1.yml) | sparkDash and doco-cd with `SPARK_HOST` set to `spark-1`. |
| [.doco-cd.spark-2.yml](.doco-cd.spark-2.yml) | doco-cd with `SPARK_HOST` set to `spark-2`. |
| [../platform/doco-cd](../platform/doco-cd/compose.yaml) | The deployer, bootstrapped by hand and then self-updated. Each Git self target supplies `SPARK_HOST` to select its own poll from the shared definition. |

The collector files are identical on both Sparks; [sparkDash](#sparkdash) is the one project that runs on a single node. node-exporter runs its default collectors except `cpufreq`: on a Spark that collector never returns, which stalls every scrape. Without it a full scrape takes about 0.2 seconds. SparkRun owns inference; this project never starts, stops or restarts an inference container.

Native self-update uses a [one-time migration](../../docs/runbooks/doco-cd-self-update.md), where version, migration status, and acceptance checks are maintained. Each Spark polls its own self target, so a `spark-1` handover retains the dashboard poll. Local `/opt/doco-cd` files are bootstrap/recovery copies and may become stale after self-updates.

## Inference

Nothing here names a model. vmagent asks Docker for running containers that carry a `sparkrun.runtime` label and scrapes the serving endpoint of the head rank (`sparkrun.rank` 0, or a single-node run) every second. The series take their `model`, `runtime` and `recipe` labels from SparkRun's own container labels (`sparkrun.served_model_name`, `sparkrun.runtime`, and the recipe file name), so swapping a model or runtime with SparkRun needs no change in Git: vmagent rereads the container list every 30 seconds and follows it. The target exists for as long as a head container runs, whether or not the server inside it is answering. While a model loads, and for up to 30 seconds after a container stops, scrapes fail and `up{job="spark-inference"}` is 0; no alert is tied to that. Once a model is serving, `InferenceEngineBroken` fires on a fault the engine reports and `InferenceEngineStalled` on a decoding stream that stops writing tokens. `NodeMemoryAvailableLow` warns under 2 GiB for ten minutes, `NodeMemoryAvailableCritical` fires under 1.25 GiB for two, and `NodeGPUMemoryExhausted` warns when the kernel logs `NV_ERR_NO_MEMORY`. A loaded Spark rests above these lines, so normal use stays quiet. With no SparkRun container running there is no target. Both TP ranks serve one deployment; the worker rank is not scraped.

Two things are still fixed. Port 8888 is the serving port every recipe uses; a recipe on another port shows as a down target. Reading container labels requires the Docker socket, so on a Spark vmagent can reach the Docker API like the deployer can.

All metrics the endpoint exposes are kept, whatever the runtime. The TensorFold dashboard reads the `tensorfold:*` and `tensorfold_health:*` families; another runtime's metrics are collected the same way but need their own dashboard.

doco-cd recreates every container of a project on each poll while one of its services cannot start. The GPU exporter depends on the driver and CDI, so it is a separate project: if it cannot start after a driver or OS update, host and inference collection keep running. It runs `nvidia-smi` through NVIDIA CDI; its NVML build is AMD64-only. GB10 has no dedicated framebuffer, so GPU memory is not reported: read host memory and memory pressure instead. A power-cap flag is ordinary operation; thermal slowdown is not. `nvidia-smi` power is the GPU alone, not the machine. Board temperature comes from the ACPI thermal zones; the firmware's critical trip point on both Sparks is 104.8 °C.

## sparkDash

[sparkDash](https://github.com/MiaAI-Lab/sparkDash) is a live dashboard for both Sparks at `http://10.32.21.31:5555`. It asks for an access token on first use; read it with `ssh kelchm@spark-1 sudo cat /etc/sparkdash/token`. It has no agent mode: one instance reads its own host directly and every other unit over SSH, so it runs on `spark-1` and logs in to `spark-2`. Its series are not scraped into the metrics store: nearly all of them repeat, in less detail, what node-exporter, the GPU exporter and the inference scrape already send, and its RoCE and energy readings are not in its Prometheus export. Whether to extend that export is tracked in [#812](https://github.com/kelchm/home-lab/issues/812).

The container is privileged, shares the host's PID namespace and network, and mounts `/` read-only, which is what its local collectors need. On `spark-2` it logs in as `sparkdash`, an account with no sudo rights and no Docker access, so it can read metrics there but cannot power the node off or start or stop a container. No model launcher is configured: SparkRun owns inference.

The image is built from an upstream release by the [kelchm/sparkDash](https://github.com/kelchm/sparkDash) fork. Upstream incorporated the publishing workflow from [MiaAI-Lab/sparkDash#197](https://github.com/MiaAI-Lab/sparkDash/pull/197) as [`bfdfe16`](https://github.com/MiaAI-Lab/sparkDash/commit/bfdfe168d154556a5e484f16f07ad4037ebccd5a), as the [maintainer confirmed](https://github.com/MiaAI-Lab/sparkDash/pull/197#issuecomment-6123639182); GitHub records the PR as closed without merging. The fork stays deployed pending a qualifying public upstream image/tag/digest and an accepted replacement ([#809](https://github.com/kelchm/home-lab/issues/809)). To build a newer release, cherry-pick the fork's publish commit onto the upstream release commit and push a `v<version>-<n>` tag to the fork.

State outside Git, all on `spark-1` unless noted:

| Path | Content |
|---|---|
| `/etc/sparkdash/token` | The access token. Any value works; replace it and restart the container to rotate. |
| `/etc/sparkdash/id_ed25519` | The SSH key for `spark-2`, authorized for `sparkdash` in `/home/sparkdash/.ssh/authorized_keys` there. |
| `/var/lib/sparkdash` | Fleet, settings and history. The two units are registered in `sparks.json`. |
| `/opt/doco-cd/compose.yaml` | Bootstrap/recovery copy of the shared Spark definition. Manual apply supplies `SPARK_HOST`; Git self targets supply it for native updates. |

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

Bootstrap or migrate `spark-1` using the [shared definition with `SPARK_HOST=spark-1`](../../docs/runbooks/doco-cd-self-update.md#bootstrap-or-migrate). doco-cd starts the dashboard within three minutes. Then add `spark-1` as this host and `spark-2` as a remote unit with user `sparkdash` and key authentication.

## Reading the TensorFold dashboard

| Signal | Interpretation |
|---|---|
| Live output tokens/s | Reply tokens written per second across all streams, including idle time. The GLM kit's Python server counts them in `tensorfold_health:completion_tokens_total`; TensorFold servers in `tensorfold:generation_tokens_total` plus `tensorfold:generation_tokens_running`, which overlap for one scrape as a reply finishes, so the chart keeps a window of at least four seconds and a short reply reads lower than its decode speed. Built from `rate()` and `delta()` rather than a subquery, so any range works. |
| Decode speed per reply | Finished-request tokens over their decode seconds, as one point per step: a single reply when zoomed in. Each reply's decode time is counted separately, so it falls as concurrent replies share the GPU; the machine's total is Live output. One-token replies decode in no time and would read as thousands of tokens per second, so a step with under 0.05 s of decoding shows nothing. |
| Prefill speed | Prompt tokens not found in cache over prefill seconds, as one point per step. Recorded when a request finishes, so a prompt still being read appears only as the blue band. |
| Decode round time | Decode seconds over decode rounds. It rises with concurrent replies and, unlike tokens per second, does not move with draft acceptance. |
| TTFT and request latency | Native server histograms updated on request completion, shown as heatmaps because quantiles need more requests than a Spark usually finishes in five minutes. Buckets are coarse above ten seconds: 30, 60, 120 and 300. TTFT ends at the first generated token, not necessarily the first client-visible text. |
| Running / waiting | Native current request gauges against `tensorfold_health:streams_max`. TP ranks are not extra requests. Queue-wait duration is not measured. The blue band is a request reading its prompt: a filling stream on the Python kit, or on TensorFold a running request with no reply tokens in flight. |
| Prompt cache hits | Cached prompt tokens over `tensorfold:prompt_tokens_total`, by tokens rather than requests. |
| Pool used | Share of the shared context pool held by streams and kept prompts, from `tensorfold_health:pool_tokens` and `pool_free_tokens`. It is not unified-memory use. |
| Context held by running requests | `tensorfold:kv_cache_usage_ratio` for each live stream, multiplied by `tensorfold_health:context_length` for tokens. Its `pool` label is a place in the list of live streams and shifts when an earlier stream finishes, and requests have no id in the metrics. So the bars rank streams by size at that moment, `#1` being the largest then, and the tokens chart draws every stream in one colour. The kept conversations are in `/health` as `kept_mix`. |
| Prompt tokens read | Prompt tokens of the requests finished in each step, new and cached, as bars. A cancelled request counts its whole prompt as new. It does not measure live prefill speed. |
| Speculative acceptance | Accepted / drafted tokens over completed requests. The `mtp` metric prefix does not identify the actual drafter or prove MTP is enabled. |
| Engine | `tensorfold_health:ok`, which the engine sets to 0 on a fault that stops it serving; `/health` gives the first cause. "No metrics" means no fresh scrape and "Not reported" a server without the flag. A healthy scrape does not prove a request can complete or detect a stalled generation. |

Shaded bands behind a chart are states, not values. On the inference dashboard amber is every stream busy and red is requests waiting. On the health dashboard red is thermal slowdown, memory stalls or RoCE errors, amber is the power cap and grey is a busy GPU.

TensorFold's servers and the GLM kit's Python server name four counters differently, and no recipe exports both, so each query reads either name:

| Figure | TensorFold | GLM kit, Python server |
|---|---|---|
| Cached prompt tokens | `tensorfold:prompt_tokens_cached_total` | `tensorfold_health:cached_tokens_total` |
| Prefill seconds | `tensorfold:request_prefill_seconds_sum` | `tensorfold_health:prefill_seconds_total` |
| Decode seconds | `tensorfold:request_decode_seconds_sum` | `tensorfold_health:decode_seconds_total` |
| Decode rounds | `tensorfold:decode_rounds_total` | `tensorfold_health:rounds_total` |

The rest of the `tensorfold_health:*` families come only from the Python kit's `/health`, so the Engine flag, the pool, the window size behind the tokens chart and the stream capacity are not reported on other recipes. Decode, prefill and cache figures are booked when a request finishes and lag a running reply, so their charts draw one point or bar per step rather than a line. Neither server's metrics show a prompt's progress while it is read; TensorFold's `/stats`, enabled with `--dashboard`, does.

Idle counters produce zero throughput while fresh. Missing families, failed scrapes and stale collection remain absent. Rates handle ordinary counter resets; do not compare raw totals across restarts. One-second sampling does not provide per-token timing.

## Drive health

Each host has one NVMe controller at `/dev/nvme0`: Samsung MZALC4T0HBL1-00B07 4 TB on the Sparks. The stock v0.15.0 smartctl-exporter is scraped on loopback every minute. It maps only that controller, runs as root with `CAP_SYS_ADMIN`, and retains Docker's default AppArmor profile, dropped remaining capabilities, read-only filesystem and a 128 MiB memory limit. It is not privileged, but NVMe admin authority can still modify or erase the mapped drive if compromised; the device `r` mapping does not restrict ioctls to harmless commands. Adding a drive requires updating the explicit device list.

The existing **Hosts / Drive health** dashboard and shared rules use `instance`, `platform` and `device`. See the runbook for [permissions, the exporter cache limitation, and post-merge runtime verification](../../docs/runbooks/host-monitoring.md#drive-health). Runtime collection on these hosts remains to be verified after deployment.
