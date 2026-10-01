# GLM-5.3-Flash recipes through SparkRun

These recipes serve `GLM-5.3-Flash-EXL3` across two DGX Sparks at `http://10.32.21.31:8888/v1`. SparkRun 0.3.10 owns model/image distribution, fabric detection, containers, runtime caches, readiness, warmup, logs, stop, and native benchmarking for both engines. TensorFold uses the adjacent external runtime adapter. These hosts are outside Flux. Check actual host state before launching; other experiments can use either GPU.

The September 30 candidates are under evaluation in [#645](https://github.com/kelchm/home-lab/issues/645). The refreshed Mia recipe and TensorFold W20 recipe are prepared candidates, not an adoption decision. The exact September 19 Mia recipe, image and prepared mod remain the fallback. Qualification results and the final choice will be added here as the campaign completes; raw receipts stay outside git.

| Recipe | Source/build | Target / draft | Capacity choice |
| --- | --- | --- | --- |
| [Refreshed Mia](mia-glm53-exl3.yaml) | Mia `674155de`, fresh ExLlamaV3 CUDA build, site DFlash fixes | Stock TR3 `25a44fdb` / `dc77ff1c` | 850k request ceiling, C4, 11 GiB KV per rank |
| [TensorFold W20](tensorfold-glm53-exl3.yaml) | Jay `b463237b`, engine `2f8e514b`, selected 75 patches, site stop/stats/history guard | Same stock TR3 / published W20 draft `7d74cdd8` | 850k request ceiling, four slots, shared 1,048,576-token latent KV pool |

This compares complete recipes: the draft weights, arithmetic, scheduler and cache implementation differ. Normal evaluation is C4 at 128k–250k, with C1/C2 controls; four 262k prompts plus reserved output exceed TensorFold's shared pool and must queue. C8 is a secondary short/warm workload. Neither recipe promises four simultaneous 850k requests.

Grok's native-X follow-up checked Mia, Netrunner and Jay from September 30 14:27 to October 1 02:27 UTC and found no published Mia GLM TensorFold kit; W21 replies were sparse. Upstream [TensorFold 0.6.0](https://github.com/ashhart/TensorFold/releases/tag/v0.6.0) adds GLM CUDA conversation resume and lighter DFlash buffers. Our W20 kit pins engine 0.3.4 plus its selected patches. A 0.6-based recipe is a follow-up candidate once its stock-TR3 TP2/C4 setup and SparkRun lifecycle are available for qualification; the release's short-context and other-model numbers do not establish this campaign's long-stream result.

## Deploy

The saved `sparks` cluster uses head `10.32.21.31`, worker `10.32.21.32`, SSH user `kelchm`, cache `/opt/spark-cache/huggingface`, greedy placement, and management-network transfers. Use `transfer_mode: auto`. SparkRun detects both CX7 rails for inference; do not copy another operator's interface names into the TensorFold recipe. Its TCPStore rendezvous may use the management address while GPU traffic uses the detected fabric.

Copy this directory to the head, keeping the mod beside the recipe, then prepare the pinned source bundle:

```sh
rsync -a --exclude __pycache__ sparks/inference/sparkrun/ kelchm@10.32.21.31:sparkrun/recipes/glm53/
ssh kelchm@10.32.21.31
export PATH="$HOME/.local/share/sparkrun-glm53-0310/bin:$PATH"
cd ~/sparkrun/recipes/glm53
python3 mods/mia-glm53/prepare.py
sparkrun run ./mia-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
# Stop the current workload with its own recipe before launching on these GPUs.
sparkrun run ./mia-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
```

Build the pinned candidate image on the head before the dry run; instructions follow below. SparkRun downloads the pinned target and draft snapshots into its standard HF cache and synchronizes them to the worker. Existing snapshots are reused; initial provisioning can transfer about 164 GiB. The worker needs no upstream checkout or manually staged models. SparkRun clears page cache after distribution; if its sudo operation is unavailable, use `sparkrun setup clear-cache --cluster sparks --save-sudo`.

For a fresh control host, first provision Docker/SSH access and the cache directory on both hosts, owned by the SSH user. Install the control tools and create the cluster:

```sh
python3 -m venv ~/.local/share/sparkrun-glm53-0310
~/.local/share/sparkrun-glm53-0310/bin/pip install sparkrun==0.3.10 uv==0.12.6
export PATH="$HOME/.local/share/sparkrun-glm53-0310/bin:$PATH"
sparkrun cluster create sparks --hosts 10.32.21.31,10.32.21.32 --user kelchm --cache-dir /opt/spark-cache/huggingface --transfer-mode auto --transfer-interface mgmt --scheduler greedy
```

Keep this venv on PATH for subsequent commands. The existing head retains its older `~/.local/bin` SparkRun 0.3.9; the isolated venv avoids replacing that installation. Benchmarks need `uvx` on PATH.

### Build the candidates

The recipes pin local Docker image IDs from the evaluation head. SparkRun distributes those images to the worker. The IDs identify our built artifacts, rather than public registry manifests; rebuilding from the same inputs can produce another ID. Record the build receipt, verify the resulting image, and update the recipe pin before using a rebuild. Do not silently substitute a mutable upstream tag.

For Mia, check out the exact source outside the recipe checkout and build its ARM/CUDA image:

```sh
git clone https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks.git ~/sparkrun/builds/mia-674155de
git -C ~/sparkrun/builds/mia-674155de checkout --detach 674155dec2f2f62bb879801b5ce2cfc759a0bebf
docker build --progress plain --build-arg GLM53_RECIPE_STAMP=674155dec2f2f62bb879801b5ce2cfc759a0bebf \
  -t glm53-mia:674155de-20260930 ~/sparkrun/builds/mia-674155de
docker image inspect --format '{{.Id}}' glm53-mia:674155de-20260930
```

Our build resolved the upstream base to `vllm/vllm-openai:glm53-flash-arm64-cu130@sha256:905c02933be6021301db2dc284e24e3727467aa3a0f63b41d609885778a07bce`. The compiled image and checksum-pinned mod are separate inputs. The mod applies the selected upstream patches first, then the site backports of vLLM [54282](https://github.com/vllm-project/vllm/pull/54282) and [58454](https://github.com/vllm-project/vllm/pull/58454). They separate draft/residual Gumbel noise and retain committed K-pool tail entries across rejected drafts, preserving the older NVIDIA packed layout and the upstream padded-stride fix. Dense-H3, FAST and cooperative MoE are excluded from this first trial.

For TensorFold, prepare checksum-locked kit/engine archives, then build the selected W20 patches and the small site layer:

```sh
python3 mods/tensorfold/prepare.py
python3 mods/tensorfold/prepare.py --check
docker build --progress plain -f mods/tensorfold/upstream/kit/docker/Dockerfile \
  --build-arg PATCHES="$(cat mods/tensorfold/upstream/kit/results/W20/build-patches.txt)" \
  -t glm53-tensorfold:b463237b-20260930 mods/tensorfold/upstream/kit
docker build --progress plain --build-arg BASE=glm53-tensorfold:b463237b-20260930 \
  -t glm53-tensorfold:b463237b-site-history1-20261001 mods/tensorfold
docker image inspect --format '{{.Id}}' glm53-tensorfold:b463237b-site-history1-20261001
docker run --rm --network none --entrypoint python3 \
  -v "$PWD/tests/tensorfold_tool_history.py:/tmp/test_tool_history.py:ro" \
  glm53-tensorfold:b463237b-site-history1-20261001 /tmp/test_tool_history.py
```

`prepare.py --check` verifies the archives and every materialized build input, including the pinned NVIDIA base. The site layer ports LimeChain's stop-token and response-summary behavior onto W20 and retains its tool fixes with one narrower reasoning-cache lookup. W20's fallback signature uses only the last user text and tool arguments: a check against the built image reproduced conversation A receiving B's reasoning after the client renumbered the tool IDs. The site guard restores reasoning only by the original server call IDs; clients that renumber or omit IDs must retain their own reasoning. The [installed-code regression check](tests/tensorfold_tool_history.py) covers parallel histories, missing/renamed IDs, caller-supplied reasoning and eviction. It fails on the original site image and passes on the new pinned `17d8d02d…` image without loading model weights. `GLM53_TF_STATS=summary` omits unbounded per-round lists, `full` retains diagnostics, and `off` omits the extension object. Logprobs from LimeChain's older W17 overlay are not included.

### Enable TensorFold in SparkRun

Merge these keys into the head's `~/.config/sparkrun/config.yaml`, preserving existing configuration. Use the actual absolute path where this directory was staged:

```yaml
features:
  core.external_plugins: true
plugins:
  paths:
    - /home/kelchm/sparkrun/recipes/glm53/plugins
```

The [adapter](plugins/tensorfold/__init__.py) requires exactly two TP2 ranks with PP1 and uses SparkRun's native launcher, rendezvous gate, cache mounts, log sources and stop operation. It maps immutable HF snapshots into `/cache/huggingface/hub`, and prepared weights/session storage into SparkRun's persistent `/cache/runtime` namespace on each node. Prepared weights can consume another checkpoint-sized allocation on disk; retain sufficient room for the 64 GiB session tier per node. No upstream two-host launcher or watchdog is installed.

SparkRun 0.3.10's generic readiness probe checks HTTP status only. TensorFold uses strict engine health so fatal failures/stalls produce HTTP 503; its `post_exec` gate also requires JSON `ok: true`, strict mode, exactly four loaded slots and the configured context ceiling, then checks live C4 shape warmup. A requested slot count alone is insufficient: TensorFold can load fewer slots when memory is tight.

Select the TensorFold recipe for its dry run, launch and every subsequent operation:

```sh
sparkrun run ./tensorfold-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
# Stop the previous stack with its own recipe first.
sparkrun run ./tensorfold-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
sparkrun logs ./tensorfold-glm53-exl3.yaml --cluster sparks
sparkrun stop ./tensorfold-glm53-exl3.yaml --cluster sparks
```

## Operate and roll back

From `~/sparkrun/recipes/glm53` on the head:

```sh
sparkrun status --cluster sparks
sparkrun logs ./mia-glm53-exl3.yaml --cluster sparks
# When stopping or replacing the workload:
sparkrun stop ./mia-glm53-exl3.yaml --cluster sparks
```

`run --no-follow` waits for readiness and upstream shape warmup because the recipe has `post_exec`. A failed hook makes the command fail but does not automatically stop the server; inspect logs and stop the failed candidate before rollback. Containers require an explicit SparkRun launch after a host reboot. Persistent runtime caches are managed by SparkRun.

Before an upgrade, retain the exact working recipe and prepared mod bundle outside the checkout. The September 30 campaign retained them at `~/sparkrun/experiments/glm53-20260930/fallback/`; receipts are beside it in `receipts/`. Stop the candidate with its own recipe, then launch the retained recipe with the same cluster and launch flags. Recipe fingerprints can differ, so use the corresponding recipe for stop and logs. Never overlap inference stacks on these GPUs. Inspect running containers, GPU processes and available host memory on both hosts before launch, and stop conflicting workloads with their own lifecycle commands. The legacy Qwen Compose recipe and its Taskfile interlock are retired; they provide no admission guard for SparkRun or other experiments.

For the initial migration, the previous recipe remains at `~/sparkrun/receipts/integration-20260919/baseline.yaml`, with its original Mia checkout and `/opt/sparkrun/models/` paths on both hosts. Those model files share hardlinks with the native HF cache: do not edit weights in place. Native synchronization normalized ownership; the root-run baseline remains readable. Fresh provisioning uses native downloads and does not require the migration script.

The endpoint is unauthenticated on the trusted LAN. The existing nonpersistent firewall exception permits workstation `10.32.10.244/32` to head port 8888 via `enP7s7`. Check access after a host reboot or workstation address change.

## Validate

Run these against the existing instance, with no other workload submitting requests. Use a working directory outside the recipe checkout for native reports and the evaluator's local database:

```sh
mkdir -p ~/sparkrun/results
cd ~/sparkrun/results
recipe=~/sparkrun/recipes/glm53/mia-glm53-exl3.yaml
profiles=~/sparkrun/recipes/glm53/benchmarks
sparkrun benchmark "$recipe" --cluster sparks --skip-run --no-stop --fresh --profile "$profiles/throughput.yaml" --output ./throughput.yaml
uvx --from git+https://github.com/SeraphimSerapis/tool-eval-bench@v1.8.0 tool-eval-bench \
  --base-url http://127.0.0.1:8888/v1 --model GLM-5.3-Flash-EXL3 \
  --short --no-think --parallel 1 --timeout 120 --max-turns 8 --temperature 0 \
  --json-file ./tools.json
python3 ~/sparkrun/recipes/glm53/tests/acceptance.py
```

The [throughput profile](benchmarks/throughput.yaml) uses native llama-benchy for 2k/32k prompts at C1/C4, with warmup, two measured repetitions, temperature zero and a fixed seed. Its 256 tokens are an output budget; thinking follows each recipe's template default. Treat it as a supporting diagnostic. The long-session client explicitly disables thinking and records actual completion lengths for the primary comparison. Run native benchmarks on the head: the tokenizer path points to the pinned HF snapshot under the cluster cache. Adjust that path if the cache moves; an unreadable tokenizer can silently fall back to GPT-2 and invalidate comparisons.

SparkRun 0.3.10 shell-quotes its llama-benchy argument list before direct subprocess execution. The profile uses shell-safe `key=value` entries for `extra_body`; JSON-valued entries such as `chat_template_kwargs` acquire literal quotes and do not reach the intended API field. Inspect the constructed argv and parsed request fields before adding complex settings. The initial control sweep omitted explicit temperature and is excluded from matched comparisons.

Tool evaluation uses the upstream `tool-eval-bench` v1.8.0 CLI directly for 15 core scenarios. SparkRun 0.3.9's tool integration passes the model repository ID instead of the served alias, ignores a model argument override, and shell-quotes JSON arguments before direct subprocess execution. The direct command avoids those integration bugs without a wrapper or installed patch. Inspect `scores.scenario_results` and the native conversation report, not just the exit status: completion does not mean the scenarios passed. No results are submitted to Spark Arena.

TensorFold's [W21 tool report](https://github.com/jayleaton/glm53-tensorfold-spark/pull/14), published during this campaign, adds measurements on the existing production build. Its abliterated checkpoint and one run per configuration do not establish our stock-TR3 result. It supports testing low and high thinking for agent chains, retaining the W20 tool fixes, and supplying distinct explicit seeds when measuring variance. The report's full-suite score also has a different denominator from our 15-case core gate.

For the bounded chain follow-up, use `tool-eval-bench` at `c7b5b95504dbdd9e6c080aa74c776e9fe79c41c4` with `--categories C --temperature 0.3 --parallel 1 --timeout 120 --max-turns 8`. Compare thinking off, low and high across the same three seeds (`20260930`–`20260932`) on each recipe. Off uses `--no-think`; low/high use `--backend-kwargs` containing both top-level `reasoning_effort` and `chat_template_kwargs` with `enable_thinking: true` and the matching `reasoning_effort`. Save each run separately and inspect the four chain scenarios, output-cap failures and latency. This supplements the existing core/API gates; the primary long-session performance settings are temperature zero, seed `20260930` and thinking disabled.

The [acceptance gate](tests/acceptance.py) requires the colors of five distinct images in randomized input order and checks the known scheduler regression: a correct short reply must begin within five seconds while an earlier decode is still active. It exits nonzero on failure. `--url` accepts either the server root or the API base ending in `/v1`. The retained control passed this live gate on September 30, with 0.519s newcomer TTFT. For deeper mixed-prefill diagnosis, use Mia's [upstream probe at the locked revision](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks/blob/674155dec2f2f62bb879801b5ce2cfc759a0bebf/tests/test_mixed_prefill_decode.py) on the head; it measures decode overlap and newcomer latency but does not enforce the short-arrival gate. Benchmark output and experimental receipts stay outside git.

The [agent gate](tests/agent_acceptance.py) exercises streaming/non-streaming tools, null assistant content, thinking, interleaved histories with identical tool arguments, structured responses, cache accounting, bounded execution of generated Python, and cancellation. Candidate runs require `--require-cache`; the older control omits the cache-details flag. Inspect per-case results, including model failures and format limitations, rather than counting a completed process as a pass. Generated-code execution permits restricted pure Python under CPU/memory limits.

Run [Mia's CUDA regression checks](tests/mia_gpu_fixes.py) via `docker exec` in its SparkRun-owned container while serving is idle. They test real ring allocation, production prefill/decode kernels against a rejected-draft negative control, padded-stride seed isolation, and draft/target noise independence in FP32/FP64 with greedy invariance. These checks validate the backports, rather than general model quality.

The [long-session client](benchmarks/long_sessions.py) complements the native throughput profile. Run it on the head using `uv run --with llama-benchy==0.4.0 python`, the pinned target `tokenizer.json`, an absolute recipe path, and output outside the checkout. `--suite full` covers C1/C2/C4 controls, unrelated/shared prefixes, warm replay, edits/forks, 250k, the 262k admission boundary, and a short newcomer during long prefill. `--suite repeat` repeats the C4 128k cold/warm pair after a fresh start; give each paired boot a new `--campaign` value so persistent TensorFold sessions do not turn a cold case into a disk replay. `--suite 250k` runs only the C4 250k cold/warm pair, including the link-recovery workload gate after an interrupted campaign; use a fresh instance and a new campaign value. `--suite ceiling` separately probes one request near 850k after cheaper gates pass. Payloads are synthetic repository text, with local and API token counts recorded; this is not a general long-context coding-quality benchmark.

The client records per-stream TTFT, elapsed time, chunk gaps, usage/cache details, aggregate end-to-end rate and both nodes' memory minima. Failed content checks and interrupted streams retain response text, timing and errors in a separate `.failed.jsonl` receipt and still fail the campaign. Aim for 8 GiB MemAvailable per node; abort on allocation/kernel errors or sustained headroom below 2 GiB. The temporary campaign monitor stops the corresponding recipe through SparkRun after three low-memory samples or lost telemetry. Use `--require-stable-links` during link recovery and the resumed campaign: it records `enP7s7` carrier counters and aborts on lost 10GbE carrier or any new drop, even if SSH recovers between samples. It does not install a persistent monitor. The retained control starts with about 6.4 GiB available on the head, already below the target.

The September 30 fresh control's initial C4 128k cold/replay pair completed in 341.35/340.73 seconds, with slowest content TTFT 331.25/330.76 seconds. Each stream submitted 131,108 API prompt tokens and produced 256 completion tokens at temperature zero with thinking disabled. Minimum available memory across the pair was 5.73 GiB on the head and 8.46 GiB on the worker. Replay did not establish a useful improvement; this is a prefill-heavy end-to-end measurement, not a steady decode rate. Receipts are `receipts/baseline-long.jsonl` and its memory companion under the retained campaign directory.

Six 128k cases completed before the C4 250k phase aborted on lost worker SSH/memory telemetry at 21:33 EDT. No valid 250k result was produced. The worker's management NIC recorded carrier drops beginning at 20:42, with further drops after both inference containers were stopped through SparkRun; UniFi independently recorded SFP receive-signal loss on Core Aggregation port 8. The retained journal showed no OOM/Xid, and EEE was already disabled. This was a telemetry-loss abort, not a measured low-memory abort. The network incident limits confidence in the preliminary timing evidence. [Cable/module isolation and recovery acceptance](https://github.com/kelchm/home-lab/issues/649) block candidate live qualification, larger-context cases and fresh-start comparisons; [the recipe investigation](https://github.com/kelchm/home-lab/issues/645) and [draft PR](https://github.com/kelchm/home-lab/pull/648) remain open. Raw NIC and switch receipts are alongside the benchmark output.

After swapping the port 7/8 modules and replugging both cable ends, both links passed a 30-minute idle observation at 10GbE with unchanged carrier counters. The first recovery C4 250k cold phase also kept both links stable, but its content check failed when the stock model refused to quote the synthetic `REVIEW_SECRET` label as a credential. The client now names it `PUBLIC_REVIEW_MARKER` and explicitly identifies it as public synthetic data. A fresh cold/replay recovery pair is required; use this same revised wording for every arm and do not mix its measurements with the earlier payloads. The manual swap/replug window does not establish whether the fault followed a module.

### Token usage accounting

The refreshed Mia recipe includes [`--enable-prompt-tokens-details`](https://docs.vllm.ai/en/latest/cli/serve/#--enable-prompt-tokens-details). It exposes `usage.prompt_tokens_details.cached_tokens` in API responses and is independent of `--enable-prefix-caching`. TensorFold exposes the same usage breakdown from session reuse. `usage.prompt_tokens` includes both cached and fresh input; a missing cache breakdown does not mean every prompt token was freshly processed.

The [September 26 usage investigation](https://app.t3.codes/085bbae7-4da9-4bfa-86ed-ac1a17ad3763/8c1a694a-b03e-4b5d-80f1-79b1d9f778e4) confirmed that the previous GLM launch omitted this flag: vLLM recorded cache reuse internally, while OpenCode stored the whole prompt as ordinary input and Codeburn lost that distinction. Verification of the updated candidates through OpenCode/Codeburn remains part of the current qualification.

After enabling it, repeat an identical long prompt while its prefix is still cached and confirm a warm response reports nonzero `usage.prompt_tokens_details.cached_tokens`. For streaming requests, send `"stream_options": {"include_usage": true}` and inspect the final usage chunk. Then verify the same call's cache reads reach OpenCode's saved `tokens.cache.read` and Codeburn's cache breakdown. Server metrics alone do not establish that the client captured them, and missing details must not be interpreted as zero cache reuse.

## Settings and limits

The [recipe](mia-glm53-exl3.yaml) keeps InstantTensor, EXL3/TR3 target TP2, DFlash2 k=7 with draft TP2, 850,000 context, four sequences, 7,168-token batches, FP8 KV, CUDA graphs, fair mixed-prefill scheduling, and abliteration disabled. Image and model revisions are immutable pins. Source and image are pinned separately; the source revision is not a claim about image build provenance.

`INSTANTTENSOR_BUFFER_SIZE=2147483648` caps loader I/O staging at 2 GiB instead of the installed InstantTensor 0.2.0 TP2 default of 4 GiB. The library automatically reduces I/O depth; the largest target tensor is about 1.18 GiB. This is a loading bound, not a promise of 2 GiB lower steady-state RAM. The refreshed Mia recipe's explicit 11 GiB KV pool governs serving allocation instead of the `.85` utilization setting; the retained control uses 14 GiB. Eight NCCL channels, right-sized indexer workspace, a 1 GiB multimodal processor cache and graph estimation disabled remain; graph execution stays enabled. Dense FP8 `all`, BF16 large-M KDA and compact draft KV follow the coherent current upstream bundle.

TensorFold uses q4mse non-experts, latent FP8 KV, multi-slot prefill, W19 scratch/NCCL settings, W20 tools, a 2 GiB RAM session store and a 64 GiB NVMe tier per rank. Its shared pool reserves prompt plus output plus 64 tokens per request. The 850k ceiling describes one request while pool space is available; slot admission, memory minima, cold-prefill latency and actual decode overlap must be measured. Vision is enabled in both recipes; logprobs and maximum-image/context capabilities are not assumed equivalent.

SparkRun's fit estimate does not account for the full hybrid cache state; use vLLM allocation and host telemetry to judge fit. The 850k allocation/retrieval test completed with cache preemptions and replay, not uninterrupted prefill. 850k is a per-request ceiling, not a promise of four simultaneous 850k requests or responsive long-prefill latency. Idle prefix retention is much smaller than equivalent-token capacity. The configured 48-image/1-video maximum, simultaneous maximum-context workloads, general long-context quality and reboot recovery have not been qualified.

## Update the upstream bundle

`mods/mia-glm53/prepare.py` fetches the reviewed files in `upstream.lock.json`, verifies their checksums, and materializes an ignored `upstream/` directory. SparkRun copies the mod to both ranks; `run.sh` verifies the bundle and applies the locked patch sequence. Missing/changed files, duplicate runtime basenames and inconsistent patch entries fail verification. The pinned image supplies compiled kernels.

For an upstream update, review Mia's launcher, runtime files and relevant tests together. Update the source revision, selected-file hashes and patch order in the lock, plus the recipe's source metadata and image/model pins as needed. Preserve the license files in the bundle. Check new tensor sizes before retaining the loader buffer cap. Re-prepare, run the dry run, then validate the deployed candidate. Do not update checksums merely to silence a failure.

Stop/relaunch after mod changes and use `--fresh` for new benchmark comparisons: SparkRun fingerprints declared mod references, not their contents. The README and recipe record deliberate site choices; there is no second parser mirroring all upstream launcher defaults.
