# Spark inference through SparkRun

These recipes serve one model at a time across the two DGX Sparks at `http://10.32.21.31:8888/v1`; since October 21 the running instance is [DeepSeek-V4.1-Flash-1017](#deepseek-v41-flash-1017-on-bertholomuss-zig-engine). SparkRun 0.3.10 owns model and image distribution, fabric detection, containers, runtime caches, readiness, logs, stop and benchmarking, through the TensorFold runtime adapter in `plugins/`. The hosts are outside Flux: merging a recipe changes nothing on them, and each launch is a manual step. Check host state before launching; other experiments can use either GPU.

| Model | Engine | Profiles | Work |
| --- | --- | --- | --- |
| [GLM-5.3-Flash](#glm-53-flash-on-mias-tensorfold-kit) | Mia's TensorFold kit, on TensorFold 0.6.0's Python CUDA engine | `mia-tensorfold-glm53-exl3-tp2/` | [#941](https://github.com/kelchm/home-lab/issues/941) |
| [Qwen3.8-Flash-Next](#qwen38-flash-next-on-mias-zig-engine-kit) | Mia's Zig-engine kit v1.1, with a rebuilt server | `mia-tensorfold-qwen38fn-tp2/` | [#940](https://github.com/kelchm/home-lab/issues/940) |
| [DeepSeek-V4.1-Flash-1017](#deepseek-v41-flash-1017-on-bertholomuss-zig-engine) | bertholomus's fork of TensorFold's Zig engine | `bertholomus-tensorfold-dsv41-tp2/` | [#942](https://github.com/kelchm/home-lab/issues/942) |

Every profile follows the contract in [#943](https://github.com/kelchm/home-lab/issues/943): the same endpoint and `/v1/models` fields, one shared readiness gate, one runtime adapter, and pinned mods. [Deploy](#deploy) covers the shared parts and [Operate and roll back](#operate-and-roll-back) the checks before any launch. Each model's section has the same parts: current profile, inputs outside SparkRun, build, launch, stop and roll back, and known limits. Trial records belong in each model's issue. Records of retired profiles stay in Git history:

- [this runbook at `bc3fa9ad`](https://github.com/kelchm/home-lab/blob/bc3fa9ad32d1bf29fdba48f8abaf6429531f903b/sparks/inference/sparkrun/README.md) has GLM v1.2 to v1.8, the W20 engine and Mia's vLLM recipe, their September and October comparisons, and the October 8 GLM v1.10 trial;
- [this runbook at `ed23c952`](https://github.com/kelchm/home-lab/blob/ed23c95284a51fb012cd298e330765f79b356d0c/sparks/inference/sparkrun/README.md) has GLM v1.10, Qwen's v1.1 and INT4-AR profiles and DeepSeek `6366d2c`, with Qwen's trials from October 8 to 22 and DeepSeek's launches of October 21 and 22.

## GLM-5.3-Flash on Mia's TensorFold kit

`mia-tensorfold-glm53-exl3-tp2/` holds the profile for [Mia's two-Spark GLM kit](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold): TensorFold 0.6.0's Python CUDA engine with Mia's patches, serving Mia's 4 bpw EXL3 checkpoint across both Sparks. It replaced the kit's v1.10 release after [#941's comparison on October 22](https://github.com/kelchm/home-lab/issues/941#issuecomment-6334247435), in which the same 48 requests gave identical replies on both, and decode and prefill were within about 3% of v1.10's. GLM last served day to day on October 8, on v1.10.

### Current profile

| Profile | Kit | Image | Served name |
| --- | --- | --- | --- |
| `glm53-exl3-tp2-mia-tf-3fd60dfc-mia-1m` | `main` at [`3fd60dfc`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/commit/3fd60dfc836fab0f1ed0fd4f0afaf2a1d62b2aab), October 12, 107 patches | `14b5072e…`, config `9b007193…` | `GLM-5.3-Flash` |

The kit patches engine commit `c4646171` and has no numbered release after v1.10. The image is ARM64 and labelled with the kit's patch hash, `tf.patches=e66c657a433d`. Its patched files match a reconstruction of the engine plus the kit's patches byte for byte, for every file the mod guards or reads.

- **Weights:** [Mia's 4 bpw TensorFold checkpoint](https://huggingface.co/Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold/tree/078455ffe6472f9a52fbc1139f58b9db2881b25c) at `078455ff`, 83 shards and 175,642,267,944 bytes, and the DFlash2 drafter `incoai/GLM-5.3-Flash-DFlash2` at `bf582e4e`, 2,342,169,800 bytes.
- **Serving:** a 1,048,576-token window per request and a 32,768-token reply budget, four streams sharing one FP8 KV pool, and thinking and vision on. The engine sizes the pool from the memory free at startup, inside the kit's 14.5 GiB memory reserve and 12.5 GiB kept-prompt cap; on October 22 it loaded 2,330,624 tokens. Readiness requires at least 1.2M, room for four 262,144-token prompts plus their replies. `TF_GLM_FILL_ROWS=2048` makes a fill's chunk the whole prompt chunk. Several of the kit's CUDA attention changes target three Sparks; the kit keeps the two-Spark 32-head path on Triton.
- **Launcher settings:** SparkRun never runs the kit's `start.sh`, and the engine's built-in defaults differ from it, so the recipe states all 39 variables the kit's [`scripts/config.sh`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/blob/3fd60dfc836fab0f1ed0fd4f0afaf2a1d62b2aab/scripts/config.sh) exports in a clean environment. The mod's lock records them as `launcher_env`, and the tests compare recipe and lock.
- **Site guards:** the mod patches three behaviours into the installed engine.
  - Reasoning is restored only by the server's original call IDs. Clients that renumber or omit call IDs must keep their own reasoning.
  - A failed decoder or dead scheduler thread answers `/health` with HTTP 503 and `ok: false`.
  - `/v1/models` reports `owned_by: tensorfold`, the window as `context_length` and `max_model_len`, and `reasoning` with the thinking default and the levels `max`, `high`, `low` and `none`. The template defines `low` and `high` and renders anything else as `max`; the server maps `minimal` to `low`, `medium` to `high` and `xhigh` to `max`, and `none` turns thinking off.

  The mod checks every guarded file's upstream hash before changing it, so it refuses files that differ from the ones it was written for. That is not an image check: the recipe's image pin pairs the recipe with its image.
- **Mod:** the rank launcher installs as `/usr/local/bin/tensorfold-entrypoint` and gives the engine `oom_score_adj` 1000. Readiness is the [shared gate](#enable-tensorfold-in-sparkrun), with the 1.2M pool floor and a C4 shape warmup at 128, 4,096 and 16,384 words.

How the profile differs from the kit's own launch:

- **No `start.sh`:** its preflight, memory warning and smoke test do not run, and neither do its recoveries: the retry with a smaller window when the requested one does not fit, and the `SPLIT=0` retry after a first NCCL connection failure ([upstream #36](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/36)). Either failure ends the launch here.
- **No rail test:** `start.sh` pings each RoCE rail at full size in both directions and drops a rail that does not answer. SparkRun passes every RDMA device whose port is active to NCCL, and the engine pairs the two ranks' devices by list position, so the [rail check](#before-a-launch) runs by hand first and a failure holds the launch.
- **Rank command:** the kit's serve flags plus `--backend cuda` and `--no-update-check`.
- **Fabric:** the ranks meet on `FABRIC_MASTER`, the head's CX7 address, as the kit's launcher does, but on SparkRun's `MASTER_PORT` where the kit uses port 29551. They fall back to SparkRun's management address only when the adapter has no fabric address. NCCL's collectives run over RoCE on both rails, with the devices and GID index SparkRun detects.
- **Container:** SparkRun clears the image entrypoint and adds `--user root`, `no-new-privileges` and an open-file limit. Both ranks use host IPC and networking, all GPUs, `/dev/infiniband`, `IPC_LOCK`, unlimited locked memory and a 64 MiB stack.
- **Caches:** weights mount at `/cache/huggingface`, and compiled kernels go to SparkRun's image- and model-keyed `/cache/runtime`, so a new image's first boot compiles from empty. This image's first boot took 460 s on October 22.

### Inputs outside SparkRun

None. Mia publishes the image by digest, and SparkRun fetches the image and both snapshots and copies them to the worker. All three were on both hosts after the October 22 comparison. Stage weights in the head's home cache before a switch, as [Deploy](#deploy) describes.

### Build

Nothing is built here. For a new kit revision, check the published image before pinning it, as this one was checked:

1. Evaluate the kit's `scripts/config.sh` in a clean environment, with no `scripts/local.sh` or `.env`, for the recipe's settings and the lock's `launcher_env`.
2. Apply the kit's patches to the pinned engine commit with `patch -p0`, check that they hash with the kit's extras line to the image's `tf.patches` label, and compare the image's patched-files layer with the reconstruction, at least for every file the mod guards or reads.
3. Add a new versioned recipe and mod with the new image, upstream hashes and guarded hashes, so the current profile stays as the rollback.

Before any live check, run the source regressions against the installed code inside each rank's container: the [Mia regression](tests/mia_tensorfold_site.py) for call-ID reasoning and HTTP health, the [capacity regression](tests/mia_tensorfold_capacity.py) for the queue cap and HTTP 429, and the [switch and route regression](tests/mia_tensorfold_switches_and_routes.py) for the recipe's launcher switches, streamed admission refusals and the Anthropic route. Feed each one unchanged to the rank's own Python, from the head for rank 0 and the worker for rank 1. They use mocked handlers and decoders, so they are source evidence, not live traffic:

```sh
rank="<rank container>"  # This host's rank container ID.
failed=0
for t in site capacity switches_and_routes; do
  docker exec -i -e PYTHONDONTWRITEBYTECODE=1 "$rank" python3 -B - < "tests/mia_tensorfold_$t.py" || { echo "FAIL $t"; failed=1; }
done
test "$failed" -eq 0
```

### Launch, stop and roll back

From `~/sparkrun/recipes/glm53` on the head, after syncing this directory as in [Deploy](#deploy) and passing the [checks before a launch](#before-a-launch). Stop the running instance with its own recipe first:

```sh
glm="$PWD/mia-tensorfold-glm53-exl3-tp2/glm53-exl3-tp2-mia-tf-3fd60dfc-mia-1m.yaml"
python3 mods/tensorfold-readiness/prepare.py --check
python3 mods/mia-tensorfold-glm53-3fd60dfc/prepare.py --check
sparkrun run "$glm" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
sparkrun run "$glm" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
sparkrun logs "$glm" --cluster sparks --all-sources
# When stopping or replacing the workload:
sparkrun stop "$glm" --cluster sparks
```

`run --no-follow` returns after the readiness hook passes: four streams, the window, at least 1.2M pool tokens, the C4 shape warmup, the discovery fields, the four levels and their renders, a reply on every stream and a tool call.

After a boot, run the shared checks in [Validate](#validate) and three for GLM: the [cut-call probe](tests/mia_tensorfold_cut_calls.py), which cuts a real tool call at its output limit and needs `--model`, `--output` and the pinned `tokenizer.json` as `--tokenizer`. It imports `tokenizers`, which the head's Python lacks, so run it from the workstation with `--url http://10.32.21.31:8888/v1`; a `/v1/messages` tool turn; and a request with a chunked body. The repository has no script for the last two.

To roll back, stop it with its own recipe and launch the profile that served before, as [Operate and roll back](#operate-and-roll-back) describes.

### Known limits

- **Measured once, on October 22:** a 500K needle was found, four 128K sessions completed together, and the lowest `MemAvailable` was 6.61 GiB on the head and 8.42 GiB on the worker. That is below the 8 GiB goal on the head and well above the 2 GiB stop line.
- **Not established:** four long sessions growing together past 128K, retrieval near 1M, full 32k replies, coding and quantization quality, and stability beyond that trial.
- **NCCL sockets on the management network:** SparkRun sets NCCL's socket interface to the management network, so some of NCCL's socket traffic crosses the worker's management link, which has dropped for seconds at a time ([#649](https://github.com/kelchm/home-lab/issues/649)).
- **Open kit reports** of memory growth and hangs under long multi-agent load: [#143](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/143), [#154](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/154), [#163](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/163) and [#197](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/197).
- **Admission:** requests past the four streams queue without limit, because `TF_GLM_MAX_QUEUED` is empty.
- **Access and memory:** the endpoint has no API key, and no memory watchdog runs ([#944](https://github.com/kelchm/home-lab/issues/944)).

## Qwen3.8-Flash-Next on Mia's Zig-engine kit

`mia-tensorfold-qwen38fn-tp2/` holds the profile for Mia's [Qwen3.8-Flash-Next two-Spark kit](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/blob/be3ed857ab1055ca47b36134d22691f00f2bb94a/README.md), v1.1 of October 8. The kit serves text through TensorFold's Zig engine, `tensorfold-native`; a Python helper on rank 0 decodes pictures and video and runs the checkpoint's vision tower on the GPU. The profile keeps the kit's published image and replaces only `tensorfold-native`, rebuilt here with `/v1/models` discovery and community fixes for the kit's two-rank stalls and crashes. It [qualified at a 491,520-token window on October 22](https://github.com/kelchm/home-lab/issues/940#issuecomment-6343678459). Qwen last served from October 8 to 17, on the kit's own binary and then on earlier builds of this one.

### Current profile

| Profile | Image | Server | Weights | Served name |
| --- | --- | --- | --- | --- |
| `qwen38fn-nvfp4-tp2-mia-tf-v1.1-discovery-nvidia-480k` | The kit's, `e188128d…`, config `5eb5d4d3…` | `tensorfold-native` `6ae47f69…`, the kit's nine patches and six more | [`nvidia/Qwen3.8-Flash-Next-NVFP4`](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/tree/fc694b54fb0174e0913e6adf86691ef85a4ead47) at `fc694b54` | `Qwen3.8-Flash-Next` |

- **Serving:** the kit's defaults, read from [`scripts/config.sh`](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/blob/be3ed857ab1055ca47b36134d22691f00f2bb94a/scripts/config.sh) and [`start.sh`](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/blob/be3ed857ab1055ca47b36134d22691f00f2bb94a/start.sh) with no `scripts/local.sh` or `.env`, apart from the window. That is 16 streams, a 32,768-token reply budget, thinking on, drafts from the checkpoint's own MTP head plus copy drafts, an FP8 KV cache, vision on with picture URLs off, a 10 GiB memory reserve and a 2 GiB vision workspace. The launcher forwards every `TENSORFOLD_*` and `TF_FLASHNEXT_*` variable the config exports, and the recipe states all ten with the same values. The FP8 cache is lossy: upstream reports 98.8% top-1 agreement with bf16 on teacher-forced replies, for a pool about 1.8–1.9 times larger. No separate drafter model is downloaded.
- **Window:** 491,520 tokens, below the 503,359-token prompt that stalled the two ranks' control link on the kit's `main` in [kit issue #3](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/3). The kit's YaRN factor of 4 stays, so the engine could admit up to 1,048,576 and `--context` sets the limit. On two fresh boots, two cold prompts of about 490K tokens each completed, the first as the first long request after the boot. Without the fixes below, [no window qualified](https://github.com/kelchm/home-lab/issues/940#issuecomment-6341022722), down to 262,144.
- **Rendezvous:** the ranks link on `FABRIC_MASTER`, the head's CX7 address from the adapter, at SparkRun's `MASTER_PORT`, and each rank logs it. `serve.py` refuses to start without it.
- **Mod:** `run.sh` verifies the mod and the rebuilt binary against the lock. It installs the binary as `/opt/tensorfold-mod/bin/tensorfold-native` and the rank launcher as `/usr/local/bin/tensorfold-entrypoint`, and leaves the image's own binary in place, unused. The launcher sets the engine's `oom_score_adj` to 1000 and runs the binary by its path. The engine finds its kernels and vision helper through the image's `TENSORFOLD_CUDA_KERNELS` and `TENSORFOLD_VISION_PYTHONPATH`. It links only `libc` and opens CUDA, NCCL and the verbs library by name, so moving the binary changes nothing else.
- **Readiness:** the shared gate, with 16 lanes, the window and `xhigh,medium,low,none`. Its `/tokenize` check renders each level, and expects `minimal` to render as `low` and `high` and `max` as `xhigh`.
- **Lock:** `upstream.lock.json` pins the kit, engine, image, weights, both discovery patches' source commits, the four fixes, the build inputs and the binary.
- **Tests:** [`tests/test_qwen_mia_contract.py`](tests/test_qwen_mia_contract.py) checks the recipe against the lock, the gate's arguments, both rank commands through SparkRun's fabric address, the install paths, the frozen mod and binary, that no lab address or path is named, and the expected entry against the gate and the live check.

The server's six patches go on after the kit's nine, in file order:

| Patch | Source | Change |
| --- | --- | --- |
| `0010-models-context-length.patch` | [`ashhart/TensorFold@51ad073f`](https://github.com/ashhart/TensorFold/commit/51ad073feadd81d04809edf8f0822cc5380a832c), upstream PR #483, released in 1.0.3 | `/v1/models` reports `context_length` and `max_model_len` |
| `0011-models-reasoning.patch` | [`ashhart/TensorFold@60f27da0`](https://github.com/ashhart/TensorFold/commit/60f27da01cb86d076a078d3b4ae3e36c2f3b41c2), upstream PR #819, released in 1.0.12 | `/v1/models` reports `reasoning` |
| `0012-record-notification-first.patch` | Zebro (`321sssrt-bit`), [kit PR #4](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/pull/4) at `36356337`; the same bytes in their [maintained fork](https://github.com/321sssrt-bit/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold) at `4e96a866` | Fixes [#3](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/3), the control-link stall. Rank 0 sends a record before its long payload, so rank 1 knows to drain the link. Before, rank 0 blocked writing the payload into a full link and never sent the record rank 1 was waiting for |
| `0013-admission-agreed-before-sequence.patch` | xinlu8439, [the version for `be3ed857`](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/6#issuecomment-6166228691) | Fixes [#6](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/6), a one-rank `NoRoom` that exits both ranks. The ranks agree on admission before either makes a sequence, so a refusal fails only that request (`OtherRankFailed`) |
| `0014-prompt-cache-evicted-kept-state.patch` | xinlu8439, [the issue's own patch](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/9) | Fixes [#9](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/9), a segfault in `endPass`. A state the store evicts in the pass that kept it is left out of rank 1's note and freed on both ranks |
| `0015-two-ranks-sample-within-248.patch` | This lab | Fixes [#8](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/8), sampling that exits both ranks. With more than one rank, a sampled request with `top_k` off (−1 or 0) is drawn with `top_k` 248, and its `top_p` and `min_p` still cut within those candidates. A `top_k` above 248 gets HTTP 400 naming the fix: it needs more than the sampler's 256 candidates (`top_k` plus 8), and otherwise fails after admission with `TopKPastScratch` |

The kit pins an engine commit with neither discovery change, so `0010` and `0011` are a local backport until the kit moves its pin. Each is that commit's change to the source files its lock entry lists, without upstream's test files, fixtures and README text. To move one to another commit, change its `base` and `commit` in the lock, then run `prepare.py --refresh ~/Development/kelchm/TensorFold` to rewrite the patch and `build/build.sh --record` to rebuild and record the binary.

The four fixes are Apache-2.0 like the kit, and each is kept byte for byte as its source published it, except `0015`, which is this lab's. The lock's `site_patches` records each one's source, author, licence and sha256, and `prepare.py` refuses a copy that differs. The kit closed PR #4 and reverted PR #2 for reasons that weren't technical; [#940](https://github.com/kelchm/home-lab/issues/940) has the history. The qualification ran on binary `9f28a902…`, whose `0015` refused whole-vocabulary sampling outright. The clamp replaced that refusal and was [checked on the Sparks](https://github.com/kelchm/home-lab/issues/940#issuecomment-6344709923) the same day.

Expected entry:

```json
{"id": "Qwen3.8-Flash-Next", "object": "model", "created": 0, "owned_by": "tensorfold",
 "context_length": 491520, "max_model_len": 491520,
 "reasoning": {"default_enabled": true,
               "supported_efforts": ["xhigh", "medium", "low", "none"]}}
```

`created` is the time of the request. `reasoning.default_effort` appears only when the server is started with `--reasoning-effort`, which this profile does not do. The pinned checkpoint's `chat_template.jinja` accepts `xhigh`, `medium` and `low` and defaults to `xhigh`. The server accepts seven efforts, `none`, `minimal`, `low`, `medium`, `high`, `xhigh` and `max`, and answers one that is not in `supported_efforts` with the nearest listed level, ties going higher. Chat requests of 8 tokens with no `chat_template_kwargs` echoed these on October 13, and again on October 22:

| `reasoning_effort` sent | Echoed `reasoning_effort` | Echoed `enable_thinking` |
| --- | --- | --- |
| Omitted | `null` | `true` |
| `none` | `none` | `false` |
| `minimal` | `low` | `true` |
| `low` | `low` | `true` |
| `medium` | `medium` | `true` |
| `high` | `xhigh` | `true` |
| `xhigh` | `xhigh` | `true` |
| `max` | `xhigh` | `true` |

`reasoning_effort: "none"` with `chat_template_kwargs: {"enable_thinking": true}` echoed thinking on with a null effort and produced reasoning text, so the switch wins over the effort. What a client must not read into the entry:

- `context_length` is the configured window. A request that sets `max_tokens` is refused when the prompt plus that budget exceeds it. Without `max_tokens`, the reply gets 32,768 tokens or the room the prompt leaves, whichever is less.
- The entry lists no input modalities. Vision and video on this server come from the kit's `0009-vision-python.patch`, which the discovery fields do not describe.
- The reasoning defaults describe `/v1/chat/completions` and `/v1/responses`. Raw `/v1/completions` and Anthropic `/v1/messages` keep their own thinking defaults.

v1.1 (`be3ed857`) is pinned over the kit's `main` (`6730319`). Besides the changelog, credits and README, `main` removes `tools/context_boundary.py` and reverts v1.1's admission change ([kit PR #2](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/pull/2)) in `patches/0007-serving.patch`:

- On `main`, a request is refused when its prompt, `max_tokens` and the draft window together exceed `--context`. v1.1 counts only the prompt and `max_tokens`. Both size an admitted request's memory from all three; the draft window is 16 rows at the kit's depth of 15, the margin [kit issue #1](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/1) found.
- A client that sends no `max_tokens` gets the reply budget above, so a prompt longer than the window less 32,768 fills the window exactly. At this window, `main` would refuse every such prompt from 458,737 to 491,519 tokens. v1.1 admits them, as `/v1/models` advertises.
- The changelog gives no defect as the reason for the revert, and `main` has no published image.

How the profile differs from the kit's own launch:

- **Launcher:** `start.sh` and `scripts/prepare.sh` never run, so their preflight, argument check, free-memory stop, memory watch while loading, saved logs and smoke test are absent. The kit refuses to start a rank on a Spark with less than about 102 GiB `MemAvailable` at its 1M window; here that is a manual check on both hosts before launch.
- **Rendezvous gate:** rank 0 loads and warms the vision tower before it opens its listener, and allows the helper 900 s, while SparkRun 0.3.10 waits a fixed 120 s for the port and then removes both containers. A boot that ends with `Head node failed to become ready` while the helper is still loading is this gate, not an engine fault; upstream starts rank 1 first and has no such gate.
- **Fabric:** SparkRun detects the NCCL interface, devices and GID index at launch; the kit's link detection does not run. The engine's one-shot RoCE all-gather takes its devices from `NCCL_IB_HCA` and, per upstream, leaves both ranks on NCCL when a rank cannot open RoCE. Each rank's log says which path is in use.
- **Container:** SparkRun clears the image entrypoint, so NVIDIA's entrypoint script does not run, and adds `--user root`, `no-new-privileges` and an open-file limit. Both ranks use host IPC and networking, all GPUs, `/dev/infiniband`, `IPC_LOCK` and unlimited locked memory.
- **Weights and caches:** each host keeps its own copy in SparkRun's cache, mounted at `/cache/huggingface`; upstream's documented setup has the worker read the head's over NFS. The CUDA cache goes to SparkRun's image- and model-keyed `/cache/runtime`.

### Inputs outside SparkRun

- **The rebuilt binary:** `out/tensorfold-native` in the mod, ignored by Git. `build/build.sh` builds it, and it goes to the head with the mod.
- **The image:** on both Sparks on October 22, as `5eb5d4d3…`. `docker images` doesn't list it, but `docker image inspect` with the lock's `image_id` finds it. If it's missing, pull it on the head by the lock's `image`; SparkRun copies it to the worker at launch.
- **Weights:** `nvidia/Qwen3.8-Flash-Next-NVFP4@fc694b54` in SparkRun's cache on both hosts, 25 files and 132,734,506,847 bytes each on October 22. Check free space under the cache on both hosts before a first launch.

### Build

The image is not rebuilt. [`build/build.sh`](mods/mia-tensorfold-qwen38fn-v1.1-discovery/build/build.sh) compiles `tensorfold-native` alone on any linux/arm64 Docker daemon, with no GPU, no `nvcc` and no NVIDIA base image. Its inputs are pinned:

- Ubuntu 24.04 by digest, with its `gcc`, `libc6-dev` and `libibverbs-dev` packages;
- Zig 0.17.0 by the kit's checksum;
- engine `db281878`, and the kit's nine patches at `be3ed857` by checksum;
- the six patches above;
- the CUDA fatbins, taken from the published image's own layer by digest and embedded with the engine's `-Dfatbins` option.

Before the six patches go on, the build compiles the kit's tree as it is. It stops unless that build's `.text`, `.rodata`, `.data` and `.eh_frame` equal the published binary's byte for byte, so the patched binary is the published program plus the six patches and nothing else. Matching takes two things:

- naming the CPU the published build detected, `cortex_a725` with the features Zig 0.17.0 reads from a Spark;
- having `infiniband/verbs.h` present. Without that header, the engine compiles a stub in place of its RoCE all-gather.

The result goes to the ignored `out/tensorfold-native`, and its sha256 is in the lock.

This route was chosen over the kit's own image build because the recipe keeps the published image digest, and with it the image's kernel set and the CUDA cache already on both hosts. Adding a patch to the kit's build instead changes its image hash. That means:

- a new 11 GiB-class image, built on the head from an NVIDIA base that is not on either Spark;
- a Triton kernel-set compile;
- a copy to the worker;
- a different SparkRun workload, with an empty runtime cache.

All of that for changes to the Zig server alone.

Checked on a workstation:

- Both discovery patches apply to engine `db281878` with the kit's nine patches applied, and regenerate byte for byte from the pinned commits.
- The unpatched rebuild matches the published binary in every loaded section. The two files differ only in compiler-numbered symbol names in the unloaded string table.
- The patched build takes about four minutes with that reference build. Three of four builds of the two discovery patches from an empty Docker cache gave the same binary. The fourth ran while another container shared the build VM: it had the same loaded sections and different unloaded symbol names. The lock check rejects such a file.
- The binary carries the RoCE proxy as the published one does. The `dlopen libibverbs.so.1` path and the `ibv_*` entry-point names are in it, and the stub's `built without libibverbs headers` message is not. Its only `NEEDED` library is `libc.so.6`, and it has no `RPATH` or `RUNPATH`.
- With all six patches, the engine's host tests (`zig build test`) pass 184, against 180 without the four fixes, and the server's (`zig/tests/server/zig_test.sh`) 56 and 17, against 53 and 17. The engine's tests include a draw over a flat row at `top_k` 248, which stays within the 248 best candidates for `top_p` 0.95 and 1. The server's include request-path tests:
  - with two ranks, `top_k` −1 with `top_p` 0.95, and with `top_p` 1, each reach the engine as `top_k` 248;
  - with one rank, `top_k` −1 stays off;
  - `top_k` 249 gets HTTP 400 with two ranks, while `top_k` 248 and greedy requests are served.

### Launch, stop and roll back

Merging this directory changes nothing on the Sparks. Each step below is manual. The stop in step 6 begins the outage.

1. On the workstation, confirm the binary: `python3 sparks/inference/sparkrun/mods/mia-tensorfold-qwen38fn-v1.1-discovery/prepare.py --check --require-binary`. If `out/` is missing, `build/build.sh` rebuilds it in about four minutes, and fails unless the result matches the lock.
2. On the head, confirm the image with `docker image inspect` on the lock's `image_id`. If it's missing, `docker pull` the lock's `image`.
3. Pass the [checks before a launch](#before-a-launch).
4. Copy the mod, with its `out/`, the shared gate and the recipe to the head. This does not touch the running containers, which hold their own copy of the mods they started with. `--delete` drops a file an earlier copy carried, which `prepare.py` would otherwise refuse:

   ```sh
   cd sparks/inference/sparkrun
   rsync -a --delete --exclude __pycache__ mods/mia-tensorfold-qwen38fn-v1.1-discovery mods/tensorfold-readiness kelchm@10.32.21.31:sparkrun/recipes/glm53/mods/
   rsync -a mia-tensorfold-qwen38fn-tp2/qwen38fn-nvfp4-tp2-mia-tf-v1.1-discovery-nvidia-480k.yaml kelchm@10.32.21.31:sparkrun/recipes/glm53/mia-tensorfold-qwen38fn-tp2/
   rsync -a tests/qwen_model_discovery.py kelchm@10.32.21.31:sparkrun/recipes/glm53/tests/
   ```

5. On the head, verify the copy, run the binary once on a Spark CPU without a GPU or network, and dry-run the recipe. The container is a throwaway from the pulled image. It should print the `usage: tensorfold-native` line, not `Illegal instruction`:

   ```sh
   export PATH="$HOME/.local/share/sparkrun-glm53-0310/bin:$PATH"
   cd ~/sparkrun/recipes/glm53
   mod="$PWD/mods/mia-tensorfold-qwen38fn-v1.1-discovery"
   python3 "$mod/prepare.py" --check --require-binary
   image=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"])' "$mod/upstream.lock.json")
   docker run --rm --pull never --network none -v "$mod/out:/site:ro" --entrypoint /site/tensorfold-native "$image"
   qwen="$PWD/mia-tensorfold-qwen38fn-tp2/qwen38fn-nvfp4-tp2-mia-tf-v1.1-discovery-nvidia-480k.yaml"
   sparkrun run "$qwen" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
   ```

6. Stop the running profile with the recipe that launched it. Then confirm on both hosts that no engine process remains and at least 102 GiB is available, the kit's preflight floor at its 1M window.
7. Launch. The shared gate runs once the head answers, so a launch with the wrong discovery fields, smoke replies, tool call or level renders fails here:

   ```sh
   sparkrun run "$qwen" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
   ```

8. Read both rank logs with `sparkrun logs "$qwen" --cluster sparks --all-sources`:
   - the control-link line, naming the head's CX7 address;
   - the load and the NCCL join;
   - `RoCE all-gather on 2 port(s)` on both ranks, with no `built without libibverbs headers` line;
   - the sequence-memory line, 31.45 and 31.72 GiB on the October 22 boots.

   Confirm each rank runs the rebuilt binary: `docker exec <container> sha256sum /opt/tensorfold-mod/bin/tensorfold-native` matches `binary_sha256` in the lock, on both hosts.

Then, from `~/sparkrun/recipes/glm53` on the head:

1. The entry: `python3 tests/qwen_model_discovery.py --context 491520` prints the entry and checks `context_length` and `max_model_len` against the window, that `default_max_tokens` is absent, and the reasoning object's consistency. Compare it with the expected entry above, `supported_efforts` order included.
2. The echoes: `python3 tests/qwen_model_discovery.py --context 491520 --completions` sends eight 8-token chat requests with no `chat_template_kwargs`.
   - One names no effort. Its `tensorfold.enable_thinking` must equal `default_enabled`, and its `tensorfold.reasoning_effort` must be null, since no `default_effort` is advertised.
   - Seven name each accepted effort. Each echo must be in `supported_efforts`, an effort listed there must echo itself, and thinking must be off exactly when the echo is `none`. Compare the other three with the table above.

   Then send one request with `reasoning_effort: "none"` and `chat_template_kwargs: {"enable_thinking": true}`. It should echo thinking on with a null effort, as recorded above.
3. The shared checks in [Validate](#validate), with the agent gate run once for each `--section` of `tools`, `isolation`, `cache`, `coding` and `cancellation`, each with its own `--output`, since this server refuses the `structured` section's fields. Then the kit's own [checks](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/blob/be3ed857ab1055ca47b36134d22691f00f2bb94a/tools), which talk to the server with the standard library: `exact.py`, `toolcheck.py`, `prompt_reuse.py 30000 4 --other` and `context_boundary.py --context 491520`.
4. One throughput point: replaying the October 8 decode request bodies, under `~/sparkrun/results/qwen38fn-v1.1/evaluation-20261008/` on the head, gave about 62, 138 and 310 tok/s at one, four and sixteen streams on October 22.
5. The window: retrieval prompts that fill the window less the reply. Send one as the first long request after the boot, and one after shorter long prompts. The configured window is the largest at which both pass twice (#940).

The head may still hold older Qwen recipes from earlier syncs, such as the retired `qwen38fn-nvfp4-tp2-mia-tf-v1.1-nvidia-1m.yaml`. Those on the kit's image share this profile's runtime, model, image, port, served name and parallelism, so SparkRun takes them for the same workload and runtime cache: `stop`, `logs` and `status` with one act on whichever is running, and `run` with the v1.1 file serves the image's own binary at a 1M window. Use this profile's path, and tell instances apart by `context_length` in `/v1/models` or by the binary each rank runs.

To stop it, use `sparkrun stop "$qwen" --cluster sparks`. To roll back, stop it, confirm both GPUs are free, and launch the profile that was serving with its own recipe and the same flags. Nothing is left to undo on the hosts except the copied files and the pulled image.

### Known limits

The six patches above address the kit's open issues #3, #6, #8 and #9. On the Sparks, #3's fix and #8's clamp are confirmed; nothing in the trials reached #6's or #9's failure.

- **Control-link stall** ([#3](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/3)). Without `0012`, a long prompt can deadlock the two ranks' control link. Rank 1 exits at `TF_TP_TIMEOUT_S` (300 s), and rank 0 follows. The reports disagree on where it starts:
  - 503,359 tokens stalled and 382,416 completed on `main`;
  - 288,012 stalled on `dbcaaed`, with every repeat;
  - here on October 8, 524,135 completed and 999,874 stalled, over the management link, after shorter prompts;
  - here on October 22, over CX7, the first long request after each boot stalled at 489,437, 390,775 and 260,504 tokens.
- **One failed prefill ends the server** ([#6](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/6), [#7](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/7)). A `NoRoom` on one rank after admission exits both ranks. It is reported with long prompts and a full prompt cache. With `0013`, a refusal on either rank fails only that request; a failure after both ranks agree still exits both, as #7 reports. SparkRun does not restart the ranks.
- **Whole-vocabulary sampling** ([#8](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/8)). Two ranks reach the drafts' nucleus exchange only with `top_k` off, and it can exit both ranks: with `top_p` 1, as reported, and with a `top_p` cut when the nucleus is wider than 1,024 candidates a rank, as at a high temperature. Without the clamp, `top_k` −1 with `top_p` 0.95 at temperature 5 exited both ranks here on its second request. With `0015`, a client that asks for the whole vocabulary gets the best 248 candidates instead. The server's own defaults, used when a client sends none, are `top_k` 20 and `top_p` 0.95.
- **Prompt-cache use-after-free** ([#9](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/9)). Rank 0 can segfault in `endPass` when the prompt cache evicts a state the same pass kept. It is reported with long prompts and a full cache, and with a small cache at 45–57K-token prompts. `0014` is the issue's fix, which one other operator reports stopped the crash.
- **A GPU stuck busy after both ranks exit.** It happened twice on October 22, both times to spark-2's GPU, rank 1's. [Operate and roll back](#operate-and-roll-back) has the recovery; check both GPUs after any crash, before the next launch.
- **Health:** this server's `/health` handler always answers `status: ok`, so SparkRun's probe sees a listening server, not a healthy decoder. The gate's replies and tool call are what show a working decoder.
- **Admission:** a request is admitted when its prompt plus its whole reply budget fits beside the others, and is refused otherwise.
- **Unsupported fields:** a `response_format` of `json_object` or `json_schema`, the `guided_*` fields, `logprobs: true` and `n` other than 1 answer HTTP 400.
- **Pictures:** image and video prompts are not kept, so each turn of a chat with pictures processes them again.
- **Capacity:** the kit's 16 streams and 10 GiB reserve, not the 8 GiB headroom goal the GLM profile works to. With two cold ~490K prompts on October 22, available memory stayed above 34.9 GiB on the head and 38.4 GiB on the worker.
- **Not established:** concurrent long prompts, long sessions growing together, coding quality, and stability beyond the October 22 trials. `long_sessions.py` has not run against this server.
- **YaRN:** the factor stays at the kit's 4. A factor of 2 would also cover this window, and is untried here.
- **Access and memory:** the endpoint has no API key, and no memory watchdog runs ([#944](https://github.com/kelchm/home-lab/issues/944)).

## DeepSeek-V4.1-Flash-1017 on bertholomus's Zig engine

`bertholomus-tensorfold-dsv41-tp2/` holds the profile that serves [`deepseek-ai/DeepSeek-V4.1-Flash-1017`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017) as `DeepSeek-V4.1-Flash-1017`, through [LibertAI's EXL3 2.9 bpw quant](https://huggingface.co/LibertAIDAI/DeepSeek-V4.1-Flash-1017-EXL3-2.9bpw/tree/0be63c3337efbb2a0061d5b8bb4006fa186d433f) on the native Zig engine of [`bertholomus/TensorFold`](https://github.com/bertholomus/TensorFold/tree/deepseek-v41-zig), branch `deepseek-v41-zig`. Upstream TensorFold refuses DeepSeek checkpoints until its whole-model gate lands ([ashhart/TensorFold#827](https://github.com/ashhart/TensorFold/pull/827)). It has served since its [launch on October 22](https://github.com/kelchm/home-lab/issues/942#issuecomment-6330745295), apart from other models' trial windows.

### Current profile

| Profile | Engine | Image | Window |
| --- | --- | --- | --- |
| `dsv41-1017-exl3-tp2-bth-tf-2970bcd-libertai-512k` | `2970bcd` with six patches | `a7845d4e…`, built here | 524,288, qualified to 524,139 prompt tokens |

Rank 0 runs `tensorfold-native serve` with the OpenAI and Anthropic routes; rank 1 runs `tf-dsv41-lanes`. Settings: four streams, a 32,768-token reply budget, thinking on, temperature 1.0, top-p 0.95, top-k 20, DSpark drafts, CUDA graphs and the Engram read-ahead. The ranks link over the CX7 cable, not the management network SparkRun's rendezvous address uses: `serve.py` takes the head's CX7 address from the adapter's `FABRIC_MASTER` and refuses to start without it. Both gather over both rails with RDMA on the devices and GID index SparkRun detects. The rank-1 pool equals the window.

The [mod](mods/bertholomus-tensorfold-dsv41-2970bcd/) builds the image and launches both ranks. `upstream.lock.json` pins the engine, the inputs, the kit, the Engram tables and every mod file. The patches:

| Patch | Source | Change |
| --- | --- | --- |
| `0001-models-discovery-483-819.patch` | `ashhart/TensorFold` #483 and #819, reduced to `zig/src/server` | `/v1/models` reports `context_length` and the reasoning efforts |
| `0002-call-gate.patch` | [Mia's 1017 recipe](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks), `zig/patches/0004-call-gate.patch` | `tool_choice` `"required"` and named functions force a call |
| `0003-rdma-idle-nap.patch` | Mia's recipe at [`a22ca69`](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/commit/a22ca693c965179923a8593ab34b72d7e065cc8b), `zig/patches/0010-rdma-idle-nap.patch` | The RDMA gather's proxy sleeps 100 µs between looks after 2 s idle instead of spinning a core on each rank; a nap of 0 or above 1,000,000 µs is refused |
| `0004-literal-added-tokens.patch` | [bertholomus/TensorFold#36](https://github.com/bertholomus/TensorFold/pull/36) at `c2c05641` | An added token a client quotes in a chat request, a tool result or a tool description is encoded as text; tokens the template writes stay control tokens |
| `0005-deepseek-efforts.patch` | This lab | The engine's efforts are DeepSeek's own `max` = 100, `high` = 75 and `low` = 50, and any other effort name goes to the nearest of them |
| `0006-literal-object-keys.patch` | This lab | `0004` also covers JSON object keys, such as a tool's parameter names and a tool call's argument names |

**Reasoning efforts.** DeepSeek's encoder (`encoding/encoding.py` in the checkpoint) names `low`, `high` and `max` and renders the chosen one as `Reasoning Effort: N` before the first turn when thinking; non-thinking is a separate mode. With `0005`, `/v1/models` lists `max`, `high`, `low` and `none`, highest first, and a request's other names are taken to the nearest listed level with ties going higher: `minimal` to `low`, `medium` to `high` and `xhigh` to `max`. A request's `none` turns thinking off unless the same request switches thinking on explicitly, with `chat_template_kwargs.enable_thinking` or the Anthropic `thinking` field; a request without an effort hears the template's default, `high`. The shared readiness gate checks the list in that order and renders each level and each other name through `/tokenize`.

At launch `serve.py` links three views under `/tmp/dsv41` from the read-only snapshots: the weights with the mod's `chat_template.jinja` (a port of DeepSeek's `encoding.py`), the [kernel kit](https://huggingface.co/bertholomus/DeepSeek-V4.1-Flash-TensorFold-TP2-2xGB10/tree/4996441a82d95b9a7853b01a1255e1dafc926387) with the 1017 image routing bias made from the weights and checked against the lock, and the Engram shards with their embed-only index. It sets the engine's `oom_score_adj` to 1000 so the kernel ends the engine before anything else when host memory runs out. Readiness is the shared gate (`mods/tensorfold-readiness`): healthy status with four lanes, the exact `/v1/models` entry, four concurrent replies, one tool call and the level renders.

### Inputs outside SparkRun

SparkRun distributes the weights and the kit. Two inputs are staged once per host:

- **Engram tables:** shards 47 and 48 of the 1017 checkpoint (190 GiB) at revision `1bae0e10`, as a partial snapshot in each host's cluster cache. Listing the repository in the recipe would download all 475 GiB. On each host: `hf download deepseek-ai/DeepSeek-V4.1-Flash-1017 config.json model-00047-of-00048.safetensors model-00048-of-00048.safetensors --revision 1bae0e1088993b9b82de08f59de5be5a34e42170 --cache-dir /opt/spark-cache/huggingface/hub`.
- **Rank caches and token map:** each rank's weight file (99 GiB) and `dsv41_token_map.json`, in the recipe's own runtime-cache root on each host, `/opt/spark-cache/runtime-inputs/tensorfold/LibertAIDAI__DeepSeek-V4.1-Flash-1017-EXL3-2.9bp-55410efc/`, mounted at `/cache/runtime`. They are keyed by the weights, so an engine rebuild reuses them, and no other model's launch ages them out of that root.

The `deepseek-v41-tp2` Python engine in the image writes the rank inputs on its first start. With no workload on either GPU, run this on both hosts at once, the head with `RANK=0` first and then the worker with `RANK=1`, and stop both containers once each host has `tf-ds-rank/` and `dsv41_token_map.json`:

```sh
RANK=0  # 1 on the worker
hub=/opt/spark-cache/huggingface/hub
weights=$hub/models--LibertAIDAI--DeepSeek-V4.1-Flash-1017-EXL3-2.9bpw/snapshots/0be63c3337efbb2a0061d5b8bb4006fa186d433f
base=$hub/models--deepseek-ai--DeepSeek-V4.1-Flash-1017/snapshots/1bae0e1088993b9b82de08f59de5be5a34e42170
inputs=/opt/spark-cache/runtime-inputs/tensorfold/LibertAIDAI__DeepSeek-V4.1-Flash-1017-EXL3-2.9bp-55410efc
engram=/opt/spark-cache/runtime-inputs/engram-1017  # the shards and the mod's embed-only index, by hard link
mkdir -p "$inputs" "$engram"
for f in config.json model-00047-of-00048.safetensors model-00048-of-00048.safetensors; do ln -f "$(readlink -f "$base/$f")" "$engram/$f"; done
cp ~/sparkrun/recipes/glm53/mods/bertholomus-tensorfold-dsv41-2970bcd/engram.index.json "$engram/model.safetensors.index.json"
docker run -d --name dsv41-inputs --gpus all --network host --ipc host --ulimit memlock=-1 --ulimit stack=67108864 \
  --cap-add IPC_LOCK --device /dev/infiniband -v "$hub:$hub:ro" -v "$engram:/engram:ro" -v "$inputs:/inputs" \
  -e TF_DS_REPLAY=1 -e TF_DS_PREFILL_CHUNK=2048 -e TF_DS_RANK_CACHE=/inputs/tf-ds-rank -e TF_DS_RANK_CACHE_READERS=32 \
  -e TF_DS_ENGRAM=/engram -e TF_DS_TOKEN_MAP=/inputs/dsv41_token_map.json -e TF_RDMA_GID_INDEX=3 \
  -e NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0 -e NCCL_IB_GID_INDEX=3 -e NCCL_SOCKET_IFNAME=enp1s0f0np0 \
  dsv41-tf:2970bcd-4691715-e1c6ebe02e2a /usr/local/bin/tensorfold serve "$weights" --tp 2 --rank "$RANK" \
  --master 198.19.240.11 --host 127.0.0.1 --port 18891 --context 1048576 --vision --parallel 4 --mtp-drafts 5
```

These are the settings that wrote the current inputs on October 17, with this layout's paths.

### Build

The image is built on any linux/arm64 Docker daemon, never on a serving Spark, where the Zig and `nvcc` build would compete with the engine for host memory. The mod's `build/build.sh` fetches the engine and inputs at the lock's commits, applies the patches and builds on NVIDIA PyTorch 26.07 by digest, on the workstation's Colima `tf-build` profile:

```sh
DOCKER_CONTEXT=colima-tf-build sparks/inference/sparkrun/mods/bertholomus-tensorfold-dsv41-2970bcd/build/build.sh
```

The recipe's `container` and the lock's `image_id` are the image ID the head reports, which is the image's config digest; Colima's containerd store shows the index digest instead. To read it before loading, `colima ssh -p tf-build -- sudo ctr -n moby images ls` gives the index digest; `ctr -n moby content get` on that lists the arm64 manifest, and that manifest's `config.digest` is the ID. The October 21 build is `sha256:a7845d4e…`. A rebuild can produce another ID; record it in both before launching.

### Launch, stop and roll back

From `~/sparkrun/recipes/glm53` on the head, after syncing this directory as in [Deploy](#deploy) and passing the [checks before a launch](#before-a-launch). Stop the running instance with its own recipe before loading the image, so the 37 GB load never runs beside a serving engine on the head's unified memory:

```sh
dsv41="$PWD/bertholomus-tensorfold-dsv41-tp2/dsv41-1017-exl3-tp2-bth-tf-2970bcd-libertai-512k.yaml"
running="$PWD/<family>/<profile>.yaml"  # The recipe that launched the running instance.
python3 mods/tensorfold-readiness/prepare.py --check
python3 mods/bertholomus-tensorfold-dsv41-2970bcd/prepare.py --check
sparkrun stop "$running" --cluster sparks
apps=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader && ssh kelchm@10.32.21.32 nvidia-smi --query-compute-apps=pid --format=csv,noheader) && test -z "$apps"  # No compute process on either GPU.
# If the image isn't on the head, load it now, from the workstation:
#   DOCKER_CONTEXT=colima-tf-build docker save dsv41-tf:2970bcd-4691715-e1c6ebe02e2a | ssh kelchm@10.32.21.31 docker load
docker image inspect --format '{{.Id}}' "$(awk '$1 == "container:" { print $2 }' "$dsv41")"  # Fails unless the recipe's pinned image is here.
sparkrun run "$dsv41" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
sparkrun run "$dsv41" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
sparkrun logs "$dsv41" --cluster sparks --all-sources
# When stopping or replacing the workload:
sparkrun stop "$dsv41" --cluster sparks
```

After a boot, run the shared checks in [Validate](#validate). [`tests/dsv41_chat_template.py`](tests/dsv41_chat_template.py) compares the mod's template with DeepSeek's four text reference renderings in the image's Python template engine.

The rollback is Qwen's profile, `qwen38fn-nvfp4-tp2-mia-tf-v1.1-discovery-nvidia-480k`: stop DeepSeek with its own recipe and launch Qwen as [its section](#qwen38-flash-next-on-mias-zig-engine-kit) describes. It is another model, so clients that read `/v1/models` follow it and clients with a fixed entry need Qwen's.

### Known limits

- **Quoted control tokens:** `0004` and `0006` keep control-token spellings in request text, tool results, tool descriptions and JSON object keys as text on input; text the model writes is not covered. Keys of one JSON object that differ only by private-use characters U+E000 to U+E03A or U+FFFD collapse into one key in the rendered prompt; the request's own tools and arguments are unchanged.
- **Prompt cache:** it evicts kept conversations oldest first, so an agent's idle main conversation goes before finished subagents' ([#27](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/issues/27) there).
- **Forced calls:** a request with `tool_choice: "required"` or a named function reads every token synchronously, even after the call gate (`0002`) has forced the call's opening, so it loses decode overlap for the call's arguments. Other requests are unaffected, and `0002` stays verbatim from Mia's recipe.
- **Scheduling flags:** the engine's piecewise prompt passes (`TF_FILL_CHUNKS`) stay off. Mia's comparable interleave starved several queued long prompts ([#18](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/issues/18) there), and a long prompt pass holds other streams' tokens until it finishes. The engine's opt-in `TF_DS_SOLO_OVERHEAD` and `TF_DS_NO_COPIES` also stay off until measured here.
- **Window and memory:** the head keeps about 6 GiB available with the model loaded, and its low was 5.96 GiB during the 524,139-token prompt on October 22. Mia reports 512K as the practical limit for a head like this one. No memory watchdog runs.
- **Access:** the endpoint has no API key.

## Deploy

The saved `sparks` cluster uses head `10.32.21.31`, worker `10.32.21.32`, SSH user `kelchm`, cache `/opt/spark-cache/huggingface`, greedy placement and management-network transfers, with `transfer_mode: auto`. SparkRun detects both CX7 rails for inference, so recipes name no interfaces, devices or addresses. Its rendezvous uses the management address; spark-1's `known_hosts` entries for the CX7 addresses give the adapter the head's fabric address as well ([host notes](../../README.md#sparkrun-operation)).

Copy this directory to the head, keeping the mods beside the recipes:

```sh
rsync -a --exclude __pycache__ sparks/inference/sparkrun/ kelchm@10.32.21.31:sparkrun/recipes/glm53/
ssh kelchm@10.32.21.31
export PATH="$HOME/.local/share/sparkrun-glm53-0310/bin:$PATH"
cd ~/sparkrun/recipes/glm53
```

The sync does not delete: a recipe or mod removed from the repository stays on the head if an earlier sync put it there, and its `prepare.py --check` can fail because its inputs are gone. Select recipes by their path, and check only the mods a recipe declares. The directory keeps its `glm53` name until [#932](https://github.com/kelchm/home-lab/issues/932) settles the host layout.

SparkRun downloads each recipe's pinned snapshots into the HF cache and copies them to the worker, reusing snapshots already present. With the head as its own control host and `transfer_mode: auto`, that download first fills the SSH user's `~/.cache/huggingface`, not the cluster cache, so a snapshot staged only under `/opt/spark-cache/huggingface` is fetched again at launch, after the previous workload has stopped. When staging weights ahead of a switch, make the snapshot visible in the home cache as well, and confirm SparkRun's download step reports it cached before stopping anything; the two caches share a filesystem, so `cp -al` links it across without a second copy. SparkRun clears page cache after distribution; if its sudo operation is unavailable, use `sparkrun setup clear-cache --cluster sparks --save-sudo`.

Published images are pinned by digest. An image built here is pinned by the image ID the head reports: build it on a linux/arm64 Docker daemon off the Sparks, load it on the head, and SparkRun copies it to the worker at launch. A rebuild can produce another ID; record it in the recipe and the mod's lock before launching.

For a fresh control host, first provision Docker and SSH access and the cache directory on both hosts, owned by the SSH user. Install the control tools and create the cluster:

```sh
python3 -m venv ~/.local/share/sparkrun-glm53-0310
~/.local/share/sparkrun-glm53-0310/bin/pip install sparkrun==0.3.10 uv==0.12.6
export PATH="$HOME/.local/share/sparkrun-glm53-0310/bin:$PATH"
sparkrun cluster create sparks --hosts 10.32.21.31,10.32.21.32 --user kelchm --cache-dir /opt/spark-cache/huggingface --transfer-mode auto --transfer-interface mgmt --scheduler greedy
```

Keep this venv on PATH for the commands here; the head's older SparkRun 0.3.9 in `~/.local/bin` stays untouched. Benchmarks need `uvx` on PATH.

### Enable TensorFold in SparkRun

Merge these keys into the head's `~/.config/sparkrun/config.yaml`, preserving existing configuration. Use the actual absolute path where this directory was staged; the head has used the path below since October 7:

```yaml
features:
  core.external_plugins: true
plugins:
  paths:
    - /home/kelchm/sparkrun/recipes/glm53/plugins
```

The [adapter](plugins/tensorfold/__init__.py) requires exactly two TP2 ranks with PP1 and uses SparkRun's native launcher, rendezvous gate, cache mounts, log sources and stop operation. It names no model or engine: a recipe that leaves out `defaults.entrypoint`, `served_model_name` or `context` fails validation. Each rank gets `RANK`, `MASTER`, `MASTER_PORT`, `MODEL_PATH` (the immutable HF snapshot under `/cache/huggingface/hub`), `CONTEXT`, `SERVED_NAME`, `HOST`, `PORT` and, when set, `DRAFTER`, `MAX_TOKENS`, `EXTRA_ARGS`, `MTP_DRAFTS` and `NO_DRAFTS`. Rank 0 must listen on `MASTER_PORT`, which SparkRun waits on before it starts rank 1. When SparkRun reaches the head over a CX7 address, `FABRIC_MASTER` carries that address, for engines whose ranks link over the fabric rather than the rendezvous network. CUDA, Triton and Torch kernel caches go to SparkRun's persistent `/cache/runtime`; anything engine-specific is set by its own recipe and mod. No upstream two-host launcher or watchdog is installed.

Each mod pins its image, weights and inputs in `upstream.lock.json`, and its `run.sh` starts with `prepare.py --check`, which refuses a changed, missing or linked file. The contract also sets a common layout, which every profile follows: the rank launcher at `/usr/local/bin/tensorfold-entrypoint`, static files under `/opt/tensorfold-mod/`, the engine's `oom_score_adj` at 1000, and no lab addresses, host names or paths in recipes or mods. The one exception is DeepSeek's recipe, which names its dedicated runtime-cache root under `/opt/spark-cache` until [#932](https://github.com/kelchm/home-lab/issues/932) settles the host layout.

Profiles share one readiness gate, the [`tensorfold-readiness` mod](mods/tensorfold-readiness/), listed beside the engine's own mod and called from `post_exec` as `python3 /opt/tensorfold-readiness/readiness.py --model NAME --context WINDOW --lanes STREAMS --efforts LEVELS`. It requires health in the server's own format: the Zig servers' `status`, `warming` and `max_batch_size`, or Mia's Python server's strict `ok`, streams and window, plus a pool floor when the profile passes `--min-pool-tokens`. It also requires a `/v1/models` entry with `owned_by: tensorfold`, `context_length` and `max_model_len` equal to the window, and exactly the profile's reasoning levels, in the server's order. It then needs one correct concurrent reply per stream and one tool call. Where the server has `/tokenize`, it also checks that each advertised level renders its own prompt and that every other standard level name renders as the nearest of them. `--min-pool-tokens` and `--warmup-words` are optional, for the Python server's pool floor and shape warmup. SparkRun 0.3.10's own readiness probe checks HTTP status only, and it discards a passing hook's output; to keep the gate's output, run the same command once more against the live server from the head's rank container.

## Operate and roll back

From `~/sparkrun/recipes/glm53` on the head:

```sh
recipe="$PWD/<family>/<profile>.yaml"  # The recipe that launched the running instance.
sparkrun status --cluster sparks
sparkrun logs "$recipe" --cluster sparks --all-sources
# When stopping or replacing the workload:
sparkrun stop "$recipe" --cluster sparks
```

Without `--all-sources`, `logs` reads only rank 0. `run --no-follow` waits for readiness because every recipe has `post_exec`. A failed hook makes the command fail but does not stop the server; read the logs and stop the failed instance before rolling back. Containers need an explicit SparkRun launch after a host reboot.

SparkRun 0.3.10 finds a workload for `stop`, `logs` and relaunch by its runtime, model, image, port, served name and parallelism, not by filename, mod path or hooks. A recipe that differs in any of those does not find another's instance, and with the greedy scheduler `run` does not check for one either, so stopping the previous profile first, with the recipe that launched it, is a manual step. Never overlap inference stacks on these GPUs.

Before an upgrade, keep the exact working recipe, its mods and the adapter outside the checkout, under `~/sparkrun/rollback/`, and dry-run the rollback recipe against the synced tree before stopping anything. The rollback is then: stop the new instance with its own recipe, confirm both GPUs are free, and launch the kept recipe with the same cluster and flags.

A rank that dies mid-collective can leave a kernel running on its GPU with no process attached: `nvidia-smi` reads about 96% utilization, an SM clock near 1.1 GHz and about 4.7 W with no compute process, where an idle GB10 reads 0% at 208 MHz in P8. To clear it, stop the host's GPU exporter container, `monitoring-gpu-gpu-exporter-1`, and `nvidia-persistenced`, so nothing holds the GPU and the driver tears down its state. Wait about 20 s, then start both again and confirm 0% at 208 MHz before the next launch. `nvidia-smi -r` refuses on these hosts because the GPU is the primary one. Reboot only if stopping the daemon doesn't clear it. This cleared spark-2 on 2026-10-22.

The endpoint has no API key ([#943](https://github.com/kelchm/home-lab/issues/943)). spark-1's firewall admits port 8888 from the workstation, `10.32.10.244`, and from loopback, which the metrics scrape and sparkDash use. That rule is not persistent; check access after a host reboot or a workstation address change.

### Before a launch

Every switch of the served model starts from these checks:

- the running instance idle, with nothing in flight in its `/health`;
- no other GPU workload on either host;
- both management links up at 10GbE, with their carrier counters recorded, because the worker's link drops for seconds at a time ([#649](https://github.com/kelchm/home-lab/issues/649));
- both CX7 rails passing the check below;
- the recipe's mods passing `prepare.py --check`, and a dry run of the recipe.

After stopping the old instance, both hosts should have about 116 GiB available. From the stop until the trial ends, sample both hosts every 3 s: `MemAvailable`, both management links' carrier counters and new kernel-log lines. Aim for 8 GiB available on each host. Treat a sampler that stops as samples down.

- **From the stop until the trial ends,** stop the candidate on an allocation, NVRM, Xid, RoCE or OOM line in either kernel log, a rank that exits or restarts, HTTP 503 from `/health`, or headroom below 2 GiB for three samples in a row. Stop it too if readiness has not passed within 15 minutes. A nonzero launch or a failed readiness hook leaves the server running, so stopping it is always a separate step.
- **A management link or the samples down for 30 s:** during the boot, stop the candidate; once it is ready, end the load and leave the server running. A drop that recovers is recorded, and timings within 15 s of it are excluded.
- **Stopping:** use the recipe that launched the candidate, then confirm both GPUs are idle. Record each rank's container ID when it starts. SparkRun has no stop by ID and its recipe also matches a replacement, so if a rank was replaced or a host rebooted, `docker stop` the recorded containers by ID instead; that is the one exception.
- **Before a planned stop or launch,** require 60 s of both links up at 10GbE with unchanged carrier counters, and wait out another 60 s after any drop. A stop the rules above call for never waits for this.

The rail check pairs each host's active RDMA devices by list position, as Mia's GLM engine pairs the ranks' devices, and sends unfragmentable full-size pings both ways between each pair's addresses. It changes nothing. It fails if either host's inventory fails, if a host has other than two active devices or the two hosts list different devices at the same position, or if any pair's ping fails either way. A failure holds the launch. After a boot, compare each rank's `NCCL_IB_HCA` between the hosts, from each engine's environment, and read the rail count from rank 0's log, since rank 1 prints none; on October 8 both ranks had `NCCL_IB_HCA=roceP2p1s0f0,rocep1s0f0`, two ports at 200G on `198.19.241.0/24` and `198.19.240.0/24` with MTU 9000.

```bash
rails() { for p in /sys/class/infiniband/*; do grep -q ACTIVE "$p/ports/1/state" 2>/dev/null || continue; n=$(ls "$p/device/net" | head -n 1); a=$(ip -o -4 addr show dev "$n" | awk '{print $4; exit}' | cut -d/ -f1); echo "${p##*/} $n ${a:--} $(cat "/sys/class/net/$n/mtu")"; done; }
rail_check() {
  local worker=kelchm@10.32.21.32 mine theirs
  mine=$(rails) && theirs=$(ssh "$worker" "$(declare -f rails); rails") || { echo "FAIL rail inventory"; return 1; }
  if [[ $(wc -l <<<"$mine") -ne 2 || $(wc -l <<<"$theirs") -ne 2 ]]; then echo "FAIL expected two active RDMA devices on each host"; return 1; fi
  paste <(echo "$mine") <(echo "$theirs") | while read -r hh hn ha hm wh wn wa wm; do
    mtu=$(( hm < wm ? hm : wm ))
    if [[ "$hh" == "$wh" && "$ha" != - && "$wa" != - ]] && ping -c2 -W2 -I "$hn" -M do -s $((mtu - 28)) "$wa" >/dev/null 2>&1 &&
       ssh -n "$worker" "ping -c2 -W2 -I $wn -M do -s $((mtu - 28)) $ha >/dev/null 2>&1"; then
      echo "PASS rail $hh/$hn $ha <-> $wh/$wn $wa at MTU $mtu"
    else
      echo "FAIL rail $hh/$hn $ha <-> $wh/$wn $wa"
    fi
  done | tee /dev/stderr | awk '/^PASS/ { p++ } /^FAIL/ { f++ } END { exit !(p == 2 && f == 0) }'
}
rail_check
```

### Clients

Clients use the OpenAI-compatible API at `http://10.32.21.31:8888/v1`. Pi's `spark` extension (kelchm/dotfiles#35) configures itself from `/v1/models`: the served name, `context_length` and `reasoning.supported_efforts`. OpenCode carries a fixed entry per model in the workstation's `~/.config/opencode/opencode.jsonc`, under a Spark provider using `@ai-sdk/openai-compatible`; set the entry's context and output limits to the profile's window and reply budget, and its variants to the profile's levels. The provider options:

```json
{
  "baseURL": "http://10.32.21.31:8888/v1",
  "chunkTimeout": 1800000,
  "headerTimeout": 120000,
  "timeout": false,
  "includeUsage": true
}
```

The 30-minute chunk timeout covers cold prefill: a four-stream 250k batch on the GLM server took more than ten minutes before content, and that server sends nothing between its first role chunk and generation. The two-minute header timeout covers request preparation, and `timeout: false` avoids a separate overall deadline; a stalled stream is still cancelled. OpenCode 1.18.33, the version checked, caps output at 32,000 tokens internally even when a model declares more.

Where a server reports `usage.prompt_tokens_details.cached_tokens`, `usage.prompt_tokens` includes both cached and fresh input; a missing breakdown does not mean every prompt token was processed fresh. To check a client's accounting, repeat an identical long prompt while its prefix is still cached and confirm a warm response reports nonzero cached tokens; for streaming requests send `"stream_options": {"include_usage": true}` and read the final usage chunk. Then confirm the same call's cache reads reach OpenCode's saved `tokens.cache.read` and Codeburn's cache breakdown. Server metrics alone do not show that the client captured them.

## Validate

Run these against the running instance, with no other workload submitting requests. Use a working directory outside the recipe checkout for reports and the evaluator's local database:

```sh
mkdir -p ~/sparkrun/results
cd ~/sparkrun/results
recipe=~/sparkrun/recipes/glm53/<family>/<profile>.yaml  # The recipe that launched the running instance.
model=$(curl -fsS http://127.0.0.1:8888/v1/models | python3 -c 'import json, sys; print(json.load(sys.stdin)["data"][0]["id"])')
profiles=~/sparkrun/recipes/glm53/benchmarks
# The tokenizer from this recipe's pinned target snapshot on the head.
tokenizer=$(python3 -c 'import sys,yaml; r=yaml.safe_load(open(sys.argv[1])); print("/opt/spark-cache/huggingface/hub/models--" + r["model"].replace("/", "--") + "/snapshots/" + r["model_revision"])' "$recipe")
run="$(basename "$recipe" .yaml)-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$run"
sparkrun benchmark "$recipe" --cluster sparks --skip-run --no-stop --fresh --profile "$profiles/throughput.yaml" -b "tokenizer=$tokenizer" --output "$run/throughput.yaml"
uvx --from git+https://github.com/SeraphimSerapis/tool-eval-bench@v1.8.0 tool-eval-bench \
  --base-url http://127.0.0.1:8888/v1 --model "$model" \
  --short --no-think --parallel 1 --timeout 120 --max-turns 8 --temperature 0 \
  --reference-date 2026-03-20 --seed 20260930 --json-file "$run/tools.json"
python3 ~/sparkrun/recipes/glm53/tests/acceptance.py --model "$model"
python3 ~/sparkrun/recipes/glm53/tests/agent_acceptance.py --model "$model" --require-cache --output "$run/agent.json"
```

`--skip-run --no-stop` benchmarks the existing instance and does not switch recipes; check its image, target revision and health before recording a result. Give each boot or campaign its own receipt directory, and keep the recipe and mod lock beside the raw results so later profile changes cannot relabel them. Receipts stay outside git.

- **Throughput:** the [throughput profile](benchmarks/throughput.yaml) runs SparkRun's native llama-benchy for 2k and 32k prompts at C1 and C4, with warmup, two measured repetitions, temperature zero and a fixed seed. Its 256 tokens are an output budget, and thinking follows each template's default, so treat it as a supporting diagnostic. Pass the served model's tokenizer as above: an unreadable tokenizer can silently fall back to GPT-2 and invalidate comparisons. SparkRun 0.3.10 shell-quotes llama-benchy's arguments before running it, so the profile's `extra_body` is one comma-separated `key=value` string; a YAML list or a JSON-valued entry such as `chat_template_kwargs` does not reach the request as intended. Read the constructed arguments and the parsed request fields before adding settings.
- **Tools:** run `tool-eval-bench` v1.8.0 directly for its 15 core scenarios, because SparkRun 0.3.10's tool integration passes the model's repository ID instead of the served name, ignores a model override and shell-quotes JSON arguments. Keep the fixture date `2026-03-20`: the [v1.8.0 validators](https://github.com/SeraphimSerapis/tool-eval-bench/blob/7a1af7dffaade6b7b2d8f36ab8a454a7eccddcd7/src/tool_eval_bench/evals/scenarios.py) for TC-05 and TC-08 hard-code March 23 and 21 whatever `--reference-date` says. Read `scores.scenario_results` and the conversation report, not only the exit status. No results are submitted to Spark Arena.
- **Acceptance:** [`acceptance.py`](tests/acceptance.py) requires the colors of five distinct images in random order, and a correct short reply that begins within five seconds while another decode is still running. `--url` takes the server root or the API base ending in `/v1`.
- **Agent:** [`agent_acceptance.py`](tests/agent_acceptance.py) exercises streamed and non-streamed tools, null assistant content, thinking, interleaved histories with identical tool arguments, structured responses, cache accounting, bounded execution of generated Python, and cancellation. Read per-case results, including model failures and format limits, rather than counting a finished process as a pass. Generated code runs as restricted pure Python under CPU and memory limits.
- **Long sessions:** the [long-session client](benchmarks/long_sessions.py) runs on the head with `uv run --with llama-benchy==0.4.0 python`, `--model "$model"`, `--worker kelchm@10.32.21.32`, the pinned target's `tokenizer.json`, an absolute recipe path and output outside the checkout. `--suite full` covers C1, C2 and C4 controls, unrelated and shared prefixes, warm replay, edits and forks, 250k prompts with small and 32k output budgets, the 262k admission boundary and a short newcomer during long prefill; `growth`, `repeat`, `250k`, `boundary` and `ceiling` run parts of it, and `--campaign` names the run, new for each paired boot. It records per-stream time to first token, chunk gaps, usage and cache details, and both hosts' memory minima, and stops the recipe through SparkRun after three low-memory samples or lost telemetry. `--require-stable-links` aborts on a lost 10GbE carrier or any new drop. Payloads are synthetic repository text, so this is not a general long-context coding benchmark. Its cancellation can miss a silent stream ([#651](https://github.com/kelchm/home-lab/issues/651)).

## Update a pinned mod

Each mod is frozen: `upstream.lock.json` pins the image, the upstream revisions and every mod file, and `prepare.py --check` refuses any other. For an upstream update, review the kit's or engine's launcher, runtime files and tests together, and add a new versioned recipe and mod rather than editing the current ones, so the current profile stays as the rollback. Update the lock's revisions and hashes from the reviewed source, including every mod file's hash, then dry-run and validate the new profile on the Sparks. Do not update checksums to silence a failure.

SparkRun fingerprints the declared mod references, not their contents, so relaunch after a mod change and use `--fresh` for a new benchmark comparison.
