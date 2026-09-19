# Upstream parity audit — 2026-09-19

The audit compares the two-node `start.sh` path and `.env.example` at `ca8557665bffa6529758f2c330ba8fb44c1e801a` with the SparkRun recipe and live containers. `git ls-remote` confirmed that commit was still upstream HEAD during the audit. The public `exl3-instanttensor` image tag also resolved to the recipe's existing digest, `sha256:447114ee77d14c9b4732ee23978ada2a0ee9027868a231d6fd42700a8b25be1d`. No source or image upgrade was needed.

## Discrepancies corrected

| Area | Previous SparkRun behavior | Upstream behavior / correction |
|---|---|---|
| Mixed prefill | `skip`; new prompts wait for existing decode to finish | `fair`, with the five upstream fair-policy defaults explicitly supplied |
| Draft checkpoint | Mutable DFlash2 main `bf582e4eacc1810f76656d1811693ff6c6737d2a` | Exact upstream pin `dc77ff1c99eeb2df044ee3d4f0094eb033fee410`; downloaded into a separate directory, retaining the old files |
| Image count | 4 images per prompt | 48 images, 1 video; per-image token limit remains 2048 |
| Multimodal processor cache | CLI limit omitted; vLLM default is 4 GiB | Explicit `--mm-processor-cache-gb 1` |
| TileLang cache | Ephemeral `/root/.tilelang/cache` inside each container | Persistent `/cache/runtime/tilelang` under SparkRun's existing runtime-cache mount |
| Post-ready warmup | Missing; detached launch returned before readiness | Upstream `scripts/boot-shape-warmup.sh` runs through SparkRun `post_exec` after readiness, with k=7, C4 and 240-second request timeouts |
| Container privileges | SparkRun inherited `privileged=true`, explicit `SYS_PTRACE` | `privileged=false`, `IPC_LOCK` only, explicit `/dev/infiniband` device access |
| IPC | SparkRun `shareable` default | Upstream `host` IPC; Docker ignores the 32 GiB shm-size argument in this mode |
| Engine-ready timeout | Upstream environment setting omitted | `VLLM_ENGINE_READY_TIMEOUT_S=3600` |
| RoCE environment | Upstream `NCCL_IB_ROCE_VERSION_NUM` omitted | Explicit `2` |
| NCCL verbosity | `INFO` | Upstream `WARN` |

The old draft's weight SHA-256 was `b038e1d9d1e7833fa3880c2c0135ba9b673013f03da1b29fb831931584759dac`. The pinned draft is `b33c03475ba7322cf398828f2d8d1be376df30dc05c6b40c28c8ea8da23e410b`. They have the same byte length and matching config, so the previous size/config checks did not establish checkpoint identity. Both new copies were checked against the Hugging Face LFS SHA-256 at the exact upstream revision.

Earlier performance and functional receipts remain historical evidence for the previous deployment. In particular, the NCCL comparison used the old draft and `skip` policy; it is not a benchmark of the corrected upstream recipe.

## Verified matches

- All 120 target safetensors shards on each node match the LFS SHA-256 hashes at upstream's target pin `25a44fdbf16862a46b7cc9921142c6c81350af2f`. All associated JSON files selected from that revision's manifest also match, using LFS SHA-256 or Git blob SHA-1 as appropriate. Each node's original inventory audit checked 137 files across target and draft; the only mismatch was the old draft weight file.
- All 18 upstream runtime patch scripts were already present, mounted and ordered correctly. Hashes of all 22 mounted source files, including the newly mounted warmup script, match the pinned checkout on both nodes.
- Target quantization EXL3, InstantTensor loading, target FP8 KV, draft auto/BF16 KV, DFlash2 k=7, draft TP=2, probabilistic draft sampling and standard rejection match.
- TP=2, two nodes, mp executor, 850k context, 0.85 utilization, four sequences and 7168-token batches match. SparkRun supplies node rank, headless worker, master address and node count.
- The E3 grouped/fused MoE path, 32 fused temporary rows, right-sized indexer workspace, CUDA graphs and capture sizes, prefix caching, stop suppression and bounded omitted-output default match.
- Tool parser `glm47`, reasoning parser `glm45`, chat template, image-token limit and skipped multimodal profiling match. No language-only, custom reasoning effort, video frame count, long-prefill threshold or prefix-match-unit override is active upstream or here.
- Adaptive verification, dense FP8, fast experimental decode kernels, abliteration and cache-reset routes remain off. The recipe now pins the relevant off/default values explicitly rather than relying on image or patch fallbacks.
- `GLM53_APC_NO_STORE=1` matches upstream. This enables a client's optional no-store request flag; it does **not** disable prefix-cache writes globally. Empty per-group retention settings inherit upstream defaults.
- SparkRun already persists the vLLM and Triton caches and supplies `NCCL_NET=IB`, IB enablement, CPU-affinity handling and GID 3. Both fabric HCAs are selected; prior serving logs confirm NET/IB use on both rails.

