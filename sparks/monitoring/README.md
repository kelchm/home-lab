# Spark monitoring

Host, GPU and inference metrics for the two DGX Sparks. Enrollment, verification and removal are in the [host monitoring runbook](../../docs/runbooks/host-monitoring.md).

| File | Purpose |
|---|---|
| [compose.yaml](compose.yaml) | node-exporter and vmagent. |
| [gpu/compose.yaml](gpu/compose.yaml) | The NVIDIA GPU exporter, as its own project. |
| [scrape.yaml](scrape.yaml) | Host, GPU, vmagent, deployer and inference scrape jobs. |
| [.doco-cd.yml](.doco-cd.yml) | The two deployments every Spark's doco-cd applies. |
| [../platform/doco-cd](../platform/doco-cd/compose.yaml) | The deployer, applied by hand once per node. |

Every file is identical on both Sparks. SparkRun owns inference; this project never starts, stops or restarts an inference container.

## Inference

Nothing here names a model. vmagent asks Docker for running containers that carry a `sparkrun.runtime` label and scrapes the serving endpoint of the head rank (`sparkrun.rank` 0, or a single-node run) every second. The series take their `model`, `runtime` and `recipe` labels from SparkRun's own container labels (`sparkrun.served_model_name`, `sparkrun.runtime`, and the recipe file name), so swapping a model or runtime with SparkRun is followed within about 30 seconds and needs no change in Git. While nothing is being served there is no target, so a swap does not produce failed scrapes. Both TP ranks serve one deployment; the worker rank is not scraped.

Two things are still fixed. Port 8888 is the serving port every recipe uses; a recipe on another port shows as a down target. Reading container labels requires the Docker socket, so on a Spark vmagent can reach the Docker API like the deployer can.

All metrics the endpoint exposes are kept, whatever the runtime. The TensorFold dashboard reads the `tensorfold:*` families; another runtime's metrics are collected the same way but need their own dashboard.

doco-cd recreates every container of a project on each poll while one of its services cannot start. The GPU exporter depends on the driver and CDI, so it is a separate project: if it cannot start after a driver or OS update, host and inference collection keep running. It runs `nvidia-smi` through NVIDIA CDI; its NVML build is AMD64-only. GB10 has no dedicated framebuffer, so GPU memory is not reported: read host memory and memory pressure instead. A power-cap flag is ordinary operation; thermal slowdown is not.

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
