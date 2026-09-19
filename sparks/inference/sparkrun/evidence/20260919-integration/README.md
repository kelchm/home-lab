# Native SparkRun integration evaluation — 2026-09-19

This follow-up starts from #571 at `90991ef272e786ae9d31a725081f4c8195441d48`. That PR and its branch are unchanged. The new PR targets `main` and supersedes #571; it includes the inherited deployment work plus this follow-up. #571 is not a merge prerequisite. Physical deployment is an explicitly authorized evaluation, independent of a Git merge.

## Integration audit

| Source inspected | Revision | Finding and decision |
|---|---|---|
| [Mia](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks/tree/11f6cd45d39d860e290362e22fb5df9ef42196e2) | `11f6cd45` | Current TP2 runtime, InstantTensor, fair scheduler and patches. Runtime files match previous `ca855766`; only launcher override reporting changed. Package those files without maintaining a fork. |
| [Spark Arena experimental recipe](https://github.com/spark-arena/recipe-registry/blob/8cd8a06a4be606e81854b04b9642b1bbb0e01c52/experimental-recipes/glm-5.3/glm-5.3-flash-exl3-dflash2-2x-vllm.yaml) | `8cd8a06a` | Already supports InstantTensor and pinned target/draft distribution. Older image, 262k/.84, draft TP1 and `skip`; reuse distribution pattern, not the serving profile. |
| [mfellner 850k](https://github.com/mfellner/sparkrun-recipes/blob/4f8ecea3a513a151d772de1e067ebcbd72b2f684/recipes/glm-5.3-flash-exl3-dflash2-dual-spark-850k.yaml) | `4f8ecea3` | Native mods plus pinned models, September 13 qualification, older pre-InstantTensor image and `skip`. Its custom serve/readiness wrapper addresses older SparkRun behavior. Reuse mods pattern; SparkRun 0.3.9 already provides adequate post-ready lifecycle. |
| [abliter8](https://github.com/abliter8-ai/glm-5.3-flash-exl3-prod/tree/7e91abf41d07b5f4eb870a8786b4399ac13c9355) | `7e91abf4` | Useful bounded quality/long-context/headroom evidence, but older 640k lane, different image lineage and abliteration. Not a drop-in for this workload. |
| [SparkRun](https://github.com/spark-arena/sparkrun/tree/60f897e0b11fce2ddd35ceffe227448a6a34fec8) | `60f897e0`, 0.3.9 | Native mods, pinned multi-model distribution, native distributed vLLM, persistent caches, post-ready hooks and llama-benchy integration are present. No SparkRun fork or replacement supervisor is necessary. |

The live `exl3-instanttensor` registry tag resolved again to `sha256:447114ee77d14c9b4732ee23978ada2a0ee9027868a231d6fd42700a8b25be1d`. Source and image are separately pinned; this is not a claim that the image was built from the overlay revision.

## Memory experiment

The installed InstantTensor 0.2.0 iterator calls `safe_open(..., copy=True)` without explicit buffer parameters, so supported environment controls apply. Its direct-I/O TP2 default uses 8 MiB chunks and depth 256; `required_buffer_size_for_io(8388608, 256, 2)` returns 4,294,967,296 bytes. The candidate uses a 2 GiB buffer; the library reduces I/O depth automatically. Header inspection found the largest target tensor at 1,268,776,960 bytes. No loader changes or loader substitution are involved.

A separate explicit 14 GiB KV cap returns approximately 1 GiB of the baseline's 15.03 GiB allocation to host headroom. The configured `.85` utilization is retained for SparkRun metadata, but explicit KV sizing governs the actual pool. Both changes are evaluated together; no isolated attribution of end-to-end speed or total RAM changes is claimed.

## Reproduction and evidence

The current [runbook](../../README.md) owns commands and limits. The exact baseline recipe, serving logs, hash manifests, benchmark logs and raw samples are retained on spark-1 under `/home/kelchm/sparkrun/receipts/integration-20260919/`. The one-time import verified every target/draft file against the pinned Hugging Face manifest before constructing standard blob/snapshot cache entries on both hosts. Existing source and model paths remain available for rollback.

The first attempted native benchmark used the root-only old model path as its tokenizer and fell back to GPT-2. It was explicitly interrupted and excluded. The corrected baseline run names the model tokenizer and disables prefix caching for the benchmark. Its cached tokenizer SHA-256 (`19e773648cb4e65de8660ea6365e10acca112d42a854923df93db4a6f333a82d`) matches the pinned serving snapshot. The final profile points directly to that native cache snapshot so subsequent runs cannot silently follow a newer Hub tokenizer. No approximate-tokenizer run is used as performance evidence.

The first candidate launch using optional `transfer_mode: pull` failed before container creation: `sync_image_to_hosts() got an unexpected keyword argument 'ssh_options'`. The cluster was returned to native `auto` mode; no installed SparkRun code was patched.

## Validation scope

Static checks pass for all 94 parity assertions, YAML parsing, and the mod's missing/tampered-file rejection paths. Both running containers' 22 installed runtime files match the lock; each container has only SparkRun's HF and runtime cache mounts. Native image/model distribution, mod hooks, readiness, post-ready shape warmup, status, and logs from the permanent recipe path were exercised.

All eight direct workstation API checks passed: arithmetic, automatic tool call, tool result, `tool_choice=none`, JSON schema, reasoning, image and five images. The first image request took about eight seconds; subsequent image input was faster. This is a small functional gate, not model-quality certification.

The first mixed run delivered short-newcomer content in 0.479 s while the original 1024-token decode was active. The larger newcomer actually contained 36,047 tokens and took 82.009 s to first content; it completed after the original stream. That stream's maximum delivery gap was 5.976 s. Correctness passed, but no long-prefill latency SLA is claimed. Fair prefill made progress during decode; it does not make this workload fast.

The 14 GiB pool reports 883,552 equivalent tokens, 552 usable block IDs versus 532 required at 850k, and approximately 50,176 aligned idle cached-conversation tokens. Those quantities are different; none implies four simultaneous 850k requests.

The context-boundary request passed with 849,968 input tokens plus a 32-token output budget. It returned `SILVERFOX` correctly (five output tokens), with 584.445 s TTFT. This uses the serving tokenizer/chat template and exact token IDs through `/v1/completions`; it demonstrates allocation and simple retrieval at the configured boundary, not general 850k reasoning or multimodal quality. The completed-request metrics reported **five KV preemptions**, corroborated by five replay-checkpoint events in the serving log. It processed 878,448 prefill tokens, including 28,480 replayed tokens (about 3.35% extra). Live metrics had not reported these until completion. This is a successful bounded request with recovery, not a zero-preemption result. Prefix caching remains enabled: [Mia’s no-store design](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks/blob/11f6cd45d39d860e290362e22fb5df9ef42196e2/docs/DESIGN-apc-no-store.md#3-risks) explicitly describes the full-recomputation risk for a cold no-store request after preemption. No claim is made that a larger pool or different retention policy would eliminate these replays; that was not isolated.

## Completed benchmark and final state

The native llama-benchy 0.4.0 profile completed both prompt sizes at C1/C4, with two measured repetitions per cell. Figures below use the tool's aggregate throughput fields and mean request time to first response; raw distributions are in [baseline](baseline-benchmark.json) and [candidate](candidate-benchmark.json). They are measurements of the corrected fair-scheduler baseline and this candidate, not the older draft/skip deployment.

| Prompt / concurrency | Prefill tok/s, baseline → candidate | Generation tok/s, baseline → candidate | Mean TTFR seconds, baseline → candidate |
|---|---:|---:|---:|
| 2,048 / C1 | 1155.6 → 1199.4 | 31.2 → 28.5 | 1.77 → 1.71 |
| 32,768 / C1 | 1690.8 → 1690.4 | 30.4 → 32.2 | 19.38 → 19.39 |
| 2,048 / C4 | 868.0 → 545.4 | 36.3 → 37.8 | 14.24 → 11.35 |
| 32,768 / C4 | 1298.2 → 1289.6 | 11.6 → 11.7 | 64.61 → 63.83 |

C1 and 32k/C4 results are close in these samples. The 2k/C4 prefill aggregate is lower in the candidate, but baseline repetitions ranged from 240 to 1,496 tok/s versus candidate 490 to 600 tok/s; two repetitions do not establish a regression or improvement. No broad speedup is claimed. Concurrent 32k requests still have substantial queueing latency: candidate TTFR ranged from 23.0 to 101.9 seconds.

The post-benchmark mixed check passed again: short newcomer TTFT 0.502 seconds during a continuing decode; roughly 36k newcomer TTFT 65.709 seconds, completing after the original decode, whose maximum delivery gap was 1.368 seconds. Both answers were correct. The native benchmark and warm mixed run added no KV preemptions; the final counter remained five from the boundary test.

Two-second host samples from candidate readiness through all checks recorded minimum available memory of 5.23 GiB on the head and 7.78 GiB on the worker, ending at 5.57/7.83 GiB. Neither host added swap-out pages during the monitored deployment and tests; existing swap remained about 177/149 MiB. Kernel logs showed no OOM or NVIDIA Xid events in that window. These are sampled host measurements, not exact instantaneous allocator peaks. Warm workload allocations consume part of the initial headroom gain; the I/O cap bounds loader staging and the explicit KV cap makes serving allocation predictable, without claiming a permanent 2 GiB RAM saving. Target loading took 30.16 seconds versus 27.93 seconds in the baseline's retained startup log; these single startups have different cache histories.

Both containers and the API remained healthy at the final check. Temporary telemetry processes were stopped. The tested candidate remains deployed, with the permanent recipe path and rollback instructions in the runbook.

## Independent review

Grok reviewed the implementation read-only against SparkRun and Mia sources. Its concrete missing-usage error path in the boundary harness was valid and fixed so missing usage produces a failed receipt. Its memory-guard suggestion was already addressed by the expanded parity checker; the new buffer/KV settings and rendered arguments are asserted. The runbook now includes the optional expensive boundary command. No launch-blocking integration finding was reported. Live validation remains necessary regardless of review agreement.

## Remaining limits

This is a small two-host evaluation, not a statistically controlled performance study. The buffer, KV cap and packaging changed together. First-boot compilation/cache state differs from the long-running baseline. No fresh NCCL channel A/B is claimed; old channel comparisons used a different draft and `skip`. The 48-image maximum, video, four maximum-context requests, general long-context reasoning quality and reboot recovery are not qualified by these checks. The existing firewall exception remains nonpersistent.
