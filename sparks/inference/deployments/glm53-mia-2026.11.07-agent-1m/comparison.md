# Controlled GLM recipe comparison — November 7, 2026

The owner authorized N8 → N4 → P4 → repeated N8, with a final return to Python. Both service profiles follow Mia’s recipes, with their respective precision defaults. Native remains unqualified. Python was restored and passed readiness on both Sparks at the end. The running profile is `glm53-python-rerun-p4`, from `~/sparkrun/recipes/glm53/mia-tensorfold-glm53-rerun/P4.yaml`, using the isolated `/cache/huggingface/tensorfold-spill-rerun-20261107` store. The original retained production spill was not opened by the rerun.

## Decode

End-to-end aggregate tokens/s, median [minimum–maximum] of five cohorts per cell. Each request generates 768 tokens; four-stream rows include four simultaneous requests per cohort. Unique early prefixes prevent reuse between repetitions, so the spread includes prefix-sensitive generation as well as timing variation; each exact request body is paired across arms. This is bounded generation, not a completed quality task.

| Prompt / concurrency | Native q8 (N8) | Native’s own q4 (N4) | Python affine q4 (P4) | N8 repeat |
| --- | ---: | ---: | ---: | ---: |
| listprose-t0, c1 | 40.16 [37.19–41.20] | 47.24 [44.99–48.12] | 47.41 [44.76–50.83] | 40.00 [36.88–40.99] |
| listprose-t0, c4 | 76.14 [75.12–77.18] | 82.48 [81.50–83.67] | 89.14 [88.63–92.33] | 74.97 [74.57–76.54] |
| code-t0, c1 | 51.52 [50.67–55.52] | 65.58 [54.96–66.37] | 67.17 [60.14–68.52] | 51.50 [50.81–55.67] |
| code-t0, c4 | 93.89 [92.34–95.67] | 102.24 [98.89–110.91] | 111.30 [106.81–114.42] | 93.20 [91.85–95.32] |
| prose-t07, c1 | 33.40 [32.88–33.83] | 40.01 [39.38–40.45] | 40.41 [38.85–43.29] | 33.28 [32.73–33.64] |
| prose-t07, c4 | 65.84 [65.39–66.60] | 71.78 [69.17–72.46] | 75.57 [74.60–76.43] | 65.74 [64.70–66.19] |

## Per-request round mechanics

Per-request round timing, tokens/round and draft acceptance follow. Each cell is median milliseconds/round; median tokens/round; median accepted/drafted percent. The raw CSV retains each request. Medians over the 20 four-stream requests are descriptive; requests within a cohort are correlated. Shorter rounds and different draft acceptance can both change throughput.

| Prompt / concurrency | N8 ms; tokens; acceptance | N4 ms; tokens; acceptance | P4 ms; tokens; acceptance | N8 repeat ms; tokens; acceptance |
| --- | ---: | ---: | ---: | ---: |
| listprose-t0, c1 | 57.15; 2.35; 59.3% | 49.63; 2.38; 62.2% | 45.98; 2.23; 62.9% | 57.49; 2.35; 59.3% |
| listprose-t0, c4 | 109.10; 2.18; 64.4% | 101.68; 2.18; 66.2% | 96.96; 2.30; 63.2% | 109.65; 2.18; 64.2% |
| code-t0, c1 | 67.16; 3.50; 71.1% | 61.71; 4.15; 73.4% | 57.53; 4.10; 75.9% | 66.89; 3.50; 71.1% |
| code-t0, c4 | 134.83; 3.34; 78.0% | 130.60; 3.70; 80.4% | 126.97; 3.77; 75.7% | 136.10; 3.34; 78.0% |
| prose-t07, c1 | 53.79; 1.82; 47.7% | 45.57; 1.83; 51.3% | 45.64; 1.88; 43.5% | 53.98; 1.82; 47.7% |
| prose-t07, c4 | 104.36; 1.76; 53.9% | 96.00; 1.78; 54.7% | 98.85; 1.92; 43.6% | 105.01; 1.76; 53.9% |

## Capacity and repeat control

