# DGX Spark thermal headroom

Measured 2026-08-23 against a **21.1 °C (70 °F) cabinet inlet**, stated by the operator rather than instrumented.

The load was a sustained bf16 matmul at 96.6 TFLOP/s, sampled every 5 s for GPU temperature, power, clock, the driver's cumulative throttle counters, and board/NVMe/ConnectX-7 hwmon. The harness and raw CSVs are retained outside this repo; it is not kept here because a real inference workload now pins both GPUs at 96% for hours, which is a better and more representative load than a synthetic burn.

## Bottom line

Cooling was adequate in every run: **zero thermal throttle microseconds throughout**. In this one dual-node run at a ~21 °C inlet, no cabinet-level coupling was measurable — spark-1's peak fell 86 → 84 °C while its steady-state mean rose 83.0 → 84.0 °C, i.e. the two nodes did not visibly heat each other. The binding limit is the Spark's own chassis, which consumes ~62–65 °C of rise on its own, putting the **maximum tolerable cabinet inlet somewhere around 25–28 °C (77–82 °F)** depending on whether peak or mean rise is used.

The cabinet is in a garage. That 77 °F ceiling, not the rack, is what will bite.

## What was measured

Sustained bf16 matmul (96.6 TFLOP/s, 87 W steady) for 20 minutes per run. Steady state is the last half of each loaded segment.

| Run | GPU avg | GPU max | Min headroom | Board max | NIC max | SM clock | Thermal throttle |
|---|---|---|---|---|---|---|---|
| spark-1 solo | 83.0 °C | 86 °C | 4 °C | 94.8 °C | 78 °C | 2179 MHz | **0 µs** |
| spark-1 dual | 84.0 °C | 84 °C | 5 °C | 92.8 °C | 77 °C | 2190 MHz | **0 µs** |
| spark-2 dual | 85.3 °C | 86 °C | 4 °C | 94.7 °C | 81 °C | 2114 MHz | **0 µs** |

Idle reference: GPU 37–40 °C, board 41–43 °C, NIC 43–45 °C, 3–5 W.

## The three findings that matter

**1. The second node showed no measurable cost — with real caveats.** spark-1 peaked at 86 °C alone and 84 °C alongside spark-2, while its steady-state mean went the other way, 83.0 → 84.0 °C. Both movements are ~1 °C and there is **one run per condition, no replicates**, so "inside run-to-run noise" is an assertion this data cannot actually test. Read this as "no cabinet coupling large enough to see in a single 20-minute run at 21 °C inlet" — not as proof the cabinet is irrelevant.

Two further limits on that claim: the inlet temperature is **operator-stated, not instrumented**, and the load is a bf16 matmul that bounds *GPU compute* heat only. It does not exercise the ConnectX-7 under TP=2 RDMA traffic, and that NIC already reached 81 °C on case-air soak alone.

**2. The chassis owns the entire budget.** The rise from cabinet inlet to GPU die is 64.9 °C. Published open-air figures for this chassis are in the same range, and NVIDIA's stated position is that 86 °C under load is well within limits. The reference cooler is the known bottleneck on this platform — a competitor board using the same GB10 silicon with a vapour chamber runs 10–15 °C cooler. Nothing done to the cabinet moves this number much.

**3. The ceiling is ambient, and it is close.**

```
die temp   = cabinet inlet + 64.9 °C   (measured, 87 W sustained)
throttle   = ~90 °C
max inlet  = 90 − 64.9 = 25.1 °C = 77 °F
```

Seven degrees Fahrenheit of margin, in a garage. A 90 °F garage puts the die near 97 °C; a 100 °F garage near 103 °C.

## What follows

- **Do not buy cabinet cooling on this data alone.** It shows no measurable benefit available at 21 °C inlet, which is enough to defer the purchase — not enough to rule it out. An earlier ~100 CFM estimate in this work was superseded, but the replacement is "no evidence of a problem yet," not "proven unnecessary."
- **Replicate before deciding.** Re-run on the hottest day available, with an instrumented inlet sensor and at least two runs per condition, ideally under real TP=2 inference rather than a synthetic matmul.
- **Treat garage ambient as the real variable.** Re-run the load test on the hottest day available before trusting sustained dual-node work through summer.
- **`nvidia-smi -lgc 200,2150` is the cheap lever** if ambient does climb — capping clocks trades a little throughput for meaningful heat, and is reversible.
- **Watch the ConnectX-7.** It reached 81 °C on pure case-air soak with no network traffic. Tensor-parallel work would add RDMA load to an already-hot NIC — an independent reason to prefer two single-node servers.
- **Board temperature runs hotter than the die** (94.8 °C vs 86 °C) on every run. If a future failure looks thermal but the GPU looks fine, check `tz0` first.

## Under real inference load

The synthetic matmul is an upper bound on GPU compute and die temperature only - it does not exercise the ConnectX-7 under TP=2 RDMA traffic, so it does not bound NIC heating. Measured during three concurrent
DeepSeek-V4-Flash sessions at TP=2, both GPUs at 96% utilisation:

| | synthetic burn | real inference |
|---|---|---|
| GPU temp | 86 C | 63-75 C |
| GPU power | 87 W | 57-69 W |
| thermal throttle | 0 us | 0 us |

The head node runs ~12 C hotter than the worker, carrying the API server and
scheduler on top of its shard. Heat is not the operational constraint for this
workload: **host memory is**. Three sessions leave roughly 6 GB available of
121 GB, and GB10 shares one pool between CPU and GPU.

## Monitoring

There is no continuous collection today; these are one-off artifacts. Wiring the Sparks into the cluster's metrics stack would make them the **first out-of-cluster scrape target in this repo** — no `additionalScrapeConfigs`, `ScrapeConfig`, or `Probe` precedent exists. Two things make it straightforward when it is wanted: vmagent runs `selectAllByDefault: true`, so a `VMStaticScrape` is picked up with no chart edit, and the `observability` namespace has no NetworkPolicy restricting egress.

Alertmanager now delivers `warning` and `critical` alerts externally. Follow [alerting](runbooks/alerting.md) when adding Spark metrics and rules so the new signal is included in the end-to-end delivery test.

The Sparks expose no fan tachometer and no BMC, so anything built has to run on the hosts: `node_exporter` for hwmon and thermal zones, plus something for `nvidia-smi`.
