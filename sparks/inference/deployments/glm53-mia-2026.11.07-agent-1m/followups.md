# GLM qualification follow-ups — November 7–8, 2026

The original receipt and experimental verdict in this report end with the FP8 kept-state resume window on November 8. The [November 15 replay decision](#november-15-replay-decision) separately reconciles later fork receipts without extending that sealed qualification. The restore checks below establish the recorded returns, not the current live recipe; see the [operational notes](../../sparkrun/README.md) for the November 14 check.

The November 8 decision was to keep Python q4 dense/FP8 head P4. The native release's large-first-save and near-window cache-capture failures remained unresolved. The subsequent screens supported investigating message-start cache reuse, with an explicit cold-prefill and retained-state cost, while holding the full fixed-work replay campaign. The later [decision for #1497](#november-15-replay-decision) keeps Python and declines to resume that campaign for the recorded candidates; it preserves the independent native engineering and release work.

The [original controlled comparison](comparison.md) records the November 7 release experiment. The [follow-up receipt](followups.json) records the later screens, separately scoped upstream experiments, recalculated latency metrics, selected input hashes and local checks of the three November 8 Python-restoration snapshots. These are different engine/configuration comparisons; their rates must not be pooled into one benchmark.

## Workload relevance

Existing head-server metrics cover October 28 through November 7 at 17:52:25 UTC, before the initial trial: 917,495 observed seconds with 98.83% one-second coverage. More than one request was active for 6.06% of observed wall time and 27.79% of busy time. TP ranks were not summed. Exact arrival overlap and simultaneous kernel execution cannot be recovered from these gauges.

The separate local Pi metadata scope has 10,423 positive-usage replies: median recorded full context 261,922 tokens, median uncached input 729 tokens, and median cache-read share 99.665%. Tool-result text has median 584 UTF-8 bytes; no token conversion was inferred. The saved input normalization, session coverage and one-second sampling limitations remain explicit in the retained workload report. About 75% of these recorded contexts exceed the replay harness's 128K diagnostic maximum. Concurrency matters during busy periods, while a typical recorded request reuses a large history and adds relatively little input.

## Native q8 sharding screen

The unmodified Mia release ran sharding OFF then ON, one process start each, with the same 75 decode bodies per arm, four configured slots, 1M context, q8 dense/head, FP8 KV, DFlash2 and a 6 GiB prompt cache. All 150 requests generated 768 tokens. Delivered token hashes matched in 75/75 pairs, and sharding-on owner counters established activation.

| Arm | Unique logical pool tokens | Python's 2,582,528-token floor | Source-derived KV bytes/rank |
| --- | ---: | --- | ---: |
| Sharding OFF | 2,211,840 | Below | 14,111,539,200 |
| Sharding ON | 3,906,560 | Above | 13,839,941,632 |

OFF was 1.40–2.73% faster across the six workload/concurrency cells, with a 2.37% aggregate advantage over the complete workload's cohort walls. It also lost required live capacity. Physical bytes are derived from source geometry; the automatically sized pools were not capacity matched. Fixed arm order and one startup per arm do not establish a causal kernel cost or long-context performance. This result does not justify disabling sharding in production or resuming the full replay campaign.

## Python FP8 dense versus affine q4

The Python image, checkpoint, DFlash2, FP8 target head, FP8 KV and all other intended settings stayed fixed. The order was q4 → FP8 dense → q4 repeat. Each decode cell contains five cohorts of fresh-prefix paired prompts, with 768 generated tokens per request. Four-stream values are aggregate end-to-end throughput.

| Workload | q4 tok/s | FP8 tok/s | q4 repeat tok/s |
| --- | ---: | ---: | ---: |
| List prose, one stream | 48.05 | 41.12 | 47.61 |
| List prose, four streams | 87.50 | 83.59 | 87.06 |
| Code, one stream | 67.02 | 54.03 | 66.67 |
| Code, four streams | 110.87 | 101.97 | 112.46 |
| Sampled prose, one stream | 40.12 | 34.30 | 40.13 |
| Sampled prose, four streams | 74.63 | 66.97 | 74.89 |

FP8 dense was 4.5–19.4% slower in these decode cells. Cold-prefill TTFT at approximately 6K, 27K and 112K was 3.44/13.05/54.25 seconds for q4 and 3.90/14.94/62.08 seconds for FP8, with three repetitions per length. Restored-q4 decode medians moved −0.9% to +1.4%; all 75 delivered replies matched the initial q4 run.

FP8's automatic pool was 2,308,096 tokens, below the unchanged 2,582,528-token q4 pool. A recorded test-only admission amendment allowed that measurement arm; it was not production adoption. Both requested the same 12.5 GiB prompt cache and 14.5 GiB memory reserve. q4 and FP8 each passed 11/12 strict smoke checks and 10/10 seeded GSM8K items. HumanEval diagnostics were 10/10 for q4 and 8/10 for FP8: one FP8 miss was strict code-wrapper formatting and one was a parity error. These tiny samples do not rank general model quality. Comparable Python prompt NLL remains unsupported. This experiment changed dense precision; it did not test a head-only change or native integer q8.

## Upstream arbitrary-cut correctness

The fork thread tested commit `3ed491eed30f60dace08fe8269c30381fc7d0242`: upstream main `3ace0f66e` plus [upstream #2404](https://github.com/ashhart/TensorFold/pull/2404) and its `glm-cut-check` command. The local development image was `tensorfold-dev:3ed491eed30f`, config `sha256:d9dfbb788404e5980bfb6e5de58c2b2260893d46c91f53a0721f7904f1b9a901` on both Sparks. No Mia engine patches were included.

The reported 160/160 patterns, across synthetic, Pi-shaped and prose inputs, kept the global first token and rank-0 vocabulary-shard logits byte-identical to the 2,048-row grid through 32 teacher-forced continuation tokens. Prompts ranged from 5K to 30K tokens, with native q8 and native q4 dense, q8 head, contiguous FP8 KV, target MTP and no DFlash2 drafter. The subsequent adversarial review identified reused-slot contamination and the absence of a kept-state resume: a skipped cache write could read correct leftovers from the previous run. Logical state equality was also not established. The v1 result therefore remains a reused-slot output check; v2 below addresses those coverage gaps. Neither result qualifies Mia's 4,096-row, paged, sharded, absorb-MMA or DFlash2 paths.

## Upstream kept-state resume correctness (v2)

Commit `f5eaf02d0ea0203c85abb44aab9386f226fe72aa`, image `tensorfold-dev:f5eaf02d0ea0`, config `sha256:4c9e40ee5e34d367e35295c2743afbab3e2e1736e9e5353a6da554dd157ae07b` on both ranks, ran `glm-cut-resume`. Both recorded containers exited zero. The coordinator recounted 30 exact comparisons on each rank, 60 total, including every rank's own vocabulary shard and canonical committed-state hashes.

Three Pi-shaped history/extension pairs (5,001+203, 12,003+602 and 30,002+1,101 tokens) cover extended-prompt pool residues 0, 1 and 3. Three cut patterns use the history end alone, arbitrary message-like starts, and 64-row-aligned starts. The index split is 2,048, matching the served default. Each sequence is freshly allocated/refilled with distinct finite byte patterns, so the grid repeat checks whether any unwritten arena bytes influence the observations.

- G2 = G1: two fresh grid passes agree under different initial fill bytes.
- F = G1: a fresh cut pass agrees with the fresh grid pass.
- R = F and R = G1: a state kept by a mark at the history boundary, then resumed into another fresh sequence after an intervening conversation, agrees with both fresh references.

The comparisons cover first/verified tokens, MTP drafts, prompt and continuation logits, the second draft step's MTP logits, and canonical state immediately after prefill and after the continuation. The 48-ID continuation tape uses verification widths 1–8 with partial and whole keeps. State checks include KDA convolution/current recurrent copies, latent rows and FP8 scales, completed pooled rows, device cursors, the held prompt row and both index rings aligned by absolute position over their overlap. No observed logit was non-finite. Canonical state is compared through hashes rather than raw scratch/layout bytes; differently based rings agreed over their shared retained positions.

This closes the tested upstream reused-slot, both-rank, partial-pool and kept/resumed-state gaps. It remains one token source and one precision (dense/head 8/8), TP2, contiguous FP8 KV, target MTP, extended prompts up to 31,103 tokens and no DFlash2. Paired/solo prompt steps, paged/sharded KV, disk persistence, larger contexts and Mia's kernels remain outside this test. The fork thread reports two source reviews before the run with initial blockers fixed; the coordinator checked retained source/logs and restoration without rerunning hardware or claiming a new independent review.

## Upstream served message-start experiment

The same development image served q8 dense/head with DFlash2, contiguous FP8 KV, context 393,216, parallelism 2, a 6 GiB prompt cache and the deployed template. The arm order was `TF_GLM_REQUEST_STARTS` OFF → ON → OFF, one server start per arm. There was no disk learning or spill writing. #2404 refuses `--learn-conversations`, which remains a production requirement.

Median seconds follow. Answer TTFT is the first nonempty content/reasoning delta, eight answers per loop/arm. Tool-only replies have no such TTFT; their row reports whole-reply completion latency for seven calls after the initial request. The 242K-history follow-up row has eight text requests. Cold prefill is one request per arm.

| Metric | OFF | ON | OFF repeat |
| --- | ---: | ---: | ---: |
| Small loop: answer TTFT | 1.566 | 0.544 | 1.660 |
| Small loop: tool-only completed reply | 1.741 | 0.765 | 1.846 |
| File loop: answer TTFT | 1.763 | 1.239 | 1.844 |
| File loop: tool-only completed reply | 2.360 | 1.091 | 2.511 |
| 242,026-token history: follow-up TTFT | 1.641 | 0.831 | 1.549 |
| Same history: cold TTFT | 213.934 | 236.655 | 213.994 |

Message-start reuse improved answer TTFT in these loops while cold prefill cost 10.62% more. The repeated OFF arm moved −5.57% to +5.96% on the three answer/follow-up medians and 0.028% on cold TTFT. There is one process start per arm; these are descriptive controls rather than a multi-start confidence interval. The roughly 1,200-message cold fixture is one request shape, not a universal 242K penalty.

The supplied analyzer's headline small/file-loop numbers (1.67→0.65 and 1.91→1.16 seconds) combine content TTFT with whole tool-only reply latency. That legacy mixed proxy is retained in the JSON under an explicit label; the table separates the two measurements. The coordinator recalculated all medians from the saved client records.

Content, reasoning, tool names and exact argument strings matched for 32/32 assistant-message pairs across all three arms after excluding generated tool-call IDs. Original call IDs were preserved within each actual tool loop. The nine long-history request hashes matched across arms. This does not assert identical complete API envelopes. ON ended at 6,130/6,144 MiB cache use and 22 evictions; both OFF arms ended at 4,282 MiB and zero evictions. More retained message starts have a real storage cost. Four concurrent streams and cut-disabled paired/batched prompt paths were not exercised. Python was not remeasured in this window, so no direct Python/native speed conclusion follows from it.

## Restore verification and command correction

After each of the three recorded November 8 windows (cut, served performance and kept-state resume), retained `LAUNCH_VERIFIED`, `RESTORED_P4` and explicit `verify_state.check(snapshot, 'P4')` results agree: both ranks had returned to Python q4, strict idle, four slots/1M context, the full 2,582,528-token pool and the isolated `/cache/huggingface/tensorfold-spill-rerun-20261107` block-spill store. The original retained production spill was not used by those tests. The operating thread reported exact-ID development-container cleanup, free GPUs/ports before restore, and each monitor exiting zero. Retained telemetry has no new kernel warnings and none of those windows contains ALERT/INCIDENT files. The coordinator independently reran the saved-snapshot checks locally, without host access.

The previous handover command `python3 verify_state.py --profile P4 --snapshot …` was invalid verification evidence: that module defines `check()` but has no entry point and silently ignores CLI arguments. The controller's actual launch/restore path imports and calls `check()` before publishing `LAUNCH_VERIFIED`; it was not relying on the no-op CLI. Five earlier/current snapshots passed retrospective checks, and wrong pool/image/rank/precision controls were rejected. Historical sealed inputs and receipts remain unchanged. The retained audit records the faulty invocation and the scope of the recheck; a zero exit status or empty redirected file alone is never an acceptance receipt.

For that retained module, invoke the function explicitly and require successful JSON output:

```sh
python3 -c 'import json,sys; sys.path.insert(0,sys.argv[1]); from verify_state import check; print(json.dumps(check(json.load(open(sys.argv[2])), "P4"),indent=2))' "$prep/scripts" "$snapshot"
```

## Evidence locations and decision

Raw files remain outside Git, with selected source hashes in [followups.json](followups.json):

- Workload aggregates: `/Users/kelchm/.cache/glm53-workload-screen-20261107/workload-report.md` and `production-metrics/`.
- Served sharding: `/Users/kelchm/.cache/glm53-workload-screen-20261107/runs/attempt-20261107-shard-screen-01/`.
- Python precision: `/Users/kelchm/.cache/glm53-python-fp8-screen-20261107/runs/attempt-01/`.
- Upstream cut check: `/Users/kelchm/.cache/glm-cut-check/RESULTS.md` and `runs/`.
- Upstream served experiment: `/Users/kelchm/.cache/glm-cut-check/perf/RESULTS.md` and `perf/runs/`.
- Python lifecycle: `/Users/kelchm/.cache/glm53-python-fp8-screen-20261107/runs/{cutcheck-20261108-01,perf-20261108-01,resume-20261108-01}/`.
- Verification audit: `/Users/kelchm/.cache/glm53-verify-state-audit-20261108.json`.

Keep Python and the native release's rejected status. The upstream request-start result supports further fork work on cache-resume granularity, but its cold cost, cache pressure, concurrency exclusions and disk-learning refusal must be resolved before production qualification. The fork thread owns engine changes, local development images and announced test windows; home-lab retains the production recipe, spill preservation, rollback and consolidated reporting. No recipe, engine, image pin or production setting changes in this closeout. The November 8 decision left the fixed-work replay hardware campaign on HOLD, retaining its source as a diagnostic. The [November 15 decision](#november-15-replay-decision) below resolves whether to resume it for the recorded candidates.

## November 14 record verification

The closeout audit verified all 509 initial-trial and 4,515 rerun evidence files against their recorded manifest hashes, sizes and member hashes. All three recorded November 8 restore snapshots passed the real `verify_state.check(snapshot, "P4")` function again. Matching raw q8 resume logs retain 30 exact comparisons per rank, and the saved served-client records reproduce the latency table above.

Of the 133 selected follow-up input paths, 132 still match their recorded hashes and none is missing. The mutable derived `/Users/kelchm/.cache/glm-cut-check/RESULTS.md` was revised for later BF16 work: its recorded SHA-256 is `a646099c35e3012156e8e4eee9e52fb499f7475cb705c5cfd2479e5f05950217`, while the November 14 file hashes to `68afa30d06a2edb100d6d71a0837a7d5788bd399d0587b236899fc77946f0346`. The original report bytes were not recovered. The receipt keeps its original hash; the unchanged raw q8 logs support the narrower claims recorded here. Later additions to that report are outside this qualification record.

## November 15 replay decision

Conclude [#1497](https://github.com/kelchm/home-lab/issues/1497) with **Python retained, no resumption of the held replay campaign for the recorded native candidates, and no production adoption of message-start reuse**. Later evidence already establishes a warm-turn benefit and blocking durability/capacity limits; another broad comparison of these configurations would not resolve their known failures. This completes the present replay/reuse investigation. It does not reject future native releases or stop the TensorFold fork's engineering. Retain the diagnostic harness and receipts, and carry exact-release integration/qualification in [#847](https://github.com/kelchm/home-lab/issues/847) and durable image correctness in [#1285](https://github.com/kelchm/home-lab/issues/1285).

### Keep the engine and release identities separate

| Evidence | Engine and configuration | What it qualifies |
| --- | --- | --- |
| November 7 Mia release and controlled rerun | Kit `13f2464`, engine `e52eb42b`, published image `0956a184…`; native q8/q8 and native q4/q8 arms, FP8 pages and KV sharding, four configured 1M streams | The [comparison](comparison.md) and original receipts reject this release on the recorded large-save and near-window capture gates, while preserving its successful incremental and smaller text/image restores. |
| November 8 upstream resume and served message-start checks | `f5eaf02d`, local image config `4c9e40ee…`; q8/q8, contiguous FP8 KV, two streams, no disk learning | The earlier sections qualify their bounded cut/resume checks and served comparisons only. |
| Later W4–W8 fork receipts, November 10–11 | [Deploy4 `01b5396a4`](https://github.com/kelchm/TensorFold/commit/01b5396a4d3d023128ad77abf7e6f0dbdc8c5d12), upstream `fcf85f7a` plus 45 carries; local `tensorfold-dev:01b5396a4d3d`, recorded image config `582ec23f…` | Development-engine measurements, separately described below. They are not a repeat or acceptance of the rejected Mia release. |

The `glm53-kelchm` lane was added through [spark-inference #5](https://github.com/kelchm/spark-inference/pull/5). The [recipe at the November 15 main snapshot](https://github.com/kelchm/spark-inference/blob/334d158a0f989ff93cc3aa3084b27d254dd1e13d/recipes/glm53-kelchm/recipe.json) still pins `01b5396a4` and records all 45 carries; that tree contains no `glm53-kelchm` release manifest. The fork's findings report a separately published CI image, but these runtime receipts identify the development image. A shared engine commit does not transfer hardware qualification to the CI artifact or establish an accepted release. The findings' temporary statement that Python was not restored predates the November 14 operational check and does not describe the current service.

### What the later receipts answer

W4 used q4 dense/q8 head, a four-bit DFlash2 drafter, FP8 windows, two configured streams at 1M context and 16 GiB requested prompt cache. The launch chain selects 4,096-row solo/paired prompt work and enables `TF_GLM_HC_FUSE`, `TF_GLM_SHARED_SLOT`, `TF_GLM_MM_FAST4`, `TF_GLM_ROUTER_FUSE` and `TF_GLM_ABSORB_MMA`, with `TF_GLM_SHARED_BUDGETS=0`. W5 changes to BF16 pages and four configured streams; W8 instead retains two FP8 streams and enables `--prompt-cache-disk`. These configurations and the earlier Mia/Python rates must not be pooled into one benchmark.

| Question | Reconciled result | Limit |
| --- | --- | --- |
| Does message-start reuse reduce warm latency on the later lane? | W4's 185,976-token, 768-output-token answer resumed in 0.425–0.481 s with cuts, against 1.239–1.314 s without. Reuse rose from 184,320 to 185,907 tokens. All six answer hashes agree; all nine teacher-forced follow-up request hashes match across arms. | One start per arm, one cold answer and two repeats. Cold TTFT rose from 92.515 to 122.575 s, **32.49%**, and warm decode used 263 rather than 257 rounds. The clients force temperature 0 and thinking/effort `none`; Pi-shaped history is not acceptance of actual max-effort Pi turns. |
| Does the later disk tier recover a saved long conversation? | W8's restart read 184,320 tokens from disk and answered the 186,102-token request in 3.555 s. The log records 1,583 MiB read in 2,261 ms, with SIGTERM/drained markers before the restart. | This is a two-stream, cuts-off text test. It does not establish image reuse, crash/power-loss safety or combined message-start/disk behavior. |
| Can a skipped oversized save catch up? | W8 logged `StagingFull` at 309,248, 479,232 and 481,280 tokens. After restart the 481,009-token request restored only 184,320 tokens and recomputed 296,689, taking 172.113 s; subsequent saves still skipped. This answers the [Mia rerun's explicitly unanswered catch-up question](comparison.md#incremental-durability-on-n8) for the later lane, without rewriting the Mia result. | Exact source retains the 1 GiB staging default, and CUDA disk attachment does not set `longest` to widen it. The observed persistent gap is a requirement to fix and retest, not evidence that every incremental save fails or that the same threshold applies to Mia's different layout. |
| Do four configured BF16 streams settle production capacity? | The BF16 startup accepted four 1M-configured streams with 2,052 shared pages × 512 rows = **1,050,624 pool tokens**, plus 10.2 GiB prompt cache. W5 completed the saved 186K and 481K histories. | The shared pool is below Python's historical 2,582,528 nominal-token floor. Startup with four streams does not prove four growing long histories, pool-exhaustion behavior, near-window retained-state admission or equivalent usable capacity. |
| Can the new disk flag avoid the request-start incompatibility? | No. At `01b5396a4`, [`--prompt-cache-disk` sets `learn_conversations=true`](https://github.com/kelchm/TensorFold/blob/01b5396a4d3d023128ad77abf7e6f0dbdc8c5d12/zig/src/server/cli.zig#L444); [`requestStartsAlone` refuses that mode with request starts](https://github.com/kelchm/TensorFold/blob/01b5396a4d3d023128ad77abf7e6f0dbdc8c5d12/zig/src/families/glm/cuda_native.zig#L206). | A renamed CLI option does not supply the missing combined configuration. Keep request starts off for a durable candidate unless the incompatibility is fixed and qualified. |

### Remaining release work and revisit condition

The evidence supports continued native engineering, with concrete work rather than another unconstrained campaign: large first saves and recovery after a skipped save, required retained-history and concurrent live capacity, and correct durable image identity. Message-start reuse remains promising; adopting it also requires correct operation with the selected persistence mode and acceptable cold/cache costs. A native candidate may instead leave the opt-in off. Do not trade away required durability to obtain the measured warm-turn gain.

Revisit release comparison through [#847](https://github.com/kelchm/home-lab/issues/847) when there is a specific digest-pinned candidate and production profile with evidence addressing those gates, including the [#1285](https://github.com/kelchm/home-lab/issues/1285) image requirements. Reuse the existing cut and W4–W8 results for what they already establish. Any remaining bounded replay must use matched fixed work, actual Pi effort/template/sampling, representative long histories and busy concurrency, cold/warm latency, cache pressure and correctness, while identifying the exact artifact. Its announced hardware window, monitoring, rollback and adoption decision remain separate operations.

The accepted Python service and required durability/capacity remain the baseline; neither recorded native candidate meets them. A future release needs an explicit artifact, production profile and qualification/rollout decision.

### Reconciliation evidence

The local audit bundle is `/Users/kelchm/Documents/home-lab-validation-20261115/glm-reconciliation/`. It snapshots 81 selected reports, clients, launch chains, raw logs/results and exact-commit source files, with SHA-256/size records in `manifest.json`; `verification.json` contains the offline recalculation and source-check results. A separate `github/` snapshot binds the lane recipe and absence of a release to spark-inference main `334d158`. The original later receipts remain under `/Users/kelchm/.cache/glm-cut-check/perf/`, especially `PRODUCTION-FIT.md`, `FINDINGS-2026-11-10.md` and `gate0/{W4,W5,W8}`. This new snapshot does not invent an original sealed manifest for those later experiments or alter the November 7–8 sealed receipts. Reconciliation ran locally against saved files and source; no image build, model switch, host operation or new inference load ran.
