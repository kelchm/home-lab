# GLM-5.3-Flash coding benchmarks on TensorFold 1.3.4

Mia’s 1104 kit remained at `6c55c20` on November 23. It uses TensorFold 1.3.0. The newest upstream release that day was 1.3.4. [Mia’s pinned kit](https://github.com/MiaAI-Lab/GLM-5.3-Flash-DGX-Sparks-1104/tree/6c55c20da31a02d6d381a268fa42f8a9ebe0c5d2) · [TensorFold releases](https://github.com/ashhart/TensorFold/releases)

Each recipe ran across the same two DGX Sparks with tensor parallelism of two. The Mac client timed the requests. Streaming throughput divides all output tokens after the first in each reply by their total generation time after first token. End-to-end throughput divides all output tokens by total request time, including upload and the first-token wait. A one-token seed established each history and was timed separately. [Coding fixtures and measurement definitions](glm53-coding.md#workload-and-rates)

## Confirmed result for long-context coding

On November 24 the selected Zig 1.3.4 profile (`draft-tail-8g-on`) delivered 73.25 output tok/s on six warm 768-token coding replies measured by a client on the Spark head. Durable Python delivered 70.81 and the previously tuned Mia-based Zig recipe delivered 71.07. The profile uses an 8 GiB copied prompt cache with message-boundary checkpoints and the `single_leaf` eviction rule. It also saves up to 640 rows of the external DFlash2 model’s recent attention keys and values in each checkpoint. Restoring this history helps the drafter predict more of the next tokens in each verification round. The target model still checks every proposal.

| Confirmed workload | Durable Python | Tuned Mia-based Zig | Zig 1.3.4 with draft history |
|---|---:|---:|---:|
| Six fixed-length coding replies, Spark client | 70.81 / 67.28 | 71.07 / 66.12 | **73.25 / 71.95** |
| Five warm coding turns, 40K history | **58.64 / 55.67** | 57.07 / 52.07 | 56.99 / 54.40 |
| Eight warm coding turns, 262K history | 66.37 / 53.73 | 68.22 / 53.48 | **70.21 / 55.66** |
| Two new executable requests after a project switch | 63.88 / 52.45 | 71.68 / 60.69 | **71.80 / 63.64** |
| One / four simultaneous short coding requests, aggregate end-to-end tok/s | 64.05 / **110.95** | 63.74 / 97.99 | **64.21** / 98.21 |

The first four rows show output tok/s after the first token / end to end. All three engines used the production weight and KV quantization formats described below. Python led on the 40K workload and at four concurrent streams. On a separate six-request alternating-project replay, the selected profile delivered 52.75 end-to-end tok/s, tuned Mia delivered 53.33 and the 8 GiB fixed-grid Zig profile delivered 53.90. Python delivered 55.81 on the same six requests in the earlier midpoint run. The 8 GiB copied profile with draft-history saving disabled was the fastest on the two new project-return requests at 64.82 end-to-end tok/s.

**The controlled gain comes from reused draft history.** On the same executable and with identical requests, the 8 GiB trial toggled only `TF_GLM_DFLASH_SNAP_CONTEXT`. Both ranks restored draft history on the measured deep hits. At identical target cache depths and output fingerprints, the Spark client needed 968 verification rounds for six replies, down from 1,054 (−8.2%). End-to-end time fell from 67.23 to 64.19 seconds. Reversing launch order on fresh processes reproduced the round counts and reduced time from 66.73 to 64.06 seconds. Across all 31 timed requests, including prompt setup, the saving was 1.1% in the first order and 0.9% in the reverse. The eligibility check at 8 GiB used the three second-pass Mac requests and all six Spark repeats. The 12 GiB forward pair also passed. The prespecified budget rule selected 8 GiB for the reverse and independent full runs.

**The full-profile comparisons use matched requests.** All six fixed-length replies had 768 output tokens, but their output fingerprints differed between the selected profile and both baselines. On the eight 262K turns, four output token hashes matched tuned Mia. Python’s final 40K and 262K seeds reused 39,616 and 262,272 cached tokens respectively. Its five 40K follow-ups needed only 4–51 new prompt tokens each, and its eight 262K follow-ups needed 7–62 each. The selected native profile had cold first-token times of 30.36 and 197.03 seconds on those seeds. The selected profile reproduced the native target score of 1.854202563 mean negative log likelihood over 29,281 tokens and passed both executable fixtures and both project-return tests. An earlier pair of project-return requests changed the upfront reasoning setting and forced cold misses. The reported comparison uses corrected fixtures with the same setting and a verified common token prefix.

The tested native executable has SHA-256 `39441a89a54013442ec4d10a3f6e3535f038d648eed8165c49d817c71187e24f`, verified on both ranks. This is a private qualification build. A release through the recipe repository requires qualifying its CI-published image digest on the Sparks. [Spark inference release and qualification workflow](https://github.com/kelchm/spark-inference/blob/main/AGENTS.md)

**Deployment replay and recovery.** The packaged qualification recipe (SHA-256 `58d5d57eb22b43896f58ed08241be830b026d8868bc8614156233cc06d105046`) launched the same verified native executable on both ranks. The packaged recipe delivered 72.75 streaming / 71.47 end-to-end tok/s on the six fixed-length replies, 69.69 / 55.22 on the eight 262K follow-ups and 56.86 / 54.38 on the five 40K follow-ups. The two histories, repeated writing, six alternating-project turns, target score and runnable fixtures passed their checks against the selected trial. The durable Python service was then restored on both Sparks and correctly answered a stored request after loading 111,936 tokens from disk.

## Stock release controls

The comparison includes 1.3.1 as a release control. Its changes concern Hy4-flash on Metal. Version 1.3.2 added EXL3 decode work scheduling and programmatic dependent launches. Version 1.3.3 reported its long-context GLM decode gain at four ranks. Version 1.3.4 added a direct EXL3 decode path and a configurable TP2 index split threshold, so the matrix varies those settings on two Sparks. Its paired-prompt expert optimization selects NVFP4 weights in the dispatch code. Mia’s checkpoint uses EXL3 routed weights. [1.3.1 release](https://github.com/ashhart/TensorFold/releases/tag/v1.3.1) · [1.3.2 release](https://github.com/ashhart/TensorFold/releases/tag/v1.3.2) · [1.3.3 release](https://github.com/ashhart/TensorFold/releases/tag/v1.3.3) · [1.3.4 paired expert dispatch](https://github.com/ashhart/TensorFold/blob/v1.3.4/zig/src/families/glm/cuda_forward_pair.zig)

All five releases used numeric 4-bit dense projections, an 8-bit head and BF16 paged KV. The five 39.7K warm follow-ups produced identical output token hashes and verification-round counts across releases. The cold column gives mean first-token time across two fresh 31,190-token requests.

| Stock Zig release | Warm streaming tok/s | Warm end-to-end tok/s | Cold first token, s |
| --- | ---: | ---: | ---: |
| Mia 1104, TensorFold 1.3.0 | 59.01 | 54.30 | 21.41 |
| Upstream 1.3.1 | 58.41 | 51.53 | 21.77 |
| Upstream 1.3.2 | 59.04 | 53.87 | 21.84 |
| Upstream 1.3.3 | 59.13 | 54.02 | 21.80 |
| Upstream 1.3.4, direct expert path off, index split 2,048 | 59.26 | 54.48 | 21.40 |

The stock 1.3.4 control gained 0.4% streaming over Mia on this paired run. Enabling its direct expert path and moving the index split threshold to 32,768 yielded 59.40 streaming and 54.29 end to end, with the same replies and round counts. [TensorFold v1.3.4](https://github.com/ashhart/TensorFold/releases/tag/v1.3.4)

Across the four stock 1.3.4 combinations of direct experts on/off and index split at 2,048/32,768, the 31,190-token cold first-token mean ranged from 21.25 to 21.40 seconds, and warm streaming ranged from 58.76 to 59.40 tok/s. The split threshold controls when ranks divide indexer work on prompt chunks.

## Production-format coding throughput

The production-format control reran the durable Python recipe and the qualified Mia-based Zig recipe on the same request bodies. Both used Q4-MSE dense weights, the FP8 head, power-of-two FP8 KV and Q4 DFlash2 down projections. Each cell below gives output tok/s **after the first token / end to end**.

| Warm workload | Durable Python | Qualified Mia-based Zig |
| --- | ---: | ---: |
| Five coding turns, 39.7K history | 57.55 / 55.37 | 57.00 / 52.59 |
| Eight coding turns, 262K history | 66.38 / 54.94 | 66.45 / 53.60 |
| Three 768-token writing prompts, two repetitions each, 262K history | 69.58 / 64.74 | 70.73 / 65.17 |

The Python seed for the 39.7K run found 39,616 tokens already cached. Both Zig seeds and the 262K Python seed started with zero cached tokens. The table excludes seeds and times the same follow-up requests on each engine.

The fixed-length writing result favored Zig by 1.66% in streaming throughput. Grouping the two repetitions by prompt gave a descriptive 95% bootstrap interval of −0.25% to +3.72% across only three distinct prompts. At 39.7K the lighter L2 prefetch setting measured 56.87 tok/s against 57.00 for the qualified recipe.

The 1.3.4 build with the production formats alone streamed at 53.66 tok/s on the 39.7K turns and 60.57 tok/s on the 262K turns. Adding the ported drafting, expert, attention and prefill settings together reached 56.95 and 67.96 tok/s, gains of 6.13% and 12.20%. End-to-end rates rose from 48.75 to 52.19 and from 45.61 to 52.22. Both ranks reported the same four format settings on the two builds. At 262K, output increased from 3.961 to 4.307 net tokens per verification round while milliseconds per round fell from 65.40 to 63.38. Four of eight 262K output token hashes matched across those builds. All eight matched between the fully tuned 1.3.4 build and tuned Mia 1.3.0.

The matched-format 1.3.4 build passed GPU checks on the linked executable for target/drafter weights, packed KV and tiled sparse attention. A separate output-major traversal for the routed EXL3 projections reduced combined gate/up and down kernel time by 1.28–4.37% at four rows, 1.31–1.48% at eight, and 1.32–1.89% at sixteen. The two- and three-row results were mixed. At two rows the change ranged from 1.34% slower to 1.70% faster. At three it ranged from 3.51% slower to 1.59% faster. The four-, eight- and sixteen-row ranges cover two real layer/rank pairs and both tested router slot layouts. The three packaged kernels match the measured GPU instructions. A same-image factorial measures how much each of the three changes contributes to service throughput.

The first matched-format 1.3.4 service run used the external drafter alone, early shared-expert execution and output-major routed-expert traversal together. It streamed at 57.17 tok/s on the 39.7K turns, 67.21 on the 262K turns and 71.11 on the six fixed-length writing replies. The corresponding end-to-end rates were 51.84, 51.06 and 61.15 tok/s. On the 262K suite the first-token waits totaled 14.53 s compared with 11.17 s on the tuned Mia-based recipe, with 709 verification rounds on both. The 29,281-token target score matched to the recorded precision (mean negative log-likelihood 1.854202563), and both executable coding tests passed. Both 11- and 12-layer KV page layouts passed the packed-index and sparse-attention GPU checks on this binary.

Eight service configurations on the same binary varied those three switches independently. All eight configurations returned identical token IDs and verification-round counts on all 13 follow-ups. Enabling all three moved 39.7K streaming from 56.71 to 57.17 tok/s and 262K streaming from 68.65 to 67.21 tok/s. Averaging each switch over the four matched settings of the other two gave the following changes. Each configuration ran once in this screen.

| Switch enabled | 39.7K client / server, change in tok/s | 262K client / server, change in tok/s |
| --- | ---: | ---: |
| External drafter alone, `TF_GLM_DFLASH_ONLY` | +0.01 / +0.06 | −0.68 / −0.14 |
| Run the shared expert early, `TF_GLM_L2PF_SHARED_FIRST` | −0.10 / −0.05 | −0.50 / −0.23 |
| Traverse routed weights by output tile, `TF_GLM_EXL3_N_MAJOR` | +0.33 / +0.17 | −0.07 / +0.43 |

The server column divides the same output token count by TensorFold's reported generation interval. It times from the prompt-complete notification until the generator drains, including host-side streaming work. On all four 262K pairs, the server recorded a small improvement from output-major traversal. On the Mac, the small differences changed sign between pairs. Each setting had one launch in this screen. Python reports a differently bounded decode interval, so cross-engine throughput comparisons use the same client timer.

GLM’s checkpoint includes an auxiliary multi-token prediction (MTP) layer. It takes the preceding hidden state and an embedding of the latest token to guess the next token. It can repeat that step to propose several tokens. The 45-layer target checks those guesses together, keeps the matching prefix and supplies its own token at the first disagreement. Correct guesses let one target pass advance the reply by several tokens.

This recipe already asks the external DFlash2 model for those guesses. Setting `TF_GLM_DFLASH_ONLY=1` skips loading and maintaining the checkpoint's auxiliary layer, while retaining all 45 target layers and the Q4 draft vocabulary projection. Switching back to built-in MTP then requires restarting with the block enabled. The allocated KV pool grew by 17.8–21.9% across four matched configurations. With early shared-expert execution and output-major traversal enabled, reported model memory fell from 82.16 to 80.31 GiB, a 512-token KV page shrank from 3.40 to 3.12 MiB, and capacity rose from 2,874,368 to 3,470,848 tokens shared across all active streams.

To compare the two predictors directly, a separate pair kept the same 1.3.4 image, target weights, KV format, kernels and built-in MTP layer. One process used that layer to draft. The other loaded the Q4 DFlash2 model and used the recipe’s external-drafting policy. Across five 40K follow-ups, MTP streamed at 49.25 tok/s and DFlash2 at 56.38. Across eight 262K follow-ups, MTP streamed at 53.81 and DFlash2 at 67.85. The 262K MTP round was slightly cheaper (61.16 vs 63.49 ms), but accepted work advanced generation by 3.29 vs 4.31 output tokens per round. DFlash2 therefore needed 709 verification rounds to MTP’s 928. All 13 output token hashes matched. On the six fixed-length writing replies, the Mac measured 53.95 vs 71.67 streaming tok/s, and a second client on the Spark head measured 54.42 vs 71.57. All six repeated replies matched token-for-token across predictors. These figures measure each serving policy, including its copy-proposal rules.

A separate expert-weight layout experiment tested two ways to arrange the packed words in memory. It compared two real layer/rank pairs, both router slot layouts and eleven interleaved samples at each of two, three and four rows. Relative to output-major traversal, median combined projection latency increased 0.27% with one layout and 0.35% with the other. Neither justified a model-wide weight conversion.

The direct expert path flattens the router picks and sums their outputs in place. The specialized EXL3 kernels change the gate/up and down projections themselves. On 1.3.4, enabling the specialized kernels added 1.46 tok/s at 40K and 1.77 at 262K, averaged over the two settings of the direct path. The same experiment on Mia's 1.3.0 backport added 1.35 and 1.77 tok/s. Once the specialized kernels were active, adding the direct path at 262K moved Mia from 68.06 to 68.30 and 1.3.4 from 68.08 to 67.96 tok/s. All 13 output token hashes and verification-round counts matched across the four configurations on each image.

On the same 1.3.4 binary, enabling programmatic dependent launches and the bundled L2 prefetch settings raised streaming from 54.55 to 56.99 tok/s at 40K (+4.47%) and from 66.06 to 68.94 at 262K (+4.37%). All 13 outputs matched across the four settings. At 262K the individual switches contributed 1.34 and 1.55 tok/s on average. Both were already enabled in the tuned recipe.

## Prompt checkpoints and project switches

In the initial 262K control, Python processed 865 uncached input tokens across the eight follow-ups. Shared-page Zig processed 9,377. Total first-token wait was 9.65 seconds on Python, 11.17 on tuned Mia and 13.70 on tuned 1.3.4. The latter reported 8.67 seconds of engine prefill, and its Mac client took 4.14 seconds to connect and submit the eight request bodies. This difference in prefix reuse and request transfer matters even when Zig generates tokens faster once a reply starts.

An alternative 1.3.4 configuration copied snapshots into an 8 GiB cache and marked eligible message boundaries. On the five 39.7K follow-ups it restored 39,640 tokens each time, up from 38,912 with the same copied cache on the standard grid. Total input processed on those turns fell from 4,124 to 484 tokens. Median warm first-token time fell from 0.938 to 0.517 seconds and end-to-end output rose from 51.35 to 53.74 tok/s (+4.65%). The one-token cold seed rose from 27.17 to 30.33 seconds. This comparison enables `TF_GLM_REQUEST_STARTS` only with `TF_KV_SHARE=0`.

At 262K, message boundaries raised the measured streaming rate from 66.83 to 69.43 tok/s. All four tool replies retained identical output token IDs. The three writing replies changed at the same 768-token length, and the review changed from 428 to 417 tokens. A fresh process generated exactly the same 768 tokens for the first writing reply as a resume from 262,307 cached tokens on the boundary-enabled profile. The same check passed for a 39,728-token Go request resumed at 39,640. After the longer history and target-scoring request, the fixed-grid cache still held 38,912 tokens of the Go history. The boundary-enabled cache had evicted it, increasing first-token time on that revisit from 1.015 to 30.643 seconds.

Increasing the copied snapshot budget from 8 to 12 GiB kept the 39,640-token boundary through the same workload. The one-token Go revisit then took 0.519 seconds instead of 30.643. The startup KV pool fell from 3,332,608 to 2,650,112 tokens. With the 12 GiB budget, enabling message boundaries moved the 40K warm median first-token wait from 0.967 to 0.554 seconds. At 262K the boundary-enabled recipe delivered 70.36 streaming and 54.21 end-to-end tok/s, versus 68.87 and 53.86 for the 12 GiB grid.

Enabling message boundaries changed both the text and the number of rounds on the 262K replay. At 12 GiB the grid produced 3,062 tokens in 709 rounds, and the message-boundary recipe produced 3,051 in 689. Server time per round was 62.67 versus 62.90 ms. The measured increase in tok/s on those replies came from more output per round. The same tuned Mia image also measured 68.21 tok/s in an earlier 262K run and 66.45 in this screen, with the same token IDs and 709 rounds.

Exact repeats exposed a separate cost. On the 12 GiB message-boundary recipe, the three 768-token coding requests initially processed 548, 539 and 535 new prompt tokens and needed 160, 160 and 164 verification rounds. When a repeat restored a checkpoint leaving only two new prompt tokens, the same answers needed 176, 174 and 177 rounds. In this engine, restoring the target checkpoint resets the external DFlash2 drafter's context at the restore position. A deeper target hit therefore leaves less prompt history available for drafting. The shorter prefill still reduced total time on those repeats. The project-return tests use a new final user request to measure a different access pattern.

The two other 768-token writing requests and the naturally finishing review also passed cold-versus-resumed checks. Across all five checked cases, an empty cache produced exactly the token sequence seen after restoring 39,640 or 262,307 tokens. That check compares each request under the same chunking policy. Changing the chunking policy itself changed some replies.

For a new project, marking message boundaries splits the cold prompt into more pieces. Replaying the actual tokenized inputs through the chunk planner gave 20 versus 33 chunks at 39,679 tokens and 129 versus 212 at 262,309. At 12 GiB, the engine's own cold prefill measurement rose from 26.54 to 30.45 seconds on the shorter prompt and from 178.69 to 196.74 on the longer one. The eight warm 262K follow-ups together took 56.86 seconds on the grid and 56.28 seconds with message boundaries. Counting the initial request and all eight follow-ups, the two runs took 236.07 and 253.79 seconds respectively.

On the eight ordinary 262K follow-ups, both 8 GiB eviction settings restored the same prefix length for every request. Engine prefill totaled 8.15 and 8.20 seconds. The Mac took 9.09 and 3.07 seconds respectively to connect and submit the request bodies. That variation explains much of the difference in end-to-end rates on this part of the run.

The new eviction rule runs when the cache is full and removing a previously reused snapshot would still leave too little space. It can instead remove one unused conversation tip large enough to cover the entire shortfall. On an 8 GiB cache, this kept the 39,640-token Go prefix through a switch to the 262K project. Returning to Go took 0.487 seconds to first token versus 31.745 with ordinary eviction. Both ranks ran the same image, and the eviction switch was the only difference in the effective environments.

| Six alternating coding follow-ups | Output tokens | Go revisit, cached tokens | Go revisit, first token | Streaming tok/s | End-to-end tok/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Zig, 8 GiB, ordinary eviction | 3,306 | 0 | 31.745 s | 59.10 | 34.69 |
| Zig, 8 GiB, single unused tip | 3,306 | 39,640 | 0.487 s | 59.56 | 52.64 |
| Zig, 12 GiB, ordinary eviction | 3,306 | 39,640 | 0.442 s | 58.95 | 52.76 |
| Zig, 12 GiB, single unused tip | 3,306 | 39,640 | 0.633 s | 59.27 | 52.86 |
| Durable Python, midpoint run | 3,305 | 39,680 | 0.670 s | 61.00 | 55.81 |

All six Zig replies matched token-for-token across eviction policies at each cache size. At 12 GiB, ordinary eviction already retained the Go project and both policies restored identical depths throughout the six requests. At 8 GiB, five of six restores had the same depth. Avoiding the one cold prefill accounts for almost all of the 51.7% end-to-end gain. Its value depends on the order in which projects are revisited. In a separate CPU cache replay at 10 GiB, sparing a previously busy 40K prefix delayed a different eviction and eventually lost a 262K prefix.

## Speculative verification

The four-way 1.3.4 drafting-policy comparison isolated the source of a larger decode gain. When the generated suffix matches an earlier passage, the engine can propose up to 15 tokens copied from the continuation of that passage. The policy uses an eight-token match against the prompt or a sixteen-token match against generated text. The target verifies the proposals. With DFlash calibration enabled, turning on this copy policy moved 262K streaming from 59.26 to 65.59 tok/s (+10.7%). Verification rounds fell from 772 to 705, while net tokens per round rose from 3.96 to 4.34. Using the native server timer with calibration enabled, the rates were 60.97 and 65.41 tok/s (+7.3%). With calibration disabled, the Mac rates were 59.73 and 65.27. At 40K, enabling copying with calibration moved 53.55 to 54.17 tok/s. All 13 outputs matched across all four configurations. The tuned Mia-based recipe already includes this copying behavior.

An experimental DFlash scheduler used observed verification times to select window widths. On the 39.7K replay, passive timing reduced mean round time from 54.02 to 52.57 ms and raised the number of rounds from 913 to 935. The two passes streamed at 56.99 and 57.15 tok/s with fixed prices, 57.19 and 57.42 with passive timing, and 56.93 and 56.98 with active width probes. On the 262K replay the corresponding pairs were 68.94/66.47, 68.55/68.79 and 68.20/68.27. These paired passes give little evidence for enabling active probes on this workload.

The earlier 262K coding trace also bounds the opportunity in the draft vocabulary head. Across 40 native rounds the Q4 head averaged 0.814 ms, and the entire interval from final draft normalization through selection and the next target launch averaged 0.995 ms out of 61.168 ms. The two top-16 reductions occupied only 0.059 ms after the head. Even eliminating their post-head time would recover about 0.1% on that trace. The relevant 1.3.4 CUDA kernel bodies match the profiled versions. [Verification-round timing breakdown](glm53-coding.md#where-each-verification-round-spends-its-time)

## Individual switch combinations

The two prefill changes combine on the tested 31,190-token inputs. With both off, two independent cold requests averaged 23.76 seconds to first token. Two-stage packed projections alone took 22.56 seconds, tensor-core latent attention alone took 22.42, and both together took 21.42 (9.8% less time). Keeping both enabled and lowering the index split threshold from 32,768 to 2,048 rows moved the mean from 21.42 to 21.24 seconds. The corresponding 262K follow-ups streamed at 67.96 and 68.06 tok/s.

Each row gives pooled streaming and end-to-end output tok/s for one configuration. Switch values appear in the order named in the first column. The 40K and 262K columns cover five and eight warm follow-ups respectively. The prefill column averages first-token time over two distinct uncached 31,190-token requests. A dash marks a workload outside that part of the grid.

| Settings on the same image | Switch values | 40K streaming / end to end | 262K streaming / end to end | 31K cold first token, s |
| --- | --- | ---: | ---: | ---: |
| 1.3.4 external drafter only, early shared expert, output-major experts | off / off / off | 56.71 / 50.98 | 68.65 / 50.49 | — |
|  | off / off / on | 57.19 / 52.28 | 68.72 / 54.20 | — |
|  | off / on / off | 56.82 / 51.75 | 68.17 / 54.16 | — |
|  | off / on / on | 57.00 / 52.21 | 68.65 / 53.76 | — |
|  | on / off / off | 56.98 / 52.05 | 68.34 / 53.60 | — |
|  | on / off / on | 57.05 / 52.21 | 68.12 / 53.16 | — |
|  | on / on / off | 56.56 / 51.80 | 67.80 / 54.01 | — |
|  | on / on / on | 57.17 / 51.84 | 67.21 / 51.06 | — |
| 1.3.4 direct experts, specialized EXL3 | off / off | 55.42 / 50.56 | 66.01 / 52.34 | — |
|  | off / on | 56.83 / 51.78 | 68.08 / 52.97 | — |
|  | on / off | 55.43 / 51.03 | 66.50 / 52.82 | — |
|  | on / on | 56.95 / 52.19 | 67.96 / 52.22 | — |
| Mia 1.3.0 direct experts, specialized EXL3 | off / off | 55.02 / 50.55 | 65.97 / 52.68 | — |
|  | off / on | 56.79 / 51.78 | 68.06 / 53.71 | — |
|  | on / off | 55.78 / 50.89 | 66.85 / 52.88 | — |
|  | on / on | 56.70 / 52.02 | 68.30 / 54.38 | — |
| 1.3.4 wide MLA, extended tile ranges | off / off | 56.70 / 51.58 | 66.69 / 52.44 | — |
|  | off / on | 56.87 / 51.15 | 67.95 / 53.47 | — |
|  | on / off | 56.50 / 51.29 | 67.41 / 52.15 | — |
|  | on / on | 56.95 / 52.19 | 67.96 / 52.22 | — |
| 1.3.4 dependent launches, L2 prefetch | off / off | 54.55 / 50.07 | 66.06 / 49.09 | — |
|  | off / on | 56.27 / 51.56 | 67.61 / 53.15 | — |
|  | on / off | 55.46 / 50.69 | 67.40 / 52.82 | — |
|  | on / on | 56.99 / 52.18 | 68.94 / 51.71 | — |
| Mia 1.3.0 dependent launches, L2 prefetch | off / off | 53.97 / 49.58 | — | — |
|  | off / on | 55.86 / 50.95 | — | — |
|  | on / off | 55.50 / 51.05 | — | — |
|  | on / on | 57.00 / 52.59 | 66.45 / 53.60 | — |
| 1.3.4 MLA tensor cores, two-stage prompt projections | off / off | 55.50 / 50.20 | — | 23.76 |
|  | off / on | 55.77 / 50.81 | — | 22.56 |
|  | on / off | 56.65 / 51.55 | — | 22.42 |
|  | on / on | 56.95 / 52.19 | 67.96 / 52.22 | 21.42 |
| 1.3.4 calibrated drafting, 8-/16-token copying | off / off | 53.18 / 48.17 | 59.73 / 47.82 | — |
|  | off / on | 53.81 / 48.75 | 65.27 / 51.52 | — |
|  | on / off | 53.55 / 48.52 | 59.26 / 47.09 | — |
|  | on / on | 54.17 / 48.97 | 65.59 / 50.90 | — |

## Reproduction artifacts

The Spark head holds 40 files covering the first comparison and GPU checks in `~/sparkrun/results/release134-matrix-RKiM4y/first-window-829fd809c474.tgz` (SHA-256 `829fd809c474917e56767ea373f4a432928576de49c0031799e1bbd377173bb1`). The matched 1.3.4 source, inputs and build manifest are in the neighboring `inputs-815757371861.tgz`.

The final private archive on the Spark head is `~/sparkrun/results/release134-matrix-RKiM4y/results-537321987fca.tgz` (SHA-256 `537321987fcafabb6ee989c108ef5881665da6ab5d649143b8e8809d9102f05b`). It contains the 105 job logs, the original screening records, the corrected project-return comparisons and the packaged replay. The private receipts `final-selected-retained.json` and `portable-kit-retained.json` identify the six retained evidence and rebuild archives by digest.