| Arm | Nominal live-pool positions | Configured device cache |
| --- | ---: | ---: |
| N8 | 3,903,488 | 6 GiB |
| N4 | 4,194,304 | 6 GiB |
| P4 | 2,582,528 | 12.5 GiB |
| N8 repeat | 3,901,440 | 6 GiB |

Every arm preserves the historical nominal Python pool floor. Allocations have different ownership and accounting; this does not establish four simultaneously resident 1M histories or equal retained-history capacity. Repeated N8 output hashes match all 75 decode and all nine retrieval replies. Decode median throughput changed by −0.03% to −1.54% across boots, smaller than the observed recipe differences.

## Cold prefill

Client TTFT in seconds, median [minimum–maximum] of three repetitions; all accepted rows must report zero cached tokens and the correct planted fact. Rendered counts vary slightly with the per-repetition prefix.

| Approximate prompt | N8 | N4 | P4 | N8 repeat |
| --- | ---: | ---: | ---: | ---: |
| 6,000 | 3.93 [3.93–3.99] | 3.71 [3.71–3.78] | 3.39 [3.36–3.39] | 3.94 [3.94–3.95] |
| 27,000 | 14.83 [14.77–14.83] | 13.86 [13.82–13.86] | 13.07 [13.01–13.08] | 14.85 [14.82–14.88] |
| 112,000 | 61.81 [61.80–61.82] | 57.94 [57.78–58.03] | 54.49 [54.44–54.49] | 61.97 [61.87–62.00] |

## Pi-shaped tool loops

Thinking is low and preserved. Both loops replay complete delivered reasoning and original tool-call IDs, with three-second pauses. Histories naturally diverge as each engine writes its own replies. The larger loop reads eight public source files and requests substantive reviews; it crosses multiple native 4,096-token keep boundaries. Counts include newly read file text, so cached ÷ prompt is coverage, not the hit rate among already reusable tokens.

| Turn | N8 answer TTFT / cached ÷ prompt | P4 answer TTFT / cached ÷ prompt |
| --- | ---: | ---: |
| 1 | 2.284s / 4,096 ÷ 7,980 | 0.930s / 6,912 ÷ 7,980 |
| 2 | 1.209s / 8,192 ÷ 9,680 | 1.055s / 8,512 ÷ 9,587 |
| 3 | 1.987s / 8,192 ÷ 11,303 | 1.113s / 10,112 ÷ 11,191 |
| 4 | 3.134s / 8,192 ÷ 12,974 | 1.092s / 11,776 ÷ 12,790 |
| 5 | 1.584s / 12,288 ÷ 14,644 | 0.912s / 13,376 ÷ 14,401 |
| 6 | 2.446s / 12,288 ÷ 16,294 | 1.032s / 14,976 ÷ 16,009 |
| 7 | 1.154s / 16,384 ÷ 17,968 | 1.113s / 16,576 ÷ 17,662 |
| 8 | 2.024s / 16,384 ÷ 19,680 | 1.090s / 18,240 ÷ 19,306 |

Tool-only calls have no client content/reasoning TTFT in this transport; their full latency and native server TTFT are retained rather than inventing a client TTFT. Native warm-pass outcomes come from the retained `next turn:` log lines: 0 completed /16 stepped aside in the small loop, and 2 completed /14 stepped aside in the larger loop. A stepped-aside log is not by itself proof of busy traffic; a pass with no new 4K keep mark is also cancelled.

## Quality

N4 is symmetric group-64 quantization with one scale and no zero point. P4 is affine group-64 with MSE-searched ranges and an FP8 head. Their similar byte budgets do not make their quality interchangeable. N8 and N4 share a q8 head. All use the same checkpoint revision; its nonexpert weights ship in BF16 and are quantized at load.

