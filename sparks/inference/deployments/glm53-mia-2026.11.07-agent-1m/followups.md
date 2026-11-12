# GLM qualification follow-ups — November 7–8, 2026

Keep the Python q4-dense/FP8-head P4 profile. The native release's large-first-save and near-window cache-capture failures remain unresolved. The subsequent screens support message-start cache reuse as the next engine question, with an explicit cold-prefill and retained-state cost. The full fixed-work replay campaign remains on HOLD.

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

After all three November 8 windows (cut, served performance and kept-state resume), retained `LAUNCH_VERIFIED`, `RESTORED_P4` and explicit `verify_state.check(snapshot, 'P4')` results agree: both ranks are back on Python q4, strict idle, four slots/1M context, the full 2,582,528-token pool and the isolated `/cache/huggingface/tensorfold-spill-rerun-20261107` block-spill store. The original retained production spill was not used by the tests. The operating thread reported exact-ID development-container cleanup, free GPUs/ports before restore, and each monitor exiting zero. Retained telemetry has no new kernel warnings and no window contains ALERT/INCIDENT files. The coordinator independently reran the saved-snapshot checks locally, without host access.

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

Keep Python and the native release's rejected status. The upstream request-start result supports further fork work on cache-resume granularity, but its cold cost, cache pressure, concurrency exclusions and disk-learning refusal must be resolved before production qualification. The fork thread owns engine changes, local development images and announced test windows; home-lab retains the production recipe, spill preservation, rollback and consolidated reporting. No recipe, engine, image pin or production setting changes in this closeout. The fixed-work replay source remains available as a diagnostic, with its hardware campaign still on HOLD.
