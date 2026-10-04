# Spark monitoring

Host, GPU and inference metrics for the two DGX Sparks. Enrollment, verification and removal are in the [external hosts runbook](../../docs/runbooks/external-hosts.md).

| File | Purpose |
|---|---|
| [compose.yaml](compose.yaml) | node-exporter and vmagent. Identical on both Sparks. |
| [gpu/compose.yaml](gpu/compose.yaml) | The NVIDIA GPU exporter, as its own project. |
| [scrape.yaml](scrape.yaml) | Host, GPU, vmagent and deployer scrape jobs. |
| [compose.inference.yaml](compose.inference.yaml), [inference.yaml](inference.yaml) | Adds the one-second scrape of the serving endpoint. Used only on the leader. |
| `spark-N/.doco-cd.yml` | Which projects and Compose files that node deploys. |
| [../platform/doco-cd](../platform/doco-cd/compose.yaml) | The deployer, applied by hand once per node. |

SparkRun owns inference. This project never starts, stops or restarts an inference container.

Both TP ranks serve one deployment, so only the leader's endpoint is scraped. The labels in `inference.yaml` describe that deployment. Before changing a recipe, identify the installed runtime, model revision, TP and concurrency, then update those labels and give a materially different evaluation its own `deployment` value. Move `compose.inference.yaml` to the other node's `.doco-cd.yml` if the leader moves.

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