| Check | N8 | Native’s own q4 (N4) | P4 |
| --- | ---: | ---: | ---: |
| biology NLL ↓ | 0.522078 | 0.663158 | unsupported |
| computing NLL ↓ | 0.784223 | 0.832972 | unsupported |
| python NLL ↓ | 0.280596 | 0.253299 | unsupported |
| legal NLL ↓ | 2.162789 | 2.253712 | unsupported |
| Token-weighted NLL, 206 scored tokens ↓ | 0.752643 | 0.799965 | unsupported |
| Strict smoke formatting | 10/12 | 11/12 | 11/12 |
| GSM8K seeded diagnostic | 10/10 | 10/10 | 10/10 |
| HumanEval isolated diagnostic | 10/10 | 10/10 | 10/10 |

The twelve smoke checks distinguish exact formatting from semantic correctness. N8’s two strict misses and N4’s one miss contain the correct answer plus unwanted formatting or explanation. The 10-item GSM8K and HumanEval samples use a custom chat wrapper with thinking off; they are diagnostic counts, not full benchmark scores. HumanEval runs in a local CPU container with no network and bounded resources. Python’s serving API does not expose the required prompt likelihood, so it has no comparable NLL here.

For published context, [Mia’s weight card](https://huggingface.co/Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold/blob/76c0b5173166d2795dd48860f45d8224817f894c/README.md) reports Python-q4 as-served KL to Z.ai’s FP8 release of 0.0903 on Wiki and 0.3150 on workload, against FP8-expert + q4-dense floors of 0.0676 and 0.3009. This supports dense quantization as a major contributor to the reported divergence. KL is not additive, and these published numbers neither rank native q4 nor replace our direct N8/P4 answer checks. The KL experiment was not rerun.

## Incremental durability on N8

| Probe | Measured result |
| --- | --- |
| Growth in ~20K steps | Correct through 305,345 prompt tokens; 303,104-token state published with 515 MiB new data |
| Clean restart | Read 303,104 tokens / 2,235 MiB from disk in 1.707s; correct answer |
| Resume latency | 5.749s native server TTFT; 6.337s to the first usable tool reply at the client |
| ~100K jump | 405,504-token state published with 989 MiB new data; later small turns saved through 413,696 |
| Separate cold first saves | 106,496- and 110,592-token saves skipped with `StagingFull` |

The 1 GiB staging charge covers new data in one head-side writer job containing both ranks’ parts. Measured incremental writes put the tested 100K jump just below that budget; the earlier 60–65K estimate was too low. Rounded logs do not establish an exact token threshold. Because the jump saved successfully, this test does not answer whether a conversation remains stuck after a skipped oversized save. The unchanged 6 GiB RAM-cache setting still refused the earlier near-1M 6,264 MiB state. These observations improve the durability picture but do not qualify this configuration.

## Evidence and limits

The raw root is `/Users/kelchm/.cache/glm53-evidence/rerun-20261107T193830Z`. Frozen clients, exact request/SSE bytes, workload hashes, per-request counters, timestamped rank logs, image identities, rails, lifecycle receipts and independent telemetry are retained there. Derived JSON/CSV can be regenerated with the saved scripts. Requests must match byte-for-byte across decode/prefill arms; history-loop requests are deliberately engine-specific. The final sealed raw manifest binds the file set.

The run uses persistent SSH masters with direct fallback disabled. One native restart failed before engine launch because the operator’s SSH config omitted self/fabric aliases; the owner was told, authorized continuation, and the corrected config passed before retry. This is an operator transport incident, not an inference result. The generic transport metadata flags intentional length-limited decode and Prometheus text as incomplete JSON/completion forms; the performance client separately validates their raw SSE completion and text counters. The retained counter checks must match. All operational warnings or qualification failures remain explicit. No engine changes, precision promotion, image rebuild or deployment merge occurred.

## Decision

Keep Python. Native q4 recovers most of the one-stream decode gap, but it is a distinct quantizer and raises native four-passage NLL by0.047322; the tiny task samples cannot settle a quality tradeoff. Native q8 remains slower on these matched workloads, with a small measured repeat drift. Incrementally grown conversations can save and correctly resume beyond300K; the known large-first-save staging and near-window RAM-capture failures still prevent qualification of this unmodified configuration. Earlier image/process-kill evidence remains in [the initial qualification receipt](qualification.json).
