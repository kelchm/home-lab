# GLM 1104 native parity candidate

This is a separate development bundle based on Mia's [108-patch kit at 02dac387](https://github.com/MiaAI-Lab/GLM-5.3-Flash-DGX-Sparks-1104/tree/02dac3879852b8c5713318d3d64a5a791002e954) and TensorFold `f350cd3e`. It preserves the original `mia-tensorfold-glm53-1104` reference and the running Python durable profile. It has not run a model on the Sparks. Adoption is tracked in [#1280](https://github.com/kelchm/home-lab/issues/1280).

## Effective behavior

The launcher retains thinking with `TF_GLM_CLEAR_THINKING=0`, four streams, the 1,048,576-token total window and a 32,768-token reply budget. Target and DFlash2 weight revisions remain the production revisions. Dense/head storage is native q8 and KV storage is FP8. Native q8 and Python's quantization/sampling have different arithmetic; quality and performance require matched workload evaluation.

| Component | This bundle |
| --- | --- |
| Agent APIs | Thinking discovery, reasoning alias normalization and bounded exact-delivered-call-ID reasoning restoration; unmatched identified tool results refuse before admission |
| Images | 50 occurrences, fixed 2,048-token cap each, 102,400 total; production byte/pixel limits, Torch resize and rendered occurrence order across OpenAI, Messages and Responses |
| Videos | Four clips, 128 sampled frames per clip, a 16,384-token per-clip ceiling and 32,768 shared request budget; single-pass decoding and fitted uint8 canvases before inference admission |
| Preparation | Four resident request families, including warm borrowers; up to 32 additional requests wait before body allocation. Temporary render/tokenizer/media scratch is counted and released separately. Active preparation budgets exclude queue and text-prefill waits |
| CUDA media rows | One 192 MiB shared feature buffer per rank, with no additional feature-row reservation per stream; tower workspace and KV memory are separate |
| Codecs | Pinned JPEG/WebP and portable FFmpeg 9.0.2; CMYK/YCCK, orientation, alpha and first-image MPO covered by independent pixel fixtures |
| HTTP | Explicit 96 MiB body ceiling for generation and counting; ordinary native defaults remain 32 MiB |
| Startup capacity | Requires at least 2,582,528 aggregate live-pool tokens and a nonzero device prompt cache; actual achieved capacity remains unmeasured |
| Checkpoints | Pool pins, capsule capture, bounded transfers, versioned metadata, direct inactive restore and filesystem epoch recovery; up to 306 picture/video-group identities. Server storage remains disabled |

The [production memory audit](tests/memory_baseline/README.md) corrects the earlier comparison: Python's idle prefixes share its observed live KV pool. Its 12.5 GiB setting does not create an additional independent row cache. Preserve the actual sharing and reuse behavior rather than double-counting that allowance.

## Source and artifact identity

`upstream.lock.json` records the full source revisions, separate upstream/site patch counts and SHA-256 values, immutable build bases and the built image config digest. The local image tag includes the site patch identity. `prepare.py --check` verifies every frozen local file. The source helper uses a Git archive of the locked engine commit, verifies the upstream kit before appending local patches, and records every native build input's hash. It never reads a dirty engine working tree into the prepared source.

Build only on the workstation's Colima `tf-build` daemon. A candidate build uses a private tag and reports its config and daemon IDs. The normal rebuild path publishes the locked tag only when its exported config digest matches the lock. Spark CPU binaries are never executed on the workstation. The portable codec tests execute separate generic CPU code.

```sh
python3 prepare.py --check
bash build/build.sh --candidate
```

## Off-host verification

Prepare the source from an existing repository that contains the locked engine commit and a separately fetched exact kit directory:

```sh
python3 source.py --repository /absolute/engine-repository --kit /absolute/pinned-kit --output /absolute/new-source-directory
python3 tests/run_host.py --source /absolute/new-source-directory --zig /absolute/zig-0.17.0/zig --cpu apple_m1 --full
```

The host runner rejects changed prepared inputs, copies them into a disposable directory and installs the independent API/media fixtures. Native tests use recording CUDA effects and inert compile images. The [composed receipt](tests/composed-receipt.json) binds the 338-step, 4,586-pass host gate and codec checks to the built image and all 123 patch hashes recorded in its `BUILD-INFO`. The broad gate reports 28 skipped cases, including optional codec cases; the separate required decoder gate runs all 32 real-library cases without skips. Read the focused evidence for [API](tests/api/run_tests.py), [media geometry/order](tests/media/README.md), [bounded media lifecycle](tests/media_lifecycle/README.md), [final preparation/video policy](tests/compat/README.md), [photo pixels](tests/photo/README.md), [single-pass video and real decoder](tests/video_prepare/README.md), [typed media refusals](tests/media_errors/README.md), [request preparation](tests/prepare_memory/README.md), [checkpoint foundations](tests/checkpoint/README.md), [inactive restore and epoch recovery](tests/checkpoint_restore/README.md), and [complete checkpoint media](tests/checkpoint_media/README.md). Historical milestone receipts apply to their recorded source layers; the final composed gate is required for the selected bundle.

## Qualification boundaries

This bundle does not provide serving NVMe recovery yet. Its filesystem epoch coordinator requires visibility of every rank file; the Sparks have separate local NVMe stores. Checkpoint publication on one rank cannot establish an all-rank durable point. Native files cannot reuse the retained Python spill directory. Safe capture/write guards, background worker admission, rank-local agreement/recovery, coordinated TP activation, shared immutable row blocks and storage quotas must be implemented and qualified through [#1284](https://github.com/kelchm/home-lab/issues/1284).

The resident-memory ledger must include retained HTTP/JSON/media inputs, codec internals, mutable capsules, background transfers and suspended work. The request policy bounds resident families and exports actual allocator categories; it does not establish an RSS limit or a safe GB10 headroom reservation. Those values require measurements through [#1283](https://github.com/kelchm/home-lab/issues/1283). The native default leaves admission disabled; this development profile explicitly selects four residents and 32 waiters, with headroom estimates unset.

Real CUDA numerical equality, TP fault liveness, image/video model quality, large-history reuse and memory peaks remain unmeasured. The [single-pass video correction](tests/video_prepare/README.md) validates selected frames during HTTP preparation and defers only float patch packing. Its 32K-token canvas payload is at most 147 MiB per prepared prompt; the actual boundary-fixture arena retains about 193 MiB, and a warm copy can coexist. Codec internals remain outside the Zig allocator ledger. The [typed media-error correction](tests/media_errors/README.md) preserves request refusals through whole, stepped and warm prefill; unknown device or server failures remain HTTP 500. The [slicing donor evaluation](tests/fairness/README.md) reproduces mixed recurrent state after mid-chunk cancellation; no slicing patch is included. Transactional rollback, fair prefill scheduling and the Python chunked-KDA optimization remain [#1286](https://github.com/kelchm/home-lab/issues/1286) work. Cache-first planning, loop/tool guards, reasoning limits and index-CUDA switches remain off.

Do not switch production based on these host tests or the image build. The qualification campaign must preserve capacity, stable image detail, agent behavior and durable reuse, then establish an improvement over the current profile. The operational prerequisites and rollback procedure remain in the [SparkRun runbook](../../README.md).