## Intentional site and SparkRun differences

Eight NCCL channels and disabled graph-memory estimation remain the two measured memory overrides. Graph execution remains enabled. The earlier A/B/A showed almost equal decode speed at 8 versus 16 channels, with substantially more KV margin at 8. Allocation and functional checks must be repeated for the corrected draft; the historical numbers are not assumed to carry over exactly.

Local checkpoint paths replace upstream HF snapshot paths; the manifest audit now establishes content identity. The draft directory includes its revision. SparkRun uses `/cache/huggingface` and `/cache/runtime` instead of upstream's `/root/.cache` paths. Our management address provides rendezvous/Gloo, with both local fabric HCAs used for NCCL data; upstream's sample single-interface names are specific to its kit. The rendezvous port is SparkRun's 25000 instead of upstream's 29521. These are topology/orchestration mappings, not model changes.

SparkRun owns container names, launch order, detached process management, runtime-cache paths and stop behavior. `auto_remove=false` retains diagnostics, and its `nofile` limit remains 65535. Neither launcher configures restart-on-reboot here. The recipe uses SparkRun's post-ready hook for upstream warmup: a hook failure surfaces as a failed SparkRun command rather than upstream's nonfatal warning; the already-running server is not automatically stopped. The direct LAN API remains unauthenticated, matching upstream's empty API-key default, with the existing workstation firewall exception unchanged.

## Repeatable checks

Run `python3 sparks/inference/sparkrun/check_upstream.py /path/to/upstream-checkout` with PyYAML installed. It verifies the reviewed commit, key environment and CLI defaults, checkpoint pins, patch order, warmup presence and selected container settings. It is a guard for future edits, not a substitute for the manual audit, checkpoint hashing or live inspection. A negative check confirmed it rejects reintroducing `skip`.

Raw inventory hashes, overlay checks, original configuration, launch and serving logs, and post-correction validation are retained under `/home/kelchm/sparkrun/receipts/upstream-audit` on spark-1, with local copies in `.private/upstream-audit`.

## Post-correction validation

The [validation receipt](upstream-validation-20260919.json) records the corrected physical deployment. Both live container environments matched the recipe; host IPC and nonprivileged operation were confirmed. Upstream shape warmup exited successfully through SparkRun's fail-fast post hook (successful hook stdout is suppressed by SparkRun). All six API checks passed, as did a five-image request that exceeded the former four-image limit. The 48-image maximum and video path were not load-tested.

The staggered one-word request received its first token in 0.865 seconds and completed while the 600-token prose response was still running, versus 20.296 seconds on the previous deployment. Prose decoded at 27.50 tok/s in this one validation sample. Because the draft, scheduler and other parity corrections changed together, this is behavioral acceptance, not an isolated scheduler or draft benchmark.

The corrected boot reported 15.03 GiB available KV and 944,266 equivalent tokens; 850k context, 0.85 utilization, InstantTensor and eight NCCL channels remain configured. Final health returned HTTP 200, the preemption counter was zero, and available host RAM was approximately 5.4 GiB / 7.5 GiB. A full 850k request remains untested.
