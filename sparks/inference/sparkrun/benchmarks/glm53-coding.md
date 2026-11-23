# GLM-5.3-Flash coding throughput on two Sparks

The comparison uses one active coding session on two DGX Sparks with TensorFold TP2, four configured serving lanes, a 1,048,576-token context limit and a 32,768-token reply limit. The target is Mia’s EXL3 checkpoint at `078455ffe6472f9a52fbc1139f58b9db2881b25c`. Both engines use DFlash2 at `bf582e4eacc1810f76656d1811693ff6c6737d2a`. The Python reference is the durable readiness-v2 image with OCI config `sha256:b925352f3515caddc1dde8abf33a621afad8133d2964a091184ad4842bfff7b6`. The native port builds on Mia’s November 21 kit at `6c55c20`, with shared KV pages, two-stage packed projections and tensor-core prompt MLA. The kit still had that commit at its default-branch head on November 23. [Mia’s source revision](https://github.com/MiaAI-Lab/GLM-5.3-Flash-DGX-Sparks-1104/commit/6c55c20da31a02d6d381a268fa42f8a9ebe0c5d2)

## Measured coding throughput

Each engine served one active coding session. Rates are pooled output tokens per second. The two long histories were seeded from cold. Their elapsed seed times are excluded from the warm-turn rates.

| Workload | Python end to end | Zig end to end | Python after first token | Zig after first token | Streaming change |
| --- | ---: | ---: | ---: | ---: | ---: |
| 262K project history, eight warm turns | 53.64 | 55.75 | 66.68 | 68.21 | +2.29% |
| Fresh 262K history, eight warm turns | 53.41 | 54.05 | 66.43 | 66.43 | -0.01% |
| Separate 39.7K history, five warm turns | 54.75 | 52.05 | 57.17 | 56.48 | -1.20% |
| 262K, six counterbalanced 768-token writing replies | 63.67 | 64.97 | 69.51 | 70.72 | +1.75% |
| 15K, two naturally completed coding tasks | 67.66 | 56.98 | 71.44 | 72.14 | +0.97% |

The final Zig recipe streamed at 68.21 tok/s on the original 262K session. That is 23.55% above stock Mia Zig and 11.76% above our previous tuned native recipe on those request bodies. The six counterbalanced writing runs used three prompts twice each. A descriptive paired bootstrap over these six observations spans −0.23% to +3.81% around the +1.75% point estimate. The data support throughput parity with a small measured lead on this set. The separate same-day retest of the restored Python service reached 67.62 tok/s after first token and 57.74 end to end, with its original prefix already warm. The fresh-prefix seed for the qualified recipe took 180.31 s versus 138.07 s on Python.

On the 15K suite the model implemented interval merging and ordered batching. Zig passed 2/2 and Python passed 1/2 executable test suites. They emitted 434 and 1,210 total tokens and completed both requests in 7.62 s and 17.88 s respectively. The Python batching reply returned three implementations. All three failed the same executable test when checked independently. The shorter Zig answers account for much of the difference in time to finish.

The 39.7K session is a timing and speculative-yield check. All five native responses and four of the five Python responses exhausted their output budgets. The separate runnable suite provides the executable correctness check.

## Equivalent precision

| Weights or state | Production Python and matched Zig |
| --- | --- |
| Routed experts | The checkpoint’s 4 bpw EXL3 codes and scales |
| Target dense projections, including shared experts | Affine Q4 per 64 input columns, 16 clipping candidates scored by squared error, BF16 scale and bias |
| Target vocabulary projection | FP8 E4M3 per 128 input columns with FP32 scale |
| Absorbed MLA kv_b, router and embeddings | BF16 source weights |
| DFlash2 linear projections and vocabulary head in the final profile | Min–max affine Q4 per 64 inputs with BF16 scale and bias, including the five MLP down projections. Both engines retain the original BF16 embeddings, normalization weights, convolution bases and selector weights. |
| Latent KV and pooled index keys | E4M3 with one power-of-two FP32 scale per row |
| KDA recurrence and convolution history | FP32 recurrence, BF16 convolution history |

The original Zig DFlash2 loader stored five MLP down projections in BF16. Production Python quantizes these matrices to Q4 and sums unrounded FP32 partial results across the two ranks. The revised native loader matched every packed word, scale and bias across all ten 4,096 × 6,144 rank-local tensors. Its Q4 matmul gave bit-identical FP32 outputs when supplied the same input group sums. End-to-end partials differed by at most 3.82 × 10⁻⁶ across 30 measurements because the two engines sum inputs in a different order. The five Q4 matrices occupy 67.5 MiB per rank, down from 240 MiB in BF16.

Mia’s native numeric settings `TF_GLM_DENSE_BITS=4` and `TF_GLM_HEAD_BITS=8` select symmetric integer weights. The matched port adds `q4-mse` and `fp8` selections, plus `TF_GLM_KV_POW2=1` for the Python cache encoding. The runtime uses different physical tiles and padding. Different tensor-core reductions, prompt chunking and speculative windows can still change generated text.

On a Spark, the installed Python quantizer and the CUDA code extracted from the served native binary produced identical codes and metadata on the sampled real head, KDA, MLA and shared-expert weight slices. Synthetic cases also exercised tails, row bands and split-K matmuls. The cache check compared contiguous and permuted physical pages, scales and guard bytes. Both native score readers returned identical values on packed keys and their BF16-decoded equivalent. Their maximum absolute difference from Python’s score was 1.20 × 10⁻⁷ in the device check.

The engines place the DFlash2 attention weights differently. Python splits them across the two GPUs and combines each output projection’s rank-local FP32 sums. Zig replicates those weights and performs the output projection locally. Splitting the five layers would save 67.5 MiB of weights per rank and add five serial collectives per speculative block. In the native trace, the first existing drafter all-gather averaged 318 µs and the other five averaged 14–38 µs each. The net effect of sharding attention would depend on when the ranks reached each additional collective. Their selector accumulation and rounding also differ. The target verifies all proposed tokens.

## Workload and rates

The long replay seeds a fresh 262,309-token synthetic project history with 420 tool exchanges. The eight measured requests include three 768-token test-writing replies, a code review that must finish naturally, and four tool-result follow-ups that must finish with their file markers. Frozen tool history keeps the next request identical even when engines generate different answers. A second pass changes the system prefix to exercise a fresh cache. An independent 40K-token history exercises Go and Python code generation, a concurrency review and two code questions. The output budget can truncate these answers.

End-to-end throughput divides the sum of generated tokens by summed elapsed request time. After-first-token throughput subtracts one token and the client time to first streamed content or reasoning from each request. Both count generated reasoning tokens. The cold seed is reported separately. The fixed-length writing cohort controls output length, and the full-session cohort includes the cost of each resumed turn. Each recorded stream has its request and response hashes, finish reason, final usage, timestamps and before/after counters.

The replay archive on the Spark head is `~/sparkrun/results/zig-equivalent-wg1omxkn/coding-replay.tar.gz`, SHA-256 `db381e5ff9f733967f3335d86f32ce7c33a943995e216fa93617f84fe506a505`. Extract it on the client, point it at the running service, and give each pass a new output directory. The three suite names are `canonical`, `repeat` and `held-out`.

```sh
python3 replay-client/run.py --suite canonical --endpoint "$ENDPOINT" --output "$RESULTS/canonical"
```

## Building and running the candidate

The [coding recipe](../mia-tensorfold-glm53-native-tp2/glm53-mia-1104-6c55c20-coding-ranges.yaml) pins the tested arm64 image by its config digest. The primary build supplies the matched target and KV kernels. The coding build adds the two recompiled CUDA modules and the Zig dispatch changes. Both reuse the local tensor-core MLA parent. From the reviewed Mia mod directory, build them in sequence.

```sh
python3 prepare.py --check
python3 python-quant/build.py
python3 python-quant/build.py --variant python-quant-ranges/variant.json
```

A cold rebuild from the archived kit passed all 120 Linux arm64 host tests and reproduced the range-enabled executable, OCI config and platform manifest. The executable SHA-256 is `859ab47402aeeeef95a65af67a13209c96dd9fbef1bf0bc7e9dd876a99a7b7a2`. The builder accepts a verified Mia kit archive via `--kit-archive`. It checks the installed executable, layer contents, arm64 manifest and configuration against the release records. Load the resulting image on both Sparks. Use the recipe with the normal two-node SparkRun launch from the recipes directory.

```sh
sparkrun run mia-tensorfold-glm53-native-tp2/glm53-mia-1104-6c55c20-coding-ranges.yaml --cluster sparks --no-auto-detect --no-sync-tuning
```

## Incremental results on the 262K coding session

These eight warm turns use the same request bytes on every profile. The early Zig rows match the target and KV formats and retain the original BF16 drafter down projections. The first row is the production reference. For perspective, the same eight request bodies on stock Mia Zig delivered 45.84 end to end and 55.21 tok/s after the first token. Our previous tuned Zig profile reached 50.44 and 61.03. Both historical profiles used their original numeric formats.

| Profile | End-to-end output tok/s | After-first-token output tok/s | Test writing after first token | Net tokens per verification round | ms/round |
| --- | ---: | ---: | ---: | ---: | ---: |
| Python durable | 53.64 | 66.68 | 68.61 | 4.254 | 63.80 |
| Zig, matched target and KV | 50.01 | 59.40 | 61.31 | 3.992 | 67.21 |
| + calibrated greedy DFlash policy | 50.49 | 60.13 | 62.28 | 4.067 | 67.63 |
| + eight-/sixteen-token copy matching | 52.02 | 62.57 | 64.90 | 4.338 | 69.33 |
| + omit the unused target MTP block | 52.11 | 62.35 | 64.82 | 4.338 | 69.58 |
| + tiled index scoring for verification | 53.48 | 64.57 | 67.35 | 4.338 | 67.19 |
| + wider sparse MLA tile and Q4 drafter down projections | 54.25 | 65.57 | 68.34 | 4.307 | 65.69 |
| + specialized routed EXL3 expert kernels | 55.92 | 67.97 | 70.62 | 4.307 | 63.37 |
| + schedule shared expert work earlier for selected verification widths | 56.15 | 68.22 | 70.87 | 4.307 | 63.14 |
| + select sparse attention tiles by measured context and width | 55.75 | 68.21 | 70.62 | 4.307 | 63.15 |

The profile through Q4 drafter downs gained 4.25% end to end and 1.93% after the first token over production Python on the original replay. Test writing gained 2.92% after the first token across three replies of exactly 768 output tokens. The completed review gained 1.92%, while the four short tool replies streamed 4.77% slower. Both engines returned all four required tool markers. The one-token cold seed took 180.20 s on Zig and 138.07 s on Python.

A native control matched the production target, head, KV and all five Q4 drafter down projections while retaining the previous decode policy and kernels. It reached 50.20 end to end and 60.52 tok/s after the first token. Enabling the tuned features on that same binary reached 55.92 and 67.97; the range-enabled recipe reached 55.75 and 68.21, an 11.04% end-to-end and 12.69% streaming increase over the format-matched control.

A second precision control kept target dense weights and the output head in BF16 in both engines. Both retained EXL3 routed experts, the Q4 DFlash2 model and power-of-two FP8 KV. Across the same eight canonical turns, Zig reached 42.49 end to end and 49.27 tok/s after the first token, against 38.46 and 45.15 for Python. The three fixed-length writing replies streamed at 52.15 vs 47.44 tok/s. The production-format recipe remains faster in absolute terms.

With all five drafter downs in Q4, the three 128-token requests with drafting disabled streamed at 31.39 tok/s on Zig and 32.94 on Python. The speculative result therefore depends on both the cost of running each target verification window and how many proposed tokens it accepts.

Earlier shared-expert work saved 0.24 ms per round on the original session and 0.48 ms on the 39.7K session. Changing the tile thresholds then moved the 39.7K rate from 56.46 to 56.48 tok/s. All eight canonical replies and all five shorter-context replies retained the same output and speculative counts across those two dispatch changes. Their 29,281-token target score also matched, with mean negative log-likelihood 1.854203.

On the shorter history, lowering the DFlash chain threshold from 0.3 to 0.2 reduced verification rounds from 913 to 867. The wider windows made each round more expensive, moving aggregate output speed only from 56.48 to 56.54 tok/s. Go generation improved from 51.93 to 53.05 tok/s and Python generation fell from 57.97 to 56.54. The qualified recipe uses 0.3.

With the same Q4 drafter, adding the routed EXL3 kernels alone lifted streaming speed from 65.57 to 67.97 tok/s (+3.65%). Verification fell from 65.69 to 63.37 ms per round. All eight replies retained identical token IDs and round counts.

The copy policy improved tokens per round. Once it was active, the Zig target produced more tokens per round than Python on these requests. Tiled scoring then cut 2.39 ms from each round on average. Removing the unused target MTP block reduced model residency and changed measured throughput by less than 0.4%.

## Where each verification round spends its time

A CUDA graph-node trace on rank 0 covered 40 complete neural verification and drafting rounds of a 160-token writing request. The input reused the warm 262K prefix. This trace uses the matched target and KV image with calibrated greedy drafting, before the copy and kernel changes. It contains 89,227 kernel events. The mean round took 61.17 ms, with 56.58 ms between target embedding and the next draft embedding, including commit and tap ingestion, and 4.58 ms from draft embedding to the next target pass. Exclusive timings attribute each interval once, including time when kernels on different streams overlapped.

| Target work per round | Mean time |
| --- | ---: |
| Routed EXL3 expert kernels and their projections | 30.04 ms |
| Dense projections and output head, including MLA absorb/expand | 12.31 ms |
| TP2 collectives | 3.26 ms |
| Sparse attention indexing | 3.24 ms |
| Hyper-connections | 2.76 ms |
| KDA and convolution | 1.84 ms |
| All other target work and idle intervals | 3.13 ms |

The copy-draft change uses Python’s eight-token prompt match and sixteen-token reply match, and can verify up to fifteen copied tokens alongside the pending token. Zig suppresses proposals temporarily after repeated complete rejections, and Python pads certain windows to sixteen rows for its CUDA graphs. Both verify each proposed token on the target.

The native host issued an average of 1,849 kernel launches per observed neural round. The CUDA launch API calls occupied 21.55 ms on the host thread, overlapping device execution. Only 0.57 ms per round had no kernel executing on any of the three observed streams. This limits the time a graph could recover solely by closing idle submission gaps.

A second trace recorded the deployed Python engine on the same writing request. Filtering unrelated prompt work and weighting both engines to the same physical verification widths (2, 3, 4, 5 and 8 rows) gave 60.15 ms per native round and 54.49 ms per Python round. Target verification through commit and tap ingestion accounted for 55.57 vs 49.92 ms. DFlash2 through the next verification accounted for 4.58 vs 4.57 ms. The native trace predates the routed-expert and sparse-index changes. In that comparison, native vs Python spent 3.29 vs 1.34 ms on sparse indexing, 28.87 vs 27.41 ms on routed experts, 2.75 vs 1.42 ms on hyper-connections, and 3.21 vs 3.28 ms on TP collectives. Each engine was profiled on one stream of requests, and Python used graph replay while Zig submitted individual kernels. The unprofiled client replays provide the throughput measurements.

Both engines launched 90 hyper-connection partial kernels per target pass. At three rows, the in-service mean kernel duration was 18.41 µs on Zig and 5.93 µs on Python. The same two kernels run in isolation across 64 weight matrices took 5.51 and 6.72 µs respectively, with identical partial results. The native trace also showed more overlap with prefetch work at this width. A direct kernel replacement did not explain the in-service gap. Prefetch scheduling and cache residency remain plausible causes, with no hardware cache-counter measurement to distinguish them.

The expert cost grew with the number of tokens verified together. It took 17.72 ms for two verified rows, 30.72 ms for four, and 55.73 ms for eight. Those means cover nine, eleven and five rounds respectively. Full-round timings include drafting and ranged from 46.26 ms at two rows to 90.53 ms at eight. A copy that cheaply supplies extra proposals also changes how many of these rows run on the target.

| GPU check | Result |
| --- | --- |
| Sparse index scores on packed FP8 keys with the served 11-layer page stride | At 262K, reusing each key tile across verification rows sped up the scorer by 1.22× at two rows, 2.42× at four, 3.51× at eight and 5.23× at sixteen. Scores matched at every tested position, including a separate 1,048,064-token test where the two, eight and sixteen-row speedups were 1.62×, 4.91× and 6.11×. |
| Sparse MLA head tile | On the 262K fixture, changing from 16 to 32 heads per tile took 268.2 → 163.6 µs for eight rows. Two and four rows were slower on the wider tile. The initial wider-tile dispatch starts at eight. The range extension starts at six, with a 4,096-token minimum on the qualified path. Output values and intermediate partials were bit-identical. |
| Real EXL3 expert layer, TP rank 0 | At two, four, eight and sixteen rows, the original → specialized times were 439.1 → 422.8, 838.2 → 818.6, 1,560.8 → 1,533.8 and 2,815.1 → 2,736.0 µs. Three rows showed little change (653.3 → 652.2 µs). Using the native nine-slot router and its arena layout, 13,369,344 FP32 routed values matched an independent reference after output and scratch buffers were poisoned before each checked call. Negative controls rejected skipped writes. Timings used separate unpoisoned graph replays. |
| Full FP8 output head at eight rows | Native 1.56 ms, Python 1.71 ms. Changing Zig’s FP8 conversion in this kernel made less than a 0.1% difference. |

Moving the shared expert ahead of routed expert work reduced time per layer for two-, three-, four- and six-row windows with the specialized EXL3 kernels on a real Spark. A separate experiment prefetching the following attention projections added 12–28 µs per layer and was discarded.

## Service and benchmark records

Both Sparks returned to the original production image with the 2,582,528-token pool and 64 GiB disk spill. Replaying a 111,952-token prompt restored 111,936 tokens from disk and returned the expected marker in 0.49 s to first token. SparkRun passed 354 tests and 34 subtests, with six skips. All eight dependency locks and fifteen recipes validated.

The Spark head holds the client streams, request hashes, per-turn metrics, code checks and matched-width trace summary in `~/sparkrun/results/zig-equivalent-wg1omxkn/timing-3ad27f5c5ffd.tar.gz`, SHA-256 `3ad27f5c5ffd2fc21232c2dc13074d7160f0a78dc900561e9355f8789c1bc565`. The raw Nsight captures are in the neighboring `zig-policy-trace` and `py-target-trace` directories.
