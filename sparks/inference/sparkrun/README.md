# Spark inference through SparkRun

These recipes serve one model at a time across the two DGX Sparks at `http://10.32.21.31:8888/v1`; since October 21 the running instance is [DeepSeek-V4.1-Flash-1017](#deepseek-v41-flash-1017-on-bertholomuss-zig-engine). SparkRun 0.3.10 owns model and image distribution, fabric detection, containers, runtime caches, readiness, logs, stop and benchmarking, through the TensorFold runtime adapter in `plugins/`. The hosts are outside Flux: merging a recipe changes nothing on them, and each launch is a manual step. Check host state before launching; other experiments can use either GPU.

| Model | Engine | Profiles | Work |
| --- | --- | --- | --- |
| [GLM-5.3-Flash](#glm-53-flash-on-mias-tensorfold-kit) | Mia's TensorFold kit, on TensorFold 0.6.0's Python CUDA engine | `mia-tensorfold-glm53-exl3-tp2/` | [#941](https://github.com/kelchm/home-lab/issues/941) |
| [Qwen3.8-Flash-Next](#qwen38-flash-next-on-mias-zig-engine-kit) | Mia's Zig-engine kit v1.1 | `mia-tensorfold-qwen38fn-tp2/` | [#940](https://github.com/kelchm/home-lab/issues/940) |
| [DeepSeek-V4.1-Flash-1017](#deepseek-v41-flash-1017-on-bertholomuss-zig-engine) | bertholomus's fork of TensorFold's Zig engine | `bertholomus-tensorfold-dsv41-tp2/` | [#942](https://github.com/kelchm/home-lab/issues/942) |

Every profile follows the contract in [#943](https://github.com/kelchm/home-lab/issues/943): the same endpoint and `/v1/models` fields, one shared readiness gate, one runtime adapter, and pinned mods. [Deploy](#deploy) covers the shared parts and [Operate and roll back](#operate-and-roll-back) the checks before any launch. Each model's section has the same parts: current profile, inputs outside SparkRun, build, launch, stop and roll back, and known limits. Trial records belong in each model's issue. The records of the retired GLM profiles (v1.2 to v1.8, the W20 engine and Mia's vLLM recipe), their September and October comparisons and the October 8 GLM v1.10 trial remain in [this runbook at `bc3fa9ad`](https://github.com/kelchm/home-lab/blob/bc3fa9ad32d1bf29fdba48f8abaf6429531f903b/sparks/inference/sparkrun/README.md).

## GLM-5.3-Flash on Mia's TensorFold kit

`mia-tensorfold-glm53-exl3-tp2/` holds two profiles of [Mia's two-Spark GLM kit](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold): TensorFold 0.6.0's Python CUDA engine with Mia's patches, serving Mia's 4 bpw EXL3 checkpoint across both Sparks. v1.10 last served from 16:07 EDT on October 8 until the Qwen trial that evening. `3fd60dfc` is meant to replace it ([#941](https://github.com/kelchm/home-lab/issues/941)) and has not booted.

### Current profile

| Profile | Kit | Image | Served name | State |
| --- | --- | --- | --- | --- |
| `glm53-exl3-tp2-mia-tf-v1.10-mia-1m` | v1.10, [`549fdc56`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/blob/549fdc562c477d477244d2df0590d6c14bda4889/CHANGELOG.md), 96 patches | `bc34d7d6…`, config `a24e8b6f…` | `GLM-5.3-Flash-EXL3` | Served October 8; the GLM rollback |
| `glm53-exl3-tp2-mia-tf-3fd60dfc-mia-1m` | `main` at [`3fd60dfc`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/commit/3fd60dfc836fab0f1ed0fd4f0afaf2a1d62b2aab), October 12, 107 patches | `14b5072e…`, config `9b007193…` | `GLM-5.3-Flash` | Not booted; replaces v1.10 once it passes #941's comparison |

Both kits patch the same engine commit, `c4646171`, and there is no numbered kit release after v1.10. The images are ARM64 and labelled with the kit's patch hash, `tf.patches=a1897d591f70` and `e66c657a433d`. The patched files in each image match a reconstruction of the engine plus the kit's patches byte for byte, for every file its mod guards or reads.

Both profiles share:

- **Weights:** [Mia's 4 bpw TensorFold checkpoint](https://huggingface.co/Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold/tree/078455ffe6472f9a52fbc1139f58b9db2881b25c) at `078455ff`, 83 shards and 175,642,267,944 bytes, and the DFlash2 drafter `incoai/GLM-5.3-Flash-DFlash2` at `bf582e4e`, 2,342,169,800 bytes.
- **Serving:** a 1,048,576-token window per request and a 32,768-token reply budget, four streams sharing one FP8 KV pool, and thinking and vision on. The engine sizes the pool from the memory free at startup, inside the kit's 14.5 GiB memory reserve and 12.5 GiB kept-prompt cap. v1.10's October 8 boot loaded 2,265,088 tokens; readiness requires at least 1.2M, room for four 262,144-token prompts plus their replies.
- **Launcher settings:** SparkRun never runs the kit's `start.sh`, and the engine's built-in defaults differ from it, so each recipe states every variable the kit's [`scripts/config.sh`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/blob/3fd60dfc836fab0f1ed0fd4f0afaf2a1d62b2aab/scripts/config.sh) exports in a clean environment: 38 for v1.10 and 39 for `3fd60dfc`. Each mod's lock records them as `launcher_env`, and the tests compare recipe and lock.
- **Site guards:** reasoning is restored only by the server's original call IDs, and a failed decoder or dead scheduler thread answers `/health` with HTTP 503 and `ok: false`. Each mod checks every guarded file's upstream hash before changing it, so it refuses guarded files that differ from the ones it was written for. That is not an image check: the guarded files are identical in both images, so the recipe's image pin is what pairs a recipe with its image. Clients that renumber or omit call IDs must keep their own reasoning.

What `3fd60dfc` changes against v1.10:

- **Engine:** eleven patches, `0098` to `0108`, speed up prompt processing, and the launcher adds `TF_GLM_FILL_ROWS=2048`, so a fill's chunk is the whole prompt chunk. Several of the CUDA attention changes target three Sparks; the kit keeps the two-Spark 32-head path on Triton.
- **Discovery:** a third site guard makes `/v1/models` report `owned_by: tensorfold`, the window as `context_length` and `max_model_len`, and `reasoning` with the thinking default and the levels `max`, `high`, `low` and `none`. The template defines `low` and `high` and renders anything else as `max`; the server maps `minimal` to `low`, `medium` to `high` and `xhigh` to `max`, and `none` turns thinking off.
- **Served name:** `GLM-5.3-Flash`, without the `-EXL3` suffix, like the other models' names.
- **Rendezvous:** the ranks meet on `FABRIC_MASTER`, the head's CX7 address, as the kit's launcher does, and fall back to SparkRun's management address only when the adapter has no fabric address.
- **Mod:** the rank launcher installs as `/usr/local/bin/tensorfold-entrypoint` and gives the engine `oom_score_adj` 1000. Readiness is the [shared gate](#enable-tensorfold-in-sparkrun), with the 1.2M pool floor and the C4 shape warmup at 128, 4,096 and 16,384 words that v1.10's own hook runs.

How both profiles differ from the kit's own launch:

- **No `start.sh`:** its preflight, memory warning and smoke test do not run, and neither do its recoveries: the retry with a smaller window when the requested one does not fit, and the `SPLIT=0` retry after a first NCCL connection failure ([upstream #36](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/36)). Either failure ends the launch here.
- **No rail test:** `start.sh` pings each RoCE rail at full size in both directions and drops a rail that does not answer. SparkRun passes every RDMA device whose port is active to NCCL, and the engine pairs the two ranks' devices by list position, so the [rail check](#before-a-launch) runs by hand first and a failure holds the launch.
- **Rank command:** the kit's serve flags plus `--backend cuda` and `--no-update-check`.
- **Fabric:** the ranks meet on SparkRun's `MASTER_PORT`, where the kit uses port 29551; v1.10's meet on the head's management address. NCCL's collectives run over RoCE on both rails, with the devices and GID index SparkRun detects.
- **Container:** SparkRun clears the image entrypoint and adds `--user root`, `no-new-privileges` and an open-file limit. Both ranks use host IPC and networking, all GPUs, `/dev/infiniband`, `IPC_LOCK`, unlimited locked memory and a 64 MiB stack.
- **Caches:** weights mount at `/cache/huggingface`, and compiled kernels go to SparkRun's image-keyed `/cache/runtime`, so each image's first boot compiles from empty.

### Inputs outside SparkRun

None. Mia publishes each image by digest, and SparkRun fetches the image and both snapshots and copies them to the worker. On October 8 both snapshots and v1.10's image were on both hosts; `3fd60dfc`'s image has not been fetched. Stage weights in the head's home cache before a switch, as [Deploy](#deploy) describes.

### Build

Nothing is built here. For a new kit revision, check the published image before pinning it, as both profiles were checked:

1. Evaluate the kit's `scripts/config.sh` in a clean environment, with no `scripts/local.sh` or `.env`, for the recipe's settings and the lock's `launcher_env`.
2. Apply the kit's patches to the pinned engine commit with `patch -p0`, check that they hash with the kit's extras line to the image's `tf.patches` label, and compare the image's patched-files layer with the reconstruction, at least for every file the mod guards or reads.
3. Add a new versioned recipe and mod with the new image, upstream hashes and guarded hashes, so the current profile stays as the rollback.

Before any live check, run the source regressions against the installed code inside each rank's container: the [Mia regression](tests/mia_tensorfold_site.py) for call-ID reasoning and HTTP health, the [capacity regression](tests/mia_tensorfold_capacity.py) for the queue cap and HTTP 429, and on v1.10 the [v1.10 regression](tests/mia_tensorfold_v110.py) for its launcher switches, streamed admission and Anthropic route. Feed each one unchanged to the rank's own Python, from the head for rank 0 and the worker for rank 1: `docker exec -i -e PYTHONDONTWRITEBYTECODE=1 <rank container> python3 -B - < tests/mia_tensorfold_site.py`. They use mocked handlers and decoders, so they are source evidence, not live traffic.

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

v1.10 is `glm53-exl3-tp2-mia-tf-v1.10-mia-1m.yaml`, with `mods/mia-tensorfold-v1.10`. `run --no-follow` returns after the readiness hook passes: four streams, the window, at least 1.2M pool tokens and the C4 shape warmup, and for `3fd60dfc` also the discovery fields, the four levels and their renders, a reply on every stream and a tool call. v1.10's October 8 boot took 349 s with the weights in place.

After a boot, run the shared checks in [Validate](#validate) and three for GLM: the [cut-call probe](tests/mia_tensorfold_cut_calls.py), which cuts a real tool call at its output limit and needs `--model`, `--output` and the pinned `tokenizer.json` as `--tokenizer`. It imports `tokenizers`, which the head's Python lacks, so run it from the workstation with `--url http://10.32.21.31:8888/v1`; a `/v1/messages` tool turn; and a request with a chunked body. The repository has no script for the last two.

The rollback from `3fd60dfc` is v1.10: stop `3fd60dfc` with its own recipe, then launch v1.10 the same way. The two differ in image and served name, so neither recipe's `stop` or `logs` finds the other's instance, and each image has its own kernel cache. A client with a fixed `GLM-5.3-Flash-EXL3` entry needs the new name; clients that read `/v1/models` follow it.

### Known limits

- **Measured on v1.10 only, on October 8:** the boot's lowest `MemAvailable` was 8.80 GiB on the head and 7.50 GiB on the worker. A finite 44-minute study completed 124 of 124 latency requests, including a 562,572-token prompt, with minima of 7.30 and 9.41 GiB, below the 8 GiB goal on the head. One four-stream batch stalled for 29 s around a worker management-link drop, unexplained.
- **Not established:** four long sessions growing together, a retrieval near 1M, full 32k replies, coding and quantization quality, stability beyond that study, and any speed comparison between v1.10 and `3fd60dfc`. #941's comparison measures what it can of these.
- **Rank coordination over the management network:** v1.10's rendezvous, and on both profiles some of NCCL's socket traffic, use the head's management address, and the worker's management link drops for seconds at a time ([#649](https://github.com/kelchm/home-lab/issues/649)).
- **Open kit reports** of memory growth and hangs under long multi-agent load: [#143](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/143), [#154](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/154), [#163](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/163) and [#197](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/issues/197).
- **Admission:** requests past the four streams queue without limit, because `TF_GLM_MAX_QUEUED` is empty.
- **Access and memory:** the endpoint has no API key, and no memory watchdog runs ([#944](https://github.com/kelchm/home-lab/issues/944)).

## Qwen3.8-Flash-Next on Mia's Zig-engine kit

The `mia-tensorfold-qwen38fn-tp2/` directory holds two profiles for Mia's [Qwen3.8-Flash-Next two-Spark kit](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/blob/be3ed857ab1055ca47b36134d22691f00f2bb94a/README.md), v1.1 of October 8. They are a different model, engine and API name from the GLM profiles above, not an upgrade of them: the kit serves text through TensorFold's Zig engine, `tensorfold-native`; a Python helper on rank 0 decodes pictures and video and runs the checkpoint's vision tower on the GPU. Both were staged from source and registry evidence. The NVFP4 profile booted on October 8, replacing GLM v1.10, which was stopped idle with its own recipe. The [evaluation](#october-8-nvfp4-evaluation) run against it ended with a request stalled on the two-rank control link and both engine processes gone. It was left down until the operator requested recovery at 23:11 EDT; the same pinned recipe is now serving again after readiness and a short LAN reply passed. A [continuation](#october-9-thinking-and-agent-continuation) on October 9 ran a thinking soak, an OpenCode accounting session and the agent gate on that instance with short prompts only; the window near 1M remains unqualified. The INT4-AR profile has not booted and its weights are not on the Sparks. A third file, the [model discovery variant](#model-discovery-variant), is the NVFP4 profile with a locally patched server binary; it has [served since October 13](#october-13-live-test).

First NVFP4 boot, October 8: with 117/116 GiB available on head/worker, the SparkRun dry run and launch passed. The weights downloaded in 28 minutes and synchronized to both caches. The vision helper was ready in 13.7 s, inside the 120 s rendezvous gate; the ranks agreed their settings, joined NCCL and opened the RoCE all-gather on two ports; the server reported loading in 91.0 s and answered HTTP 95 s after container start. It sized 30.99 GiB of sequence memory with 10 GiB kept free, above the 26.5 GiB upstream publishes for this quant. The readiness hook passed with 16 lanes reported and all 16 smoke replies correct. Afterwards 42/45 GiB was available and both management-link carrier counters were unchanged. Rank 0 listened for rank 1 on the management address, so the control link is not on the CX7 fabric. Through the workstation's OpenCode a plain reply and a file-read tool turn both completed, the second request reusing 10,530 of 10,610 prompt tokens. This paragraph is the boot and smoke result only; the measurements made on this instance afterwards are under [October 8 NVFP4 evaluation](#october-8-nvfp4-evaluation). Log: `~/sparkrun/results/qwen38fn-v1.1/first-boot-nvfp4.log` on the head.

| `qwen_profile` | Weights | API model | Published single / 4 / 16-stream prose | Published KV pool |
| --- | --- | --- | --- | --- |
| `qwen38fn-nvfp4-tp2-mia-tf-v1.1-nvidia-1m` | [`nvidia/Qwen3.8-Flash-Next-NVFP4`](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/tree/fc694b54fb0174e0913e6adf86691ef85a4ead47) `fc694b54`, the full model | `Qwen3.8-Flash-Next` | 67.4 / 152.2 / 390.1 tok/s | 26.5 GiB, about 3.9M tokens |
| `qwen38fn-int4ar-tp2-mia-tf-v1.1-azampatti-1m` | [`azampatti/Qwen3.8-Flash-Next-125B-A5B-INT4-AutoRound`](https://huggingface.co/azampatti/Qwen3.8-Flash-Next-125B-A5B-INT4-AutoRound/tree/1464274120d36a4d8fcaa934552334a7d83ce0fd) `14642741`, five routed experts a token instead of ten | `Qwen3.8-Flash-Next-INT4-AR` | 89.3 / 220.2 / 483.6 tok/s | 31.0 GiB, about 4.6M tokens |

The rates and pools in this table are the maintainer's, measured with sparkDash on October 7; prefill is published at roughly 2,500–2,750 tok/s for NVFP4 and 2,700–3,300 for INT4-AR, measured on an earlier build with a bf16 cache at eight streams. None of it is our measurement; ours, for NVFP4 only and on unmatched prompts, are in the [evaluation](#october-8-nvfp4-evaluation). The INT4-AR card reports about 10% lower general capability than the original on its authors' harness, so the two profiles compare weights and speed together, and a choice between them needs our own quality check. Filenames follow the GLM pattern with the quantization format in place of `exl3`: model, format, topology, kit release, quantization creator and request ceiling. `metadata` records the same fields separately.

Both recipes follow the pinned kit's defaults, read from [`scripts/config.sh`](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/blob/be3ed857ab1055ca47b36134d22691f00f2bb94a/scripts/config.sh) and [`start.sh`](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/blob/be3ed857ab1055ca47b36134d22691f00f2bb94a/start.sh) with no `scripts/local.sh` or `.env`: a 1,048,576-token request window through Qwen's YaRN factor 4 over the native 262,144, 16 streams, a 32,768 reply budget, thinking on, drafts from the checkpoint's own MTP head plus copy drafts, an FP8 KV cache, vision on with picture URLs off, a 10 GiB memory reserve and a 2 GiB vision workspace. The [launcher](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/blob/be3ed857ab1055ca47b36134d22691f00f2bb94a/start.sh) forwards every `TENSORFOLD_*` and `TF_FLASHNEXT_*` variable the config exports, and all ten appear in the recipe with the same values. No separate drafter model is downloaded. The FP8 cache is lossy: upstream reports 98.8% top-1 agreement with bf16 on teacher-forced replies, in exchange for a pool about 1.8–1.9 times larger.

Provenance, checked without a GPU:

- Kit commit `be3ed857` is the head of `main` and the merge that completes v1.1, whose changelog and `scripts/config.sh` pin this image. Upstream has published no tag or release. v1.0 of October 7 was staged here first and never booted; v1.1 replaces it because v1.0 could stop both ranks on a failed CUDA graph capture after about 15 minutes of many concurrent thinking requests, and refused a request whose prompt plus reply budget equalled the window. Between the two releases the launcher is unchanged and `scripts/config.sh` differs only in the image tag and digest.
- The image is manifest `e188128d…` (config `5eb5d4d3…`, ARM64, labelled `tf.patches=f3e1300f4f62` and `tf.kernels=present`). The kit's tag `zig-db28187-f3e1300f4f62` resolves to that digest, and the kit's own image-hash function, run on the pinned checkout, gives the labelled `f3e1300f4f62`.
- All nine patches apply to engine commit `db281878` on TensorFold's `zig-flashnext` branch with `git apply`. The image was not rebuilt or unpacked, so its contents are not compared with that source.
- Both checkpoint revisions are public, ungated and currently the head of their repositories: 25 files and 132,734,506,847 bytes for NVFP4, 123 files and 130,836,896,655 bytes for INT4-AR. That is metadata; no tensors were read.
- The [runtime adapter tests](tests/test_tensorfold_runtime.py) cover both profiles: recipe validation, the generated container environment, the rank command for each rank, the frozen mod and the readiness check, plus distinct runtime caches across every Mia profile, GLM and Qwen.

They adapt the upstream recipe to SparkRun and do not run its launcher. What differs:

- Launcher: `start.sh` and `scripts/prepare.sh` never run, so their preflight, argument check, free-memory stop, memory watch while loading, saved logs and smoke test are absent. The kit refuses to start a rank on a Spark with less than about 102 GiB `MemAvailable` at this window (about 4 GiB less for INT4-AR); here that is a manual check on both hosts before launch.
- Rank command: [`serve.py`](mods/mia-tensorfold-qwen38fn-v1.1/serve.py) passes the kit's serve flags to `tensorfold-native`. Rank 0 listens for rank 1 on the rendezvous address, which matches SparkRun's head-first start order, on port 25000 instead of the kit's 29551. The timing is unqualified: rank 0 loads and warms the vision tower before it opens that listener and allows the helper 900 s, while SparkRun 0.3.10 waits a fixed 120 s for the port and then removes both containers. A first boot that ends with `Head node failed to become ready` while the helper is still loading is this gate, not an engine fault; upstream starts rank 1 first and has no such gate.
- Fabric: SparkRun detects the NCCL interface, devices and GID index at launch; the kit's link detection does not run. The engine's one-shot RoCE all-gather takes its devices from `NCCL_IB_HCA` and, per upstream, leaves both ranks on NCCL when a rank cannot open RoCE. Read the rank logs on a first boot for which path is in use.
- Container: SparkRun clears the image entrypoint, so NVIDIA's entrypoint script does not run, and adds `--user root`, `no-new-privileges` and an open-file limit. Both use host IPC and networking, all GPUs, `/dev/infiniband`, `IPC_LOCK` and unlimited locked memory.
- Weights and caches: each host keeps its own copy in SparkRun's cache, mounted at `/cache/huggingface`; upstream's documented setup has the worker read the head's over NFS. The CUDA cache goes to SparkRun's image- and model-keyed `/cache/runtime`. The adapter's `GLM53_TF_*` variables are set, and nothing in the patched Zig source or the vision helper reads them.
- Readiness: the [hook](mods/mia-tensorfold-qwen38fn-v1.1/readiness.py) requires `/health` to report `status: ok`, not warming, the profile's API name and 16 lanes, then sends 16 short requests at once and checks each answer. The lane count is the configured value and the server queues requests, so this shows 16 concurrent submissions complete correctly, not that 16 lanes decoded together. The Zig server's `/health` reports no context length or pool size, so the hook cannot enforce a window or a pool floor; read the pool from the rank log.

What does not carry over from the GLM profiles:

- The site guards. The exact-call-ID reasoning guard and the HTTP 503 health mapping patch the Python engine and have no counterpart here. This server's `/health` handler always answers `status: ok`, so SparkRun's probe and the hook see a listening server, not a healthy decoder.
- Admission. A request is admitted when its prompt plus its whole reply budget fits beside the others and is refused otherwise, as with W20's reservations and unlike the Mia GLM kit's grow-on-decode extents. Upstream counts three full 1M conversations at once for NVFP4 and four for INT4-AR.
- Structured outputs. A `response_format` of `json_object` or `json_schema` and the `guided_*` fields answer HTTP 400, as do `logprobs: true` and `n` other than 1, so the agent gate's structured-response case cannot pass on this server.
- Prompt reuse for pictures. Image and video prompts are not kept, so each turn of a chat with pictures processes them again.
- Capacity tuning. These use the published 16 streams and 10 GiB reserve, not the C4 shape and the 8 GiB headroom goal the GLM evaluation was built around.

Each checkpoint is about 122–124 GiB on disk per host beside the retained GLM snapshots, and the first launch of a profile downloads and synchronizes it. Check free space under the cache on both hosts first. The image differs from every GLM profile's, so `stop` and `logs` with these files do not find a GLM instance and `run` does not check for one: stop the running profile with the recipe that launched it, confirm both GPUs are idle and both hosts have the memory above, then launch. From `~/sparkrun/recipes/glm53` on the head, with the external adapter enabled:

```sh
python3 mods/mia-tensorfold-qwen38fn-v1.1/prepare.py --check
qwen_profile=qwen38fn-nvfp4-tp2-mia-tf-v1.1-nvidia-1m  # Or qwen38fn-int4ar-tp2-mia-tf-v1.1-azampatti-1m.
qwen_recipe="$PWD/mia-tensorfold-qwen38fn-tp2/${qwen_profile}.yaml"
sparkrun run "$qwen_recipe" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
sparkrun run "$qwen_recipe" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
sparkrun logs "$qwen_recipe" --cluster sparks
sparkrun stop "$qwen_recipe" --cluster sparks
```

A first boot is a memory-fit and fabric check under the same abort rules as the GLM trials before any timing counts. One transport defect is open upstream in v1.1: a long record on the two-rank control link can deadlock, found with a 262,143-token prompt ([upstream #3](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/3), fix unmerged in [PR #4](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/pull/4)), so treat a stalled long prompt as that until it is ruled out. The October 8 evaluation ended on a stall of this shape at 999,874 tokens, after 261,902 and 524,135 tokens had completed. Clients need a model entry for the profile's API name. The workstation's OpenCode `spark` provider lists both Qwen names with a 1,048,576 context and 32,000 output limit, the same endpoint and timeouts as the GLM entry below, and no reasoning variants; the configuration shown below names only GLM. The kit's own [checks](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/blob/be3ed857ab1055ca47b36134d22691f00f2bb94a/tools) (`exact.py`, `toolcheck.py`, `prompt_reuse.py`, `context_boundary.py`, `needle.py`, `long_context.py`) talk to the running server with the standard library and are the natural first gate. The first four passed against the NVFP4 instance on October 8, and the retrieval cases there reuse `long_context.py`'s needle builder and scorer. The gates in this directory default to the GLM API name and tokenizer: `acceptance.py` passed with `--model Qwen3.8-Flash-Next`, the agent gate passed its five applicable sections on [October 9](#october-9-thinking-and-agent-continuation) with the same flag and no tokenizer argument, and the long-session client has not been run against this server.

### Model discovery variant

`qwen38fn-nvfp4-tp2-mia-tf-v1.1-discovery-nvidia-1m` is the NVFP4 profile with one file replaced inside the same pinned image: `tensorfold-native`, rebuilt so `GET /v1/models` reports the request window and the reasoning efforts. It exists so a client can configure itself from the server instead of a hand-written per-model table. Both halves are upstream TensorFold changes, released in 1.0.3 and 1.0.12, but the kit pins an engine commit that has neither, so this is a local backport until the kit moves its pin. A first build [served on October 13](#october-13-live-test) and passed every check except decode speed. A second build fixed that and served from October 14 until DeepSeek-V4.1-Flash-1017 replaced Qwen on October 17; it carries the upstream PR's draft, which also listed an `effort_map` that upstream dropped before merging. The build in this directory is the merged change and has not served yet. The v1.1 profile and mod are unchanged and are the rollback.

The [mod](mods/mia-tensorfold-qwen38fn-v1.1-discovery/) adds two patches to the server's `status_routes.zig` and `server.zig`, on top of the kit's nine, and nothing else in the engine changes:

| Patch | Source | Adds |
| --- | --- | --- |
| `0010-models-context-length.patch` | [`ashhart/TensorFold@51ad073f`](https://github.com/ashhart/TensorFold/commit/51ad073feadd81d04809edf8f0822cc5380a832c), upstream PR #483, released in 1.0.3 | `context_length`, `max_model_len` |
| `0011-models-reasoning.patch` | [`ashhart/TensorFold@60f27da0`](https://github.com/ashhart/TensorFold/commit/60f27da01cb86d076a078d3b4ae3e36c2f3b41c2), upstream PR #819, released in 1.0.12 | `reasoning` |

Each is that commit's change to the source files its lock entry lists; upstream's test files, fixtures and README text are left out. `upstream.lock.json` pins both commits and the checksum of every file in the mod. To move a patch to another commit, change its `base` and `commit` in the lock, then run `prepare.py --refresh ~/Development/kelchm/TensorFold` to rewrite the patch and `build/build.sh --record` to rebuild and record the binary.

Expected entry on this profile:

```json
{"id": "Qwen3.8-Flash-Next", "object": "model", "created": 0, "owned_by": "tensorfold",
 "context_length": 1048576, "max_model_len": 1048576,
 "reasoning": {"default_enabled": true,
               "supported_efforts": ["xhigh", "medium", "low", "none"]}}
```

`created` is the time of the request. `reasoning.default_effort` appears only when the server is started with `--reasoning-effort`, which this profile does not do. The server accepts seven efforts, `none`, `minimal`, `low`, `medium`, `high`, `xhigh` and `max`, and answers one that is not in `supported_efforts` with a listed level; the entry does not say which, and the [October 13 table](#october-13-live-test) records what this model does.

What a client must not read into these fields:

- `context_length` is the configured window, not a qualified one. The [October 8 evaluation](#october-8-nvfp4-evaluation) completed 524,135 prompt tokens and stalled both ranks at 999,874, and nothing between has been tried, so the ceiling a client can rely on is below 1,048,576 by an unmeasured amount. Admission itself allows the whole window on this release: v1.0 counted the draft window against `--context`, v1.1 fixed that, and `context_boundary.py` passed here. The patch has no setting to advertise less; a client that wants a margin applies its own.
- The entry lists no input modalities. Vision and video on this server come from the kit's `0009-vision-python.patch`, which the discovery fields do not describe.
- The reasoning defaults describe `/v1/chat/completions` and `/v1/responses`. Raw `/v1/completions` and Anthropic `/v1/messages` keep their own thinking defaults.

#### Build

The image is not rebuilt. [`build/build.sh`](mods/mia-tensorfold-qwen38fn-v1.1-discovery/build/build.sh) compiles `tensorfold-native` alone on any linux/arm64 Docker daemon, with no GPU, no `nvcc` and no NVIDIA base image, from pinned inputs: Ubuntu 24.04 by digest with its `gcc`, `libc6-dev` and `libibverbs-dev` packages, Zig 0.17.0 by the kit's checksum, engine `db281878`, the kit's nine patches at `be3ed857` by checksum, the two patches above, and the CUDA fatbins taken from the published image's own layer by digest and embedded with the engine's `-Dfatbins` option. Before the two patches go on, the build compiles the kit's tree as it is and stops unless its `.text`, `.rodata`, `.data` and `.eh_frame` equal the published binary's byte for byte, so the patched binary is the published program plus the two patches and nothing else. That takes naming the CPU the published build detected, `cortex_a725` with the features Zig 0.17.0 reads from a Spark, and having `infiniband/verbs.h` present: without that header the engine compiles a stub in place of its RoCE all-gather. The result goes to the git-ignored `out/tensorfold-native`, and its sha256 is in the lock. At container start [`run.sh`](mods/mia-tensorfold-qwen38fn-v1.1-discovery/run.sh) verifies the mod, refuses any image binary that is not the published `24a7bda5…`, and replaces it; a failure there fails the launch.

This route was chosen over the kit's own image build because the recipe keeps the published image digest. SparkRun addresses a workload and keys its runtime cache by image, so the variant reuses the v1.1 profile's image, kernel set and CUDA cache on both hosts, and nothing is pulled or loaded. Adding a patch to the kit's build instead changes its image hash: a new 11 GiB-class image built on the head from an NVIDIA base that is not on either Spark, a Triton kernel-set compile, a copy to the worker, a different SparkRun workload and an empty image-keyed cache, all for a change to one HTTP handler.

Checked on a workstation:

- Both patches apply to engine `db281878` with the kit's nine patches applied, and regenerate byte for byte from the pinned commits.
- On that tree the server unit tests pass, 53 and 17, including the five model-discovery tests.
- The unpatched rebuild matches the published binary in every loaded section; the two files differ only in compiler-numbered symbol names in the unloaded string table.
- The patched build takes about four minutes with that reference build. Three of four builds from an empty Docker cache produced `1713bf57…`. The fourth, run while another container shared the build VM, had the same loaded sections and different unloaded symbol names; the lock check rejects such a file, and rebuilding gives the recorded one.
- The binary carries the RoCE proxy as the published one does: the `dlopen libibverbs.so.1` path and the `ibv_*` entry-point names are in it, and the stub's `built without libibverbs headers` message is not.

The live results below are for the two earlier builds, `3dea815d…` of draft `4612b958` and `3f22bd80…` of draft `68911640`.

#### October 13 live test

The first build, `3dea815d…` of draft commit `4612b958`, built from this branch's commit `c7643951`, replaced the v1.1 instance at 20:45 EDT on October 13 by the steps below. Both hosts were idle with carrier counters at 12 and 1,432, the binary printed its usage line in a throwaway container on the head, and the v1.1 instance stopped in 3 s with 116 GiB available on each host. The API was down for 118 s, from 20:45:16 to 20:47:14; the launch took 112.0 s and its readiness hook passed, including the new window check. Sequence memory was 31.67 GiB. Both ranks ran the rebuilt binary by sha256.

`GET /v1/models` returned the entry expected for that draft: the one above plus `"default_max_tokens": 32768`, which the final draft dropped. `supported_efforts` matched in order. The pinned checkpoint's `chat_template.jinja` accepts `('xhigh', 'medium', 'low')` and defaults to `xhigh`, which is that list with `none` for the switch. Chat requests of 8 tokens with no `chat_template_kwargs` echoed:

| `reasoning_effort` sent | Advertised | Echoed `reasoning_effort` | Echoed `enable_thinking` |
| --- | --- | --- | --- |
| omitted | `default_enabled: true`, no `default_effort` | `null` | `true` |
| `none` | `none` | `none` | `false` |
| `minimal` | `low` | `low` | `true` |
| `low` | `low` | `low` | `true` |
| `medium` | `medium` | `medium` | `true` |
| `high` | `xhigh` | `xhigh` | `true` |
| `xhigh` | `xhigh` | `xhigh` | `true` |
| `max` | `xhigh` | `xhigh` | `true` |

One case the fields do not describe: `reasoning_effort: "none"` with `chat_template_kwargs: {"enable_thinking": true}` echoed thinking on with a null effort and produced reasoning text, so the switch wins over the effort.

The gates repeated from October 8 and 9 all passed: `exact.py`, with all 18 reply and token hashes equal to the October 8 run and 520 drafts accepted; `toolcheck.py` 3 of 3; `prompt_reuse.py 30000 4 --other` at 99.3% or better with a 9.82 s cold prefill of 31,557 tokens against 9.68 s; `context_boundary.py` 16 checks; `acceptance.py` with five images and a 0.140 s newcomer; and all 18 agent cases in the five applicable sections.

Decode was slower. Replaying the October 8 request bodies gave 56.25 and 55.73 tok/s at one stream against 62.58 and 61.18, 129.05 at four against 137.45 and 149.82, and 276.52 and 305.15 at sixteen against 312.95 and 316.93. Both rank logs say why: `RoCE: built without libibverbs headers: no RoCE all-gather`, with NCCL carrying every gather. Replies are the same bits either way, which the equal hashes show; the cost is speed, about 10% at one stream. That build had no `infiniband/verbs.h`, and the build above now does. The instance was left serving after the checks, idle, with 39 and 42 GiB available and the carrier counters unchanged. Receipts are on the head under `~/sparkrun/results/qwen38fn-v1.1/discovery-20261013/`.

The second build, `3f22bd80…` of draft `68911640` from branch commit `a4aefb8f`, replaced that instance at 00:46 EDT on October 14 by the same steps, after the same idle and link gates. The API was down for 120 s, from 00:46:50 to 00:48:50; the launch took 108.7 s and readiness passed. Both ranks ran `3f22bd80…` and logged `RoCE all-gather on 2 port(s)`, and sequence memory was 31.24 GiB. `GET /v1/models` returned the entry above plus the draft's `effort_map`, which matched the October 13 table, and no `default_max_tokens`. The eight echoes and the `none` plus `enable_thinking: true` probe gave the same results as the October 13 table. The same gates passed, with `exact.py` hashes again identical to October 8, `acceptance.py` with a 0.127 s newcomer, and all 18 agent cases. Replaying the October 8 bodies gave 61.61 and 61.43 tok/s at one stream, 137.35 at four, and 290.95 and 312.48 at sixteen, so decode is back in the October 8 range. The instance was left serving, idle, with 39 and 42 GiB available and the carrier counters unchanged. Receipts are under `~/sparkrun/results/qwen38fn-v1.1/discovery-20261014/` on the head.

#### Launch and rollback

Merging this directory changes nothing on the Sparks. Each step below is manual. The stop in step 6 begins the outage, which was 118 s on October 13.

1. On the workstation, confirm the binary: `python3 sparks/inference/sparkrun/mods/mia-tensorfold-qwen38fn-v1.1-discovery/prepare.py --check --require-binary`. If `out/` is missing, `build/build.sh` rebuilds it in about four minutes and fails unless the result matches the lock.
2. The published image has been on neither Spark since the October 17 switch. Pull it on the head first, `docker pull` with the `image` from the mod's lock; SparkRun copies it to the worker at launch.
3. Check both hosts with `task sparks:status` and the gates used for every trial here: no request in flight (`live.connections` and `live.waiting` zero in `/health`), no other GPU workload, both management links at 10GbE with carrier counters unchanged from 12 and 1,432, and nobody relying on the endpoint for the next ten minutes.
4. Copy only the new files to the head. This does not touch the running containers, which hold their own copy of the mod they started with. `--delete` drops a patch an earlier copy carried, which `prepare.py` would otherwise refuse:

   ```sh
   cd sparks/inference/sparkrun
   rsync -a --delete --exclude __pycache__ mods/mia-tensorfold-qwen38fn-v1.1-discovery kelchm@10.32.21.31:sparkrun/recipes/glm53/mods/
   rsync -a mia-tensorfold-qwen38fn-tp2/qwen38fn-nvfp4-tp2-mia-tf-v1.1-discovery-nvidia-1m.yaml kelchm@10.32.21.31:sparkrun/recipes/glm53/mia-tensorfold-qwen38fn-tp2/
   rsync -a tests/qwen_model_discovery.py kelchm@10.32.21.31:sparkrun/recipes/glm53/tests/
   ```

5. On the head, verify the copy, run the binary once on a Spark CPU without a GPU or network, and dry-run the recipe. The container is a throwaway from the image already on the head; it should print the `usage: tensorfold-native` line and not `Illegal instruction`:

   ```sh
   export PATH="$HOME/.local/share/sparkrun-glm53-0310/bin:$PATH"
   cd ~/sparkrun/recipes/glm53
   mod="$PWD/mods/mia-tensorfold-qwen38fn-v1.1-discovery"
   python3 "$mod/prepare.py" --check --require-binary
   image=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"])' "$mod/upstream.lock.json")
   docker run --rm --pull never --network none -v "$mod/out:/site:ro" --entrypoint /site/tensorfold-native "$image"
   current="$PWD/mia-tensorfold-qwen38fn-tp2/qwen38fn-nvfp4-tp2-mia-tf-v1.1-nvidia-1m.yaml"
   variant="$PWD/mia-tensorfold-qwen38fn-tp2/qwen38fn-nvfp4-tp2-mia-tf-v1.1-discovery-nvidia-1m.yaml"
   sparkrun run "$variant" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
   ```

6. Stop the running instance with the recipe that launched it, then confirm on both hosts that no engine process remains and at least 102 GiB is available:

   ```sh
   sparkrun stop "$current" --cluster sparks
   ```

7. Launch the variant. Its readiness hook is the v1.1 hook plus one check, that `/v1/models` reports the 1,048,576 window, so a launch that somehow served the published binary fails here. When the running instance is an earlier build of this variant, steps 6 and 7 use the same recipe file:

   ```sh
   sparkrun run "$variant" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
   ```

8. Read both rank logs for the load, the NCCL join, the RoCE all-gather opening on both ranks and the sequence-memory line, as on a first boot, and confirm each rank runs the rebuilt binary: `docker exec <container> sha256sum /opt/tensorfold/native/bin/tensorfold-native` matches `binary_sha256` in the lock on both hosts.

To roll back, stop with either recipe, confirm both GPUs are free, and launch `$current` with the same flags. That starts fresh containers from the same image, whose binary is the published one; nothing is left to undo on the hosts except the copied files. Both recipes are the same workload to SparkRun, so `stop`, `logs` and `status` with either file act on whichever is running and do not say which binary it is: tell them apart by `context_length` in `/v1/models` or by the binary's sha256.

#### Checks after launch

On the head, from `~/sparkrun/recipes/glm53`. The first is read-only; the rest send requests:

1. The entry: `python3 tests/qwen_model_discovery.py` checks `context_length` and `max_model_len` against the window, that `default_max_tokens` is absent, and the reasoning object's consistency, and prints the entry. Compare it with the expected entry above, `supported_efforts` order included.
2. The echoes: `python3 tests/qwen_model_discovery.py --completions` sends eight 8-token chat requests with no `chat_template_kwargs`. One names no effort: its `tensorfold.enable_thinking` must equal `default_enabled`, and its `tensorfold.reasoning_effort` must be null, since no `default_effort` is advertised. Seven name each accepted effort: each echo must be in `supported_efforts`, an effort listed there must echo itself, and thinking must be off exactly when the echo is `none`. Compare the other three with the October 13 table. Then one request with `reasoning_effort: "none"` and `chat_template_kwargs: {"enable_thinking": true}`, which on October 13 echoed thinking on with a null effort.
3. Nothing else moved: repeat the gates the [October 8](#october-8-nvfp4-evaluation) and [October 9](#october-9-thinking-and-agent-continuation) records used on v1.1, with the same commands and no long prompt. That is the kit's `exact.py`, `toolcheck.py`, `prompt_reuse.py 30000 4 --other` and `context_boundary.py --context 1048576`, then `acceptance.py --model Qwen3.8-Flash-Next` and `agent_acceptance.py --model Qwen3.8-Flash-Next --require-cache` for the tools, isolation, cache, coding and cancellation sections.
4. RoCE and one throughput point: both rank logs show the RoCE all-gather open and no `built without libibverbs headers` line, and the one-stream and 16-stream cases of the October 8 table come back near 61–63 and 313–317 tok/s, against 56 and 277–305 on October 13.

Follow-ups, not part of this variant: the v1.1 readiness hook still cannot enforce a window, because that server reports none; once the kit moves its engine pin to TensorFold 1.0.12 or later, the check added here can become the profile's normal gate and this mod can be retired. The Pi extension that motivated this ([kelchm/dotfiles#33](https://github.com/kelchm/dotfiles/pull/33)) still carries its per-model table until a server with these fields is the one running.

### October 8 NVFP4 evaluation

One read-only campaign ran on the evening of October 8 EDT against the instance from the first boot, at `http://localhost:8888/v1` on the head, with no stop, restart, relaunch or setting change during the campaign. It covered correctness gates, throughput at 1, 4, 8 and 16 streams, cold prefill and retrieval up to 524,135 prompt tokens, and then stopped: the 999,874-token retrieval request stalled before any reply token and ended in a transport error after 301 s, both engine processes exited, and the thinking soak and the OpenCode session that were to follow did not run. Every measurement used thinking off, although the recipe defaults to thinking on, so nothing here measures thinking. This is not a qualification of the profile, and it does not test every feature the kit advertises.

Every correctness gate passed, and all 15 needle checks in the three retrieval cases up to 524,135 prompt tokens were exact. Throughput was in the same broad range as the published figures. The one request near the 1M window stalled the two ranks' control link in the way [upstream #3](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/issues/3) describes and ended with the server down. The profile is therefore not qualified at its 1,048,576 window or for unattended use. That rests on one request: sizes between 524,135 and 999,874 tokens are untested, and one request per size below that is not a reliability result for those sizes. The instance was left down until the separately authorized recovery recorded below. Relying on the window needs a reviewed transport fix, [PR #4](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold/pull/4) or an equivalent, and a fresh trial that passes the ~1M case.

Correctness gates, all passed. The kit's tools are the pinned `be3ed857` copies:

- `exact.py`: six prompts at 160 tokens, temperature 0, thinking off, seed 4321. All 18 replies, serial, with drafts off and concurrent, matched in text and token hash, with 520 drafts accepted.
- `toolcheck.py`: 3 of 3, a nonstreamed call, a streamed call and a tool-result turn, with a typed array argument and call IDs preserved.
- `prompt_reuse.py 30000 4 --other`: 31,557 prompt tokens cold in 9.68 s of prefill, then four growing turns of 31,783, 32,010, 32,219 and 32,430 tokens reusing 31,550, 31,776, 32,003 and 32,212, at least 99.3% each.
- `context_boundary.py --context 1048576`: all 16 checks. It probes admission arithmetic with a 20-token prompt and a very large reply budget, so it is not a long-input result.
- `acceptance.py --model Qwen3.8-Flash-Next`: five random solid-colour 64×64 PNGs identified, and a short request arriving during a 1,024-token prose decode began its reply in 0.116 s.
- Video: a synthetic 128×128, 4 fps, 2 s clip, red then blue, was described in the right order, from 127 prompt tokens in a 4-token reply.

Throughput. Two repetitions at each concurrency in ascending order (1, 1, 4, 4, 8, 8, 16, 16), each stream a distinct 70–72-token database-tutorial prompt returning exactly 1,024 completion tokens at temperature 0, seed 20261008, with the default MTP and copy drafts on. The head-local standard-library streaming client retained request bodies, timestamped SSE events and final API usage. The rate is all completion tokens in the batch over the batch's elapsed time, so it includes the short prefill and is the conservative figure:

| Streams | Repetition 1, tok/s | Repetition 2, tok/s | Time to first token, s | Published, tok/s |
| --- | --- | --- | --- | --- |
| 1 | 62.58 | 61.18 | 0.12 | 67.4 |
| 4 | 137.45 | 149.82 | 0.12–0.34 | 152.2 |
| 8 | 213.19 | 212.37 | 0.12–0.43 | not published |
| 16 | 312.95 | 316.93 | 0.13–0.68 | 390.1 |

The published column is sparkDash on the maintainer's prompts, not these, so the two are not a matched comparison. Ours are lower at 1 and 16 streams and in the same broad range; two repetitions on one instance support no regression claim either way. The tutorial replies were counted, not scored, so this table says nothing about their quality. Sums of per-stream decode windows are kept in the receipts as diagnostics and are not aggregate rates, because the streams end at different times.

Long prompts. Each row is one cold request, zero cached tokens, temperature 0, thinking off. The prefill rows allow 32 reply tokens and got a two-token `OK`. The retrieval rows hide five key-value needles at depths 0.02, 0.25, 0.5, 0.75 and 0.98 with upstream's `long_context.py` builder, ask for all five in a 256-token budget and score with upstream's scorer. The builder's seed is 20261008 plus the target size, the generation seed is 20261008, and the three completed replies used 67, 66 and 67 tokens of the budget. Token counts are the server's own:

| Case | Prompt tokens | Server prefill, s | Client time to first token, s | Prefill, tok/s | Result |
| --- | --- | --- | --- | --- | --- |
| Prefill | 8,145 | 2.50 | 2.51 | 3,258 | `OK` |
| Prefill | 32,805 | 9.97 | 10.01 | 3,290 | `OK` |
| Prefill | 130,517 | 43.61 | 43.77 | 2,993 | `OK` |
| Retrieval | 130,886 | 44.48 | 44.63 | 2,943 | 5 of 5 exact, 45.5 s total |
| Retrieval | 261,902 | 94.87 | 95.15 | 2,761 | 5 of 5 exact, 96.1 s total |
| Retrieval | 524,135 | 221.45 | 222.22 | 2,367 | 5 of 5 exact, 223.9 s total |
| Retrieval | 999,874 | none | none | none | Stalled, then `LinkWriteFailed` at 301.18 s, no reply token |

Single-stream prefill fell from about 3,300 tok/s at 32k to about 2,370 at 524k. Upstream publishes roughly 2,500–2,750 for NVFP4 at eight streams with a bf16 cache on an earlier build, which is a different shape and not a comparison. One prompt per size shows that retrieval works at that length on that prompt; it is not a general quality result.

The stall. Before the last case both hosts were idle with 35.44 and 38.34 GiB available, which left 4.44 and 7.34 GiB after the 31 GiB estimated for the prompt, above the 2 GiB floor. The request carried 999,874 prompt tokens and a 256-token reply budget and produced no generated delta. Socket captures of the rank control connection, `10.32.21.31:25000` to `10.32.21.32:37436` on the management network, show rank 0 with 1,633,712 bytes queued and unsent and rank 1 with 1,243,464 bytes unread. Both figures and the 6,361,019 acknowledged bytes were identical at 22:43:13 and 22:43:31 EDT, the sender had been limited by the receiver's window for 99.6% of its busy time, and it had last sent about 86 s before the first capture, shortly after the request began. Neither management link's carrier count changed (12 and 1,432 throughout the campaign) and about 41 and 38 GiB were available, so neither low memory nor a carrier change was observed. The head's reading had risen from 35.44 GiB since the request began; the telemetry shows the rise and not its reason. This is strong evidence consistent with upstream #3's deadlock on a long control record. It is not proof: the socket state and the unpatched source fit that defect, and the blocked code path itself was not observed. No retry and no further inference request was sent.

Terminal state. The stream ended in an error 301.18 s after it began: `LinkWriteFailed`, with 999,874 prompt tokens and none generated. Rank 0's log records that the release was not sent to rank 1 (`FollowerGone`) and then `ended chatcmpl-502f84ff0d0397f43662bbb54379185c reason=error (LinkWriteFailed) prompt=999874 tokens=0 after=301.17s`. Rank 1 logged that a call had passed its deadline (`TF_TP_TIMEOUT_S`) with the other rank stalled, and exited; rank 0 logged that the other rank had closed the control link, and exited. At 22:47:28–29 EDT `ps` showed no engine process on either host and no vision helper, neither GPU listed a compute process, and port 8888 refused connections. Both containers were still running with their original start times, 01:59:53 and 01:59:54 UTC on October 9, and `OOMKilled: false`, each holding only its `sleep infinity` shell. The adapter keeps that shell alive, so a container Docker reports as up is no evidence of a live engine. 116.76 and 116.82 GiB were available. The worker still reported 96% GPU utilization with no process listed; the reading is kept as taken with no cause assigned, and utilization alone is not used here to call a GPU idle or busy.

Over the whole campaign the lowest available memory was 31.66 and 34.97 GiB and the highest swap use 0.22 and 0.11 GiB, and all 291 two-host memory/link samples completed. The 25 health connection failures were recorded after the engines exited. The kernel journal slices captured for the throughput step and the stall contain no entries, which shows that those slices are empty and does not prove the absence of a GPU fault. This campaign stopped, restarted and relaunched nothing: it ended with the instance down because its engines exited.

Not run in this campaign, because the stall ended it. The soak, the OpenCode session and the agent gate ran later, in the [October 9 continuation](#october-9-thinking-and-agent-continuation); the list stands as the campaign's own record:

- The 20-minute thinking soak. No thinking-on request was sent, so v1.1's fix for the failed CUDA graph capture under many concurrent thinking requests is not tested here.
- The OpenCode multi-turn accounting session. Reasoning content, call IDs and cache-read accounting across real agent turns are unqualified; the plain reply and file-read turn under the first boot stand as separate, earlier evidence.
- `agent_acceptance.py`, every section: tools, isolation, cache, coding and cancellation. Its structured-response case does not apply to this server, as above; the HTTP 400 answers for `json_object`, `json_schema`, `logprobs` and `n` are known from source and were not rechecked.
- `long_sessions.py`, deliberately: its guard can stop the service, which this campaign was not allowed to do.
- INT4-AR, concurrent long prompts, and any prompt between 524,135 and 999,874 tokens.

Conduct. Both hosts' memory, management-link carrier counters and `/health` were sampled every five seconds, with a sticky abort after three samples under 2 GiB or three missing. Available memory was 42.4 and 45.1 GiB at the start. The harness paused once, after the first one-stream repetition, when `/health` still showed one live connection just after the stream's `[DONE]`; it resumed after three zero readings 0.5 s apart, with no failed request. Before each timing run the harness checked `live.connections == 0`; after the cleanup pause it required three zero readings 0.5 s apart. No foreign traffic was observed. Long requests also used a client-only watchdog that checks telemetry freshness during silent prefill and cannot stop the service. A `/health` answer says only that the server is listening: its `status: ok` is unconditional, as noted above, so it is no evidence of a healthy decoder. It kept answering `ok` while the request was stalled, and stopped answering only when the engines exited and the port began refusing connections.

Raw receipts are outside git on the head under `~/sparkrun/results/qwen38fn-v1.1/evaluation-20261008/`, with the tools and harness scripts in its parent directory:

| Evidence | Files |
| --- | --- |
| Correctness gates | `exact.log`, `toolcheck.py.log`, `prompt_reuse.py.log`, `context_boundary.py.log`, `acceptance.py.log`, `video.json` |
| Throughput and prefill | `decode-summary.jsonl`, `prefill-summary.jsonl`, `throughput-gpu-kernel.txt` |
| Completed retrieval | `retrieval-131072.json`, `retrieval-262144.json`, `retrieval-524288.json`, `headroom-524288.json` |
| The 999,874-token request | `headroom-1000000.json`, `retrieval-1000000.expected.json`, `.request.json` and `.sse.jsonl`, `requests.jsonl`, `SERVICE_ERROR` |
| Stall and terminal state | `control-1m-early.txt`, `control-1m-stalled.txt`, `stalled-1m-gpu-kernel.txt`, `post-failure-rank0.txt`, `post-failure-rank1.txt` and their `-processes.txt` pairs |
| Whole campaign | `telemetry.jsonl`, `telemetry-summary.json`, `receipt-manifest.json` |

The rank serve log is `/tmp/sparkrun_serve.log` inside each rank container, not in the workspace; the post-failure captures hold its closing lines.

Operator-requested recovery, October 8 at 23:11 EDT. After preserving the failure receipts and checking both hosts had about 117 GiB available with no GPU compute processes, SparkRun removed the two shell-only containers and relaunched the same pinned NVFP4 recipe, image, weights and mod. No recipe, mod or host configuration changed. The vision helper was ready in 11.5 s, both ranks joined NCCL and opened the two-port RoCE all-gather, sequence memory was 31.00 GiB, and HTTP was ready 93.62 s after container start. The readiness hook passed all 16 concurrent short replies; the complete launch took 107.1 s. A workstation request to `http://10.32.21.31:8888/v1`, temperature 0, thinking off, 32-token reply budget, returned `323` for `17 × 19` (28 prompt tokens, 4 completion tokens). Afterwards `/health` was idle, available memory was 42.8/46.1 GiB on head/worker, and management-link carrier counters remained 12/1,432. Recovery receipts, both ranks' logs and five-second telemetry are under `~/sparkrun/results/qwen38fn-v1.1/recovery-20261008-2311/` on the head. The monitor ended after the checks. No evaluation step resumed during the recovery and no long prompt was retried; the [continuation below](#october-9-thinking-and-agent-continuation) was authorized later. The transport defect remains unresolved.

### October 9 thinking and agent continuation

After the recovery the operator authorized more benchmarking on the recovered instance, avoiding one-shot prompts near the window. The campaign's rule against service changes stayed in force: nothing was stopped, restarted, relaunched or reconfigured, and the recipe, image, weights and mod are the pinned ones the recovery relaunched. The engine processes, PID 1879890 on the head and 2803753 on the worker, started at 23:11:21 and 23:11:39 EDT on October 8, and both ranks showed the same PID and start time after the soak. Three things ran against that instance: a 20-minute thinking soak at 16 streams, an OpenCode session checked for token accounting on the wire, and the agent gate's five applicable sections. No long prompt was sent.

All three completed without a failed request. That adds thinking-on decode under sustained concurrency, reasoning and call-ID round-trips through a real client, and the short agent cases to what the [October 8 campaign](#october-8-nvfp4-evaluation) measured. It changes nothing about the window: the control-link defect is unfixed, the ~1M case has not been retried, and the profile is still not qualified at 1,048,576 tokens or for unattended use.

Thinking soak. From 00:00:08.706 to 00:20:48.804 EDT on October 9, 16 client threads submitted requests for 20 minutes and then drained for 40 s. The requests were distinct database-ledger design prompts, repeated over the run, of 135–137 prompt tokens by the API's count, sent with temperature 0, thinking explicitly on, seed 20261008, a 1,024-token reply budget and the default drafts on. All 365 completed, for 373,760 completion tokens: every one ended with `finish_reason: length`, with non-empty reasoning and no answer content. The soak therefore exercises concurrent thinking decode for its length and says nothing about answers or their quality, since every reply spent its whole budget reasoning. Neither rank's log has a graph-capture failure or a pause warning, and the API was idle at the end. `TF_FLASHNEXT_GRAPH_LOG`, the detailed graph log, was off, so this does not prove that the capture fallback v1.1 added was exercised or that v1.0's trigger was reproduced; it shows one 20-minute run of that shape ending with both engines up. It is one soak, not a repeated result.

Soak conduct. The client guard was set to stop further submissions after three samples below 2 GiB on either host, three missing samples or stale telemetry; it could not stop the service. All 229 five-second memory, link and health samples completed without an abort condition. The lowest available memory was 41.04 GiB on the head and 43.84 GiB on the worker, and the management-link carrier counters stayed at 12 and 1,432. `/health` reported 16 live connections in 221 samples and never more; the other eight, during transitions and the drain, read 15, 14, 13, 11, 10, 7, 4 and 1. All 365 new completion IDs in the rank log matched the soak receipts, with no unowned request; there were 17 completed requests before the soak. No foreign request was observed. A first launch of the soak harness failed on a receipt path before it sent any request; it is kept separately and is not part of the run.

OpenCode session. OpenCode 2.0.24 on the workstation ran `spark/Qwen3.8-Flash-Next` in a private instance with a read-only custom agent, through the same `@ai-sdk/openai-compatible` provider as the normal configuration, pointed at a localhost pass-through proxy that kept every request and response. No global client configuration changed. The test model entry declared a 32,768 context and 2,048 output limit, only `read`, `glob` and `grep` were advertised, and MCP was off. Three user turns over a small fixture workspace made four successful `read` calls, of the manifest, the dimensions file, the adjustment file and the dimensions file again, and the three answers matched exactly: `TOTAL=323`, `AVAILABLE=316;EXPANDED=361` and `FINAL=354`. The first user prompt carried 8,192 repeated padding words to exercise the accounting; OpenCode compacted that padding on its own before the tool loop began, so the agent calls never carried it.

That made eight API calls, one compaction and seven agent calls:

| Call | Prompt tokens | Cached | Completion tokens | Reasoning tokens |
| --- | --- | --- | --- | --- |
| Compaction | 10,082 | 0 | 431 | 123 |
| Agent 1 | 1,591 | 1,234 | 100 | 59 |
| Agent 2 | 1,764 | 1,234 | 61 | 19 |
| Agent 3 | 1,900 | 1,234 | 34 | 27 |
| Agent 4 | 2,016 | 1,234 | 108 | 66 |
| Agent 5 | 2,196 | 1,234 | 61 | 46 |
| Agent 6 | 2,317 | 1,234 | 123 | 81 |
| Agent 7 | 2,515 | 1,234 | 77 | 70 |

The stored OpenCode session totals, which include the compaction call, were 15,743 input, 504 output, 491 reasoning, 8,638 cache read and 0 cache write. They equal the wire sums with OpenCode's ordinary input as prompt minus cached tokens and its ordinary output as completion minus reasoning tokens. Each call's usage was checked against its final SSE event, the request ID in rank 0's log and the OpenCode export. Call IDs and arguments were byte-exact and unique, every tool result referenced its call's ID, each earlier `reasoning_content` was returned byte-exact in the later requests and matched the reasoning text OpenCode saved, and the message prefix was unchanged from one agent call to the next. Every agent call reported the same 1,234 cached tokens while its prompt grew, so the cache figure here is a constant reused prefix and not reuse of the growing history.

Conditions of that session, as sent. The client's requests omitted `temperature` and `chat_template_kwargs`, although the provider configuration carries legacy options for both, and carried no seed; `max_completion_tokens: 2048` and `include_usage: true` were on the wire. So this session did not ask for thinking or for temperature 0. Thinking was on by the server's default, confirmed by `tensorfold.enable_thinking` in each final event and by the non-empty reasoning, and sampling ran at the server's default temperature. A first attempt failed on the local agent's read permissions and fell back to `grep`; it is kept under `opencode/permission-mismatch/` and is excluded from the figures above.

Agent gate. Run on the head after the OpenCode session, the repository's unchanged [`agent_acceptance.py`](tests/agent_acceptance.py) passed with `--model Qwen3.8-Flash-Next --require-cache` over the tools, isolation, cache, coding and cancellation sections, with no tokenizer argument: all 18 cases, five tool cases, four isolation cases including the two high-thinking ones and the reversed history, two cache cases, six coding cases (three functions at temperatures 0 and 0.6, seed 20260930) and one cancellation. The structured section was excluded, because this server refuses those fields. That refusal was checked live: `response_format` of `json_object`, of `json_schema`, and `logprobs: true` each answered HTTP 400. `n` other than 1 and the `guided_*` fields were not rechecked.

Still not established:

- The window. No prompt between 524,135 and 999,874 tokens and no new long prompt of any size was sent, the ~1M request was not retried, and the control-link defect is unfixed.
- Concurrent long contexts, growing long sessions and `long_sessions.py`, which was again left out because its guard can stop the service.
- General coding quality. The six coding cases are short functions, and the soak's replies were counted, not read.
- Unattended reliability. One 20-minute soak and one three-turn session on one boot are not that.
- Thinking under a client's explicit request through OpenCode, and INT4-AR.

Raw receipts are outside git on the head under `~/sparkrun/results/qwen38fn-v1.1/continuation-20261008-2324/`. The `2324` in the name is only a label; the soak began at 00:00 EDT on October 9.

| Evidence | Files |
| --- | --- |
| Soak requests | `soak-start.json`, `soak-summary.json`, `soak-analysis.json`, `requests.jsonl`, `soak-*.request.json` and `.sse.jsonl` |
| Soak conduct | `telemetry.jsonl`, `pre-soak-rank0.txt`, `pre-soak-rank1.txt`, `post-soak-rank0.txt`, `post-soak-rank1.txt`, `engine-processes-before.txt`, `engine-processes-after-soak.txt` |
| Final state | `final-rank0.txt`, `final-rank1.txt`, `final-health.json` |
| Agent gate | `agent-acceptance.json`, `agent-{section}.log`, `agent-exits.txt`, `protocol-limits.json` and its log |
| OpenCode session | `opencode/wire/NNN.request.json`, `NNN.response` and `NNN.meta.json`, `opencode/turn1`–`turn3` `.export.json` and `.cli.jsonl`, the status JSON, `analysis.json`, `run_agent.py`, `analyze.py`, `expected.json`, `opencode.json` and the workspace fixtures |

The OpenCode receipts were produced on the workstation and uploaded to that `opencode/` directory on the head; server password logs were excluded. The final API was healthy and idle, both original engine processes remained up, and the finite monitor and clients exited. `receipt-manifest.json` indexes the receipts; `soak-log-analysis.json` matches the soak IDs to the rank log.

## DeepSeek-V4.1-Flash-1017 on bertholomus's Zig engine

`bertholomus-tensorfold-dsv41-tp2/dsv41-1017-exl3-tp2-bth-tf-6366d2c-libertai-1m.yaml` serves [`deepseek-ai/DeepSeek-V4.1-Flash-1017`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017) as `DeepSeek-V4.1-Flash-1017`, through [LibertAI's EXL3 2.9 bpw quant](https://huggingface.co/LibertAIDAI/DeepSeek-V4.1-Flash-1017-EXL3-2.9bpw/tree/0be63c3337efbb2a0061d5b8bb4006fa186d433f) on the native Zig engine of [`bertholomus/TensorFold`](https://github.com/bertholomus/TensorFold/tree/deepseek-v41-zig), branch `deepseek-v41-zig`. Upstream TensorFold refuses DeepSeek checkpoints until its whole-model gate lands ([ashhart/TensorFold#827](https://github.com/ashhart/TensorFold/pull/827)). It has served since October 21; from October 17 the same weights ran on engine `3ff3b2a` from hand-started containers that the inference scrape could not see.

Rank 0 runs `tensorfold-native serve` with the OpenAI and Anthropic routes; rank 1 runs `tf-dsv41-lanes`. Settings: four streams, a 1,048,576-token window, a 32,768-token reply budget, thinking on, temperature 1.0, top-p 0.95, top-k 20, DSpark drafts, CUDA graphs and the Engram read-ahead. The ranks link over rail 0 of the CX7 cable (`DSV41_TP_MASTER`), not the management network SparkRun's rendezvous address uses, and gather over both rails with RDMA on the devices and GID index SparkRun detects.

The [mod](mods/bertholomus-tensorfold-dsv41-6366d2c/) builds the image and launches both ranks. `upstream.lock.json` pins the engine, the inputs, the kit, the Engram tables and every mod file.

| Patch | Source | Change |
| --- | --- | --- |
| `0001-models-discovery-483-819.patch` | `ashhart/TensorFold` #483 and #819, reduced to `zig/src/server` | `/v1/models` reports `context_length` and the reasoning efforts |
| `0002-call-gate.patch` | [Mia's 1017 recipe](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks), `zig/patches/0004-call-gate.patch` | `tool_choice` `"required"` and named functions force a call |
| `0003-rdma-idle-nap.patch` | Mia's recipe [PR #26](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/pull/26) | The RDMA gather's proxy sleeps 100 µs between looks after 2 s idle instead of spinning a core on each rank |

At launch [`serve.py`](mods/bertholomus-tensorfold-dsv41-6366d2c/serve.py) links three views under `/tmp/dsv41` from the read-only snapshots: the weights with the mod's `chat_template.jinja` (a port of DeepSeek's `encoding.py`), the [kernel kit](https://huggingface.co/bertholomus/DeepSeek-V4.1-Flash-TensorFold-TP2-2xGB10/tree/4996441a82d95b9a7853b01a1255e1dafc926387) with the 1017 image routing bias made from the weights and checked against the lock, and the Engram shards with their embed-only index. It sets the engine's `oom_score_adj` to 1000 so the kernel ends the engine before anything else when host memory runs out. The [readiness hook](mods/bertholomus-tensorfold-dsv41-6366d2c/readiness.py) requires healthy status, four lanes and the window in `/v1/models`, then sends four concurrent replies and one tool call.

### Inputs outside SparkRun's distribution

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
cp ~/sparkrun/recipes/glm53/mods/bertholomus-tensorfold-dsv41-6366d2c/engram.index.json "$engram/model.safetensors.index.json"
docker run -d --name dsv41-inputs --gpus all --network host --ipc host --ulimit memlock=-1 --ulimit stack=67108864 \
  --cap-add IPC_LOCK --device /dev/infiniband -v "$hub:$hub:ro" -v "$engram:/engram:ro" -v "$inputs:/inputs" \
  -e TF_DS_REPLAY=1 -e TF_DS_PREFILL_CHUNK=2048 -e TF_DS_RANK_CACHE=/inputs/tf-ds-rank -e TF_DS_RANK_CACHE_READERS=32 \
  -e TF_DS_ENGRAM=/engram -e TF_DS_TOKEN_MAP=/inputs/dsv41_token_map.json -e TF_RDMA_GID_INDEX=3 \
  -e NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0 -e NCCL_IB_GID_INDEX=3 -e NCCL_SOCKET_IFNAME=enp1s0f0np0 \
  dsv41-tf:6366d2c-4691715-b52bd191b6e4 /usr/local/bin/tensorfold serve "$weights" --tp 2 --rank "$RANK" \
  --master 198.19.240.11 --host 127.0.0.1 --port 18891 --context 1048576 --vision --parallel 4 --mtp-drafts 5
```

These are the settings that wrote the current inputs on October 17, with this layout's paths.

On October 21 both were hard-linked from the hand-run copies, so neither was downloaded or rebuilt; the rank inputs moved to their own root at the relaunch that afternoon, and the hand-run copies were then deleted.

### Build

The image is built on any linux/arm64 Docker daemon, never on a serving Spark, where the Zig and `nvcc` build would compete with the engine for host memory. [`build/build.sh`](mods/bertholomus-tensorfold-dsv41-6366d2c/build/build.sh) fetches the engine and inputs at the lock's commits, applies the three patches and builds on NVIDIA PyTorch 26.07 by digest; the October 21 build took about three minutes on the workstation's Colima `tf-build` profile. Load it on the head; SparkRun copies it to the worker at launch:

```sh
DOCKER_CONTEXT=colima-tf-build sparks/inference/sparkrun/mods/bertholomus-tensorfold-dsv41-6366d2c/build/build.sh
DOCKER_CONTEXT=colima-tf-build docker save dsv41-tf:6366d2c-4691715-b52bd191b6e4 | ssh kelchm@10.32.21.31 docker load
```

The recipe's `container` and the lock's `image_id` are the image ID the head reports, `sha256:66ce7619…`. A rebuild can produce another ID; record it in both before launching.

### Launch, stop and roll back

From `~/sparkrun/recipes/glm53` on the head, after syncing this directory as in [Deploy](#deploy). Stop the running instance with its own recipe first:

```sh
dsv41="$PWD/bertholomus-tensorfold-dsv41-tp2/dsv41-1017-exl3-tp2-bth-tf-6366d2c-libertai-1m.yaml"
python3 mods/bertholomus-tensorfold-dsv41-6366d2c/prepare.py --check
sparkrun run "$dsv41" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
sparkrun logs "$dsv41" --cluster sparks
# When stopping or replacing the workload:
sparkrun stop "$dsv41" --cluster sparks
```

The rollback is the Qwen [discovery profile](#model-discovery-variant). No earlier DeepSeek engine is kept: its image and the hand-run copies were removed from both hosts on October 21, and this mod has never pinned another engine.

### October 21 launch

The hand-started containers were stopped idle at 17:05:11 UTC with nothing in flight, and SparkRun's launch returned 73 s later with the readiness hook passed; the API was down for about 85 s. Rank 0 read its 95.1 GiB of expert entries in 8.6 s and rank 1 loaded in 24.0 s; rank 0 logged RDMA to rank 1 on both rails at GID index 3, and the ranks' link ran between `198.19.240.11:25000` and `198.19.240.12`. `up{job="spark-inference"}` reported the model with SparkRun's labels within a minute.

- The idle engine processes use 1.6% and 1.8% of a core, against about 100% each before.
- `tool_choice: "required"` returned a call to the offered function for a prompt that asks for none; engine `3ff3b2a` refused it.
- Prompts of exactly 512 and 513 tokens complete, the case [recipe issue #13](https://github.com/bertholomus/deepseek-v4.1-tensorfold-tp2-2xgb10/issues/13) reports failing with `MissingTritonVariant` on the kit's previous revision.
- The chat template matches DeepSeek's four text reference renderings in the image's Python template engine ([`tests/dsv41_chat_template.py`](tests/dsv41_chat_template.py)).
- Mia's `tools/bench.py` with the arguments used on October 19 (`--suite prefill,decode,multi --prefill-sizes 2000,8000,30000 --streams 1,4 --reps 2`), medians, against engine `3ff3b2a`: decode 59.5 tok/s against 59.4, four streams 113.2 tok/s in total against 113.5, and prefill 1,946, 2,325 and 2,409 tok/s at 2K, 8K and 30K tokens against 1,902, 2,131 and 2,087.
- `MemAvailable` after the benchmark: 6.0 GiB on the head and 8.1 GiB on the worker.

### Known limits

- Control-token spellings in request text, tool results included, become control tokens: the engine renders the template to text and encodes it with the added vocabulary ([MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks#21](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/issues/21)). No fix exists.
- The prompt cache evicts kept conversations oldest first, so an agent's idle main conversation goes before finished subagents' ([#27](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/issues/27) there).
- The engine's piecewise prompt passes (`TF_FILL_CHUNKS`) stay off: Mia's comparable interleave starved several queued long prompts ([#18](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/issues/18) there), and a long prompt pass holds other streams' tokens until it finishes.
- The window is configured at 1,048,576 tokens and qualified here only to about 154K. The head keeps about 6 GiB available with the model loaded; Mia reports 512K as the practical limit for a head like this one. No memory watchdog runs.
- The endpoint has no API key.

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

Each mod pins its image, weights and inputs in `upstream.lock.json`, and its `run.sh` starts with `prepare.py --check`, which refuses a changed, missing or linked file. The contract also sets a common layout, which profiles adopt as they are refreshed: the rank launcher at `/usr/local/bin/tensorfold-entrypoint`, static files under `/opt/tensorfold-mod/`, the engine's `oom_score_adj` at 1000, and no lab addresses, host names or paths in recipes or mods. GLM `3fd60dfc` follows it. GLM v1.10 and the Mia Qwen mods install their own launcher and readiness paths and leave the engine's OOM score unchanged. DeepSeek `6366d2c` sets the score but keeps its own paths, and its recipe names its dedicated runtime-cache root under `/opt/spark-cache`, which [#932](https://github.com/kelchm/home-lab/issues/932) settles.

Profiles share one readiness gate, the [`tensorfold-readiness` mod](mods/tensorfold-readiness/), listed beside the engine's own mod and called from `post_exec` as `python3 /opt/tensorfold-readiness/readiness.py --model NAME --context WINDOW --lanes STREAMS --efforts LEVELS`. It requires health in the server's own format: the Zig servers' `status`, `warming` and `max_batch_size`, or Mia's Python server's strict `ok`, streams and window, plus a pool floor when the profile passes `--min-pool-tokens`. It also requires a `/v1/models` entry with `owned_by: tensorfold`, `context_length` and `max_model_len` equal to the window, and exactly the profile's reasoning levels, in the server's order. It then needs one correct concurrent reply per stream and one tool call. Where the server has `/tokenize`, it also checks that each advertised level renders its own prompt and that every other standard level name renders as the nearest of them. `--min-pool-tokens` and `--warmup-words` are optional, for the Python server's pool floor and shape warmup. SparkRun 0.3.10's own readiness probe checks HTTP status only, and it discards a passing hook's output; to keep the gate's output, run the same command once more against the live server from the head's rank container. Profiles move onto the gate as their recipes are refreshed; until then each mod keeps its own hook.

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

A rank that dies mid-collective can leave a kernel running on its GPU with no process attached: `nvidia-smi` reads about 96% utilization, an SM clock near 1.1 GHz and about 4.7 W with no compute process, where an idle GB10 reads 0% at 208 MHz in P8. To clear it, stop the host's GPU exporter container and `nvidia-persistenced`, so nothing holds the GPU and the driver tears down its state, then start both again and confirm 0% at 208 MHz before the next launch. `nvidia-smi -r` refuses on these hosts because the GPU is the primary one. Reboot only if stopping the daemon doesn't clear it. This cleared spark-2 on 2026-10-22.

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
- **Long sessions:** the [long-session client](benchmarks/long_sessions.py) runs on the head with `uv run --with llama-benchy==0.4.0 python`, `--model "$model"` (its default is an old GLM name), the pinned target's `tokenizer.json`, an absolute recipe path and output outside the checkout. `--suite full` covers C1, C2 and C4 controls, unrelated and shared prefixes, warm replay, edits and forks, 250k prompts with small and 32k output budgets, the 262k admission boundary and a short newcomer during long prefill; `growth`, `repeat`, `250k`, `boundary` and `ceiling` run parts of it, and each paired boot needs a new `--campaign` value. It records per-stream time to first token, chunk gaps, usage and cache details, and both hosts' memory minima, and stops the recipe through SparkRun after three low-memory samples or lost telemetry. `--require-stable-links` aborts on a lost 10GbE carrier or any new drop. Payloads are synthetic repository text, so this is not a general long-context coding benchmark. Its cancellation can miss a silent stream ([#651](https://github.com/kelchm/home-lab/issues/651)).

## Update a pinned mod

Each mod is frozen: `upstream.lock.json` pins the image, the upstream revisions and every mod file, and `prepare.py --check` refuses any other. For an upstream update, review the kit's or engine's launcher, runtime files and tests together, and add a new versioned recipe and mod rather than editing the current ones, so the current profile stays as the rollback. Update the lock's revisions and hashes from the reviewed source, including every mod file's hash, then dry-run and validate the new profile on the Sparks. Do not update checksums to silence a failure.

SparkRun fingerprints the declared mod references, not their contents, so relaunch after a mod change and use `--fresh` for a new benchmark comparison.
