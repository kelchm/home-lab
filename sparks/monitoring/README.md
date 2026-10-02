# TensorFold performance monitoring

This is an initial evaluation for GLM-5.3-Flash-EXL3 running TensorFold across spark-1 and spark-2 with TP=2. The [Grafana dashboard](https://grafana.home.kelch.io/d/spark-tensorfold-performance/sparks-tensorfold-performance) uses the existing VictoriaMetrics datasource and storage. Its definition is [versioned with Grafana](../../kubernetes/apps/observability/grafana/app/spark-tensorfold-dashboard.json). Track unfinished acceptance and permanent collection in [#653](https://github.com/kelchm/home-lab/issues/653); PVE monitoring and host configuration delivery remain separate in [#650](https://github.com/kelchm/home-lab/issues/650).

## Collection and verified state

Observed on October 1, 2026: `http://10.32.21.31:8888/v1/models` names `GLM-5.3-Flash-EXL3`, `/health` reports the CUDA/concurrent engine with four streams and an 850,000-token context ceiling, and `/metrics` already includes the `tensorfold_health:*` families needed for live generation and shared pool occupancy. No JSON adapter, host exporter, inference restart, or runtime patch is required for this build. The [fixtures](fixtures/) are discovery snapshots, not current operational state. Exact installed engine/image and model revisions remain unverified because SSH signing was unavailable; the repository's current launch recipe alone is not evidence of the running build.

The [scrape configuration](scrape.yaml) collects the leader once every five seconds, with a three-second timeout and a 1,000-sample limit. Both TP ranks contribute to one serving deployment; do not scrape a second rank as another copy of the same inference counters. Target labels describe the external service and deliberately omit `cluster=k8s-prod`. The native `tensorfold:*` and `tensorfold_health:*` families are kept; compatibility aliases are excluded.

The temporary collector runs on the operator workstation and writes through an authenticated Kubernetes port-forward to VMSingle. Both its HTTP listener and the port-forward bind only to `127.0.0.1`. This allows an evaluation without relaxing the existing K8s-to-Workloads containment or introducing a persistent privileged SSH connection. The dashboard's ConfigMap can be applied independently for the pilot; after merge Flux provisions that same stable ConfigMap name.

This pilot is not unattended monitoring. Workstation sleep, loss of network/API access, process exit, or tunnel failure stops ingestion. The supervisor stops both children when either exits, keeps logs and a bounded disk queue, and requires an explicit restart. It does not automatically relaunch or create scheduled inference traffic. Dashboard values require a successful scrape sample younger than 20 seconds; a stopped collector is displayed as unavailable, rather than preserving a last healthy value. Query caching and Grafana refresh can add display delay, so allow the next refresh before diagnosing a transition.

## Running the evaluation collector

Prerequisites: `kubectl` with access to `observability` pod port-forwarding, a readable cluster kubeconfig, direct workstation access to the Spark leader, and a verified `vmagent` binary. The pilot uses VictoriaMetrics **v1.152.0**, matching the current cluster vmagent. Obtain it from the [official release](https://github.com/VictoriaMetrics/VictoriaMetrics/releases/tag/v1.152.0), verify the archive digest, and extract only `vmagent-prod`. The macOS ARM64 archive `vmutils-darwin-arm64-v1.152.0.tar.gz` has SHA-256 `22192226f8f6dd7e4950630c74fa4e32a4f08e1f220913f76b14db899977f2a1`.

Run from the repository root:

```sh
VMAGENT_BIN=/path/to/verified/vmagent-prod \
PILOT_KUBECONFIG=/path/to/cluster/kubeconfig \
./sparks/monitoring/pilot.sh
```

The process stays in the foreground; Ctrl-C stops the collector and tunnel. On normal supervisor termination, vmagent gets a chance to flush/persist its queue before the tunnel closes. Unsent queued data can replay after restart; a hard kill can leave a stale lock. `PILOT_KUBECONFIG` takes precedence over `KUBECONFIG`, which avoids a worktree's mise configuration selecting a nonexistent kubeconfig. By default, state lives in `${XDG_CACHE_HOME:-$HOME/.cache}/home-lab/spark-monitoring-pilot`; `PILOT_STATE_DIR` can override it. The current macOS evaluation cached the verified binary there as `vmagent-v1.152.0`.

- Collector target status: `http://127.0.0.1:18429/targets`.
- VictoriaMetrics query API through the tunnel: `http://127.0.0.1:18428/api/v1/query`.
- Logs: `vmagent.log` and `port-forward.log` under the state directory.
- Cache budget: 64 MiB; one remote-write queue; disk queue cap 600 MB. These are vmagent budgets, not an OS memory limit. Its minimum supported disk cap exceeds 512 MB, so a smaller requested cap would silently round up.

For a background evaluation, use `nohup` with stdout/stderr redirected to a file and record the supervisor PID in `run.lock/pid`. Send SIGTERM to that verified supervisor process to stop both children. Inspect the PID/command before signaling it; after an unclean exit, confirm no pilot processes remain before removing a stale `run.lock`. Do not start overlapping pilots or run this alongside a permanent scraper with the same service identity.

## Reading the dashboard

| Signal | Interpretation |
|---|---|
| Live output tokens/s | `irate(tensorfold_health:completion_tokens_total[20s])` uses the latest scrape interval and includes replies in progress. Aggregate across active streams, including idle time; not a per-request decode measurement. |
| Output throughput chart | One-minute rates compare live output with output booked only when a request finishes. A long reply can produce a completion-accounting spike without a real decode-speed increase. |
| TTFT and request latency | Native server histograms are updated on request completion. TTFT ends at the first generated token, not necessarily the first client-visible text. Mean uses the last five minutes. |
| Latency percentiles | Coarse histogram estimates, hidden with fewer than 20 completed samples in five minutes or any observations above the largest finite bucket (300 seconds). The mean still includes those observations. |
| Running / waiting | Native current request gauges. TP ranks are not extra requests. Queue-wait duration is not measured. |
| Shared pool used | `(pool_tokens - pool_free_tokens) / pool_tokens`; includes retained prompt states. It is not unified-memory utilization or the native per-stream context ratio. |
| Prompt reuse | Cached prompt tokens divided by full prompt tokens over completed requests. No value without recent completed input accounting. |
| Prompt throughput | Full and uncached prompt accounting booked on completion. Neither measures live prefill speed. |
| Speculative acceptance | Accepted / drafted tokens over completed requests. The `mtp` metric prefix does not identify the actual drafter or prove MTP is enabled. |
| Scrape status | Fresh successful metrics retrieval. It does not prove end-to-end inference readiness or detect a stalled generation. |

Idle counters produce zero throughput while fresh. Missing families, failed scrapes and stale collection remain absent/unavailable. Ratios without a denominator and latency with no completed observations show no recent samples. Rates handle ordinary counter resets; do not compare raw totals across restarts. Query averages and percentiles describe the workload mix, so comparisons need comparable prompts, output lengths, cache state and concurrency. Stream states distinguish decoding, filling and paused lanes; they are not interchangeable with request counts.

Before changing recipes, identify the installed runtime/image, model revision, quantization, TP/concurrency, context ceiling, speculative policy and cache settings. Update target labels and the dashboard identity, and assign a distinct deployment label for a materially different evaluation. Do not label a guessed engine revision from the latest repository recipe. Confirm that the new build still supplies the required families and semantics; a future TensorFold build or vLLM endpoint may need a different dashboard.

## Initial verification

One idle-only streaming request used 44 prompt tokens and a hard 512-output-token cap, with a 45-second client deadline and no recurring probe. It completed in 9.985 seconds. Client first content/reasoning arrived at 208 ms; the native server TTFT observation was 196.762 ms. This is a collection sanity check, not a performance benchmark or SLO.

Nine samples showed live output advancing while the native finished-request output counter remained unchanged. The final client usage, live counter delta and completed-request output delta all accounted for 512 tokens, with one finished-request observation. The client reported 219 SSE events; event count was deliberately not treated as token count. Reasoning tokens were included in the model's output usage.

The dashboard queries were exercised against VictoriaMetrics, and PromQL behavior checks covered healthy idle, live progress, missing families, failed scrapes, stopped-collector freshness and counter resets. Percentiles require representative traffic; one request cannot validate a tail-latency distribution. Hardware, RDMA/TP communication health, reliable error/cancellation counts, client-visible TTFT, per-request decode/inter-token latency and automatic stall detection remain gaps.

## Permanent collection decision

The running cluster collector cannot currently reach the Sparks. The [network applied-state record](../../network/unifi/README.md#workloads-containment-applied-state) denies K8s-to-Workloads initiation. The earlier [observability proposal](../../docs/plans/20260703-observability-rework.md) calls for a dedicated collector egress identity before external pull access ships; that is a proposed network isolation choice, not a technical requirement of TensorFold or Prometheus. This pilot leaves the network decision open.

Choose and validate the permanent collector/network path in #653 after evaluating the dashboard. The existing cluster vmagent is the natural collector. A dedicated egress identity allows UniFi to distinguish it from ordinary node-SNAT traffic, with additional infrastructure and recovery work. A smaller alternative is a precise UniFi node-IP/leader-port exception plus Cilium rules allowing only vmagent to GET the metrics path; that trusts node-originated processes too and requires an explicit revision of the earlier proposal. In either case, keep the exception narrow, prove an unselected pod is denied, use an external scrape class without the Kubernetes cluster label, and test recovery. Keep any host-local deployment channel decision separate from PVE automation. Stop the workstation pilot at cutover to avoid double collection; completion of this pilot does not close the permanent monitoring work.
