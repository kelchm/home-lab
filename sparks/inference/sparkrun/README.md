# GLM-5.3-Flash recipes through SparkRun

These recipes serve `GLM-5.3-Flash-EXL3` across two DGX Sparks at `http://10.32.21.31:8888/v1`. SparkRun 0.3.10 owns model/image distribution, fabric detection, containers, runtime caches, readiness, warmup, logs, stop, and native benchmarking for both engines. TensorFold uses the adjacent external runtime adapter. These hosts are outside Flux. Check actual host state before launching; other experiments can use either GPU.

The September 30/October 1 candidates are under evaluation in [#645](https://github.com/kelchm/home-lab/issues/645) and [draft PR #648](https://github.com/kelchm/home-lab/pull/648). Mia TensorFold v1.2 passed native boot/shape warmup, the live API/client gates, all 13 long-session cases and actual C4 output growth on October 1. Refreshed Mia/vLLM v7 passed source validation and the native dry run; its live comparison and the fresh TensorFold repeat remain pending. TensorFold W20 remains a diagnostic comparison, and the exact September 19 Mia recipe, image and prepared mod remain the fallback. Provisional trials require fresh healthy-link/idle checks and abort on new carrier drops, lost telemetry or sustained sub-2 GiB headroom; the separate defect in [#649](https://github.com/kelchm/home-lab/issues/649) remains open. These are evaluation candidates; raw receipts stay outside git.

| Recipe | Source/build | Target / draft | Capacity choice |
| --- | --- | --- | --- |
| [Mia TensorFold](mia-tensorfold-glm53-exl3.yaml) | Mia v1.2 `1f3d909b`, TensorFold 0.5.0 + 53 patches, published OCI digest, site history/health guard | Same stock TR3 / new `bf582e4e` draft | 850k request ceiling, C4, dynamic FP8 pool, 18.5 GiB host reserve; requires at least 1.2M reported pool tokens |
| [Refreshed Mia](mia-glm53-exl3.yaml) | Mia `6278ecb0` overlays on the `674155de` CUDA image, scheduler v7 and site DFlash fixes | Stock TR3 `25a44fdb` / `dc77ff1c` | 850k request ceiling, C4, 11 GiB KV per rank |
| [TensorFold W20](tensorfold-glm53-exl3.yaml) | Jay `b463237b`, engine `2f8e514b`, selected 75 patches, site stop/stats/history guard | Same stock TR3 / published W20 draft `7d74cdd8` | 850k request ceiling, four slots, shared 1,048,576-token latent KV pool |

This compares complete recipes: the draft weights, arithmetic, scheduler and cache implementation differ. Normal evaluation is C4 at 128k–262k, with C1/C2 controls. Four 262k prompts plus reserved output exceed W20's shared pool and must queue. Mia TensorFold allocates prompt extents, then grows them during decode; its measured startup pool is required before making a capacity claim. C8 is a secondary short/warm workload. None of these recipes promises four simultaneous 850k requests.

W20's output reservations matter before that boundary: four 250k prompts with 32,768 output tokens and the 64-token reserve require at least 1,131,328 pool tokens. An 8,192-token output budget puts that arithmetic total at 1,033,024. The 256-token primary timing cases do not qualify C4 at the client's full 32k output limit. The full suite includes a 250k case with that budget and a short expected answer. Mia's kit does not reserve that whole budget at admission, so a separate growth case requires actual long output. TensorFold v1.2 completed four 250k prompts with 4,096 actual output tokens each; full 32k replies remain unqualified. Inspect admission, pool growth, pauses and tail latency before choosing client defaults.

Grok's native-X follow-up from October 1 02:27–10:22 UTC checked [Mia's 09:59 announcement](https://x.com/MiaAI_lab/status/2105598458547646875) and related Mia/Netrunner/Ash/Jay posts. It found the new [published kit at `ed026ef9`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/tree/ed026ef92d1650120dada1294a112acb6c8f2f48); independent long-context reproduction remains thin. This supersedes the earlier “no public Mia kit” finding. The initial kit used TensorFold 0.5.0 plus 52 patches, with selected 0.6.0 backports. The candidate now pins [v1.2 at `1f3d909b`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold/blob/1f3d909b00b7be7aa8f00d3a33e0b9e7aa56d221/CHANGELOG.md), merged at 13:07 UTC: the 53-patch image adds bounded many-media input and the cache cap rises to 12.5 GiB. NFS worker weights and removal of the upstream benchmark do not change our native SparkRun distribution or benchmark client. It is separate from both our W20 engine 0.3.4 bundle and upstream [TensorFold 0.6.0](https://github.com/ashhart/TensorFold/releases/tag/v0.6.0).

Mia reports 60.4 tok/s for one prose stream and 108.8 tok/s aggregate at C4, measured through sparkDash with 2,200 MHz clocks. Its 262,170-token prefill is 159.69 seconds, and identical 64k replay is under 0.07 seconds. These are maintainer results, not our C4 250k measurements. The initial advertised pool was 2,684,928 tokens; v1.2 reports 2,852,864 at its measured start, with 4.7/8.8 GiB minimum head/worker memory under a 1M prompt. These pools vary with startup memory and our larger host reserve; the default 1,048,576 request window does not provide four simultaneous 1M requests. We retain 850k for this comparison. HF metadata confirms that the kit's target revision `9eaebb7c` differs from our `25a44fdb` only in the model card; its new draft revision changes the actual draft tensor file.

Grok’s bounded follow-up requested the October 1 10:22–13:30 UTC X window and finished before 13:34 UTC. Mia/Netrunner coverage was checked; independent v1.2 and long-agent reproduction remains thin. [Niklas’s firsthand v1.0 report](https://x.com/niklaslenz_ai/status/2105643482354155745) reproduced roughly 91–98% of the advertised short decode rates but reported lower prefill through the retired upstream client; differing payloads/meters do not establish a matched regression. [Mia says disk KV is not planned](https://x.com/MiaAI_lab/status/2105642709427822840), making in-memory prefix retention and eviction a material part of this evaluation.

A further native-X pass checked October 1 13:30–15:12 UTC for the cache-retention lead. [Tom’s firsthand comparison](https://x.com/tomsmialowski/status/2105669610817093839) used 400-token replies, three single-stream trials and only two C4 trials; [Soveryn’s C4 report](https://x.com/Soveryn_AI/status/2105667568254038134) omitted context length, replay and tool-loop details. Both support further TensorFold evaluation, but neither qualifies our long-agent workload. Mia and Netrunner were checked; independent 128k–262k C4 cache/tool evidence remains thin, with no GLM-specific cache-entry tuning result found. Both Mia repository heads were unchanged at 15:12 UTC. A Qwen/one-Spark eviction report uses another engine family and cannot supply this recipe’s cache settings.

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

The September 30 recipes pin local Docker image IDs from the evaluation head. SparkRun distributes those images to the worker. The IDs identify our built artifacts, rather than public registry manifests; rebuilding from the same inputs can produce another ID. Record the build receipt, verify the resulting image, and update the recipe pin before using a rebuild. The October 1 Mia TensorFold recipe instead pins the published ARM64 registry manifest by digest. Do not silently substitute a mutable upstream tag.

For the new Mia TensorFold candidate, prepare the small site mod, then use the same external adapter and SparkRun lifecycle as W20:

```sh
python3 mods/mia-tensorfold/prepare.py
python3 mods/mia-tensorfold/prepare.py --check
sparkrun run ./mia-tensorfold-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
# Provisional evaluation: require both-host idle/healthy-link checks and the finite campaign monitor.
sparkrun run ./mia-tensorfold-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
sparkrun logs ./mia-tensorfold-glm53-exl3.yaml --cluster sparks
sparkrun stop ./mia-tensorfold-glm53-exl3.yaml --cluster sparks
```

The v1.2 registry manifest `6ee3c6e0…` resolves to ARM64 image config `3306d339…`, labelled with patch hash `cb7c56f7f921`. All 53 patches applied to the pinned engine in a source-only check; all 71 runtime files found in the published OCI patch layer match that reconstructed source byte for byte, including CUDA/C++ and the three guarded Python files. The mod verifies the guarded files' upstream or already-patched hashes before changing them. It narrows reasoning restoration to original server call IDs and maps a broken concurrent decoder or dead scheduler thread to HTTP 503. This health guard does not add W20's stall detector. SparkRun handles fabric discovery, rank rendezvous, downloads, kernel caches and stop; the upstream `start.sh`/`stop.sh`, window fallback and clock controls are not used.

The published reasoning cache includes earlier history in its fallback signature, improving on W20, but still ignores call IDs and reasoning. A source-only reproduction with two replies to identical histories restored B's reasoning into renumbered A. The [Mia regression](tests/mia_tensorfold_site.py) fails on the checksum-matched published code and passes after the guard; it also checks caller reasoning, eviction and real HTTP health responses. Repeat it inside the SparkRun-owned container before GPU/API qualification. This is narrower client support: renamed/missing call IDs require the client to retain its own reasoning.

Mia's default memory reserve is 14.5 GiB. Our candidate uses 18.5 GiB toward the 8 GiB available-memory goal, using the latest 12.5 GiB extra-cache cap; this reduces possible pool capacity. The readiness gate requires exactly four streams, an 850k request window and at least 1.2M pool tokens, then performs C4 shape warmup. That pool floor allows room for four 262,144-token prompts plus 32k replies and extent rounding; it is a capacity check, not proof of completed concurrent replies. Report actual startup capacity, pool pauses and both nodes' memory minima. Kernel caches are keyed by image; this kit does not inherit W20's prepared-weight or NVMe-session behavior. The initial signed candidate was staged on the head from a checksum-verified archive, and preparation/check plus the native SparkRun 0.3.10 dry run passed on October 1. Its first guarded boot loaded C4, an 850k window and a 1,875,968-token pool; native shape warmup passed, with 11.382/10.793 GiB observed head/worker minima. Worker telemetry then failed and a new carrier drop was observed, so the guard aborted and all timings are excluded. Both containers were subsequently stopped through SparkRun. The v1.2 boot with the same reserve and 12.5 GiB cap loaded a 1,808,384-token pool and passed shape warmup, then lost worker telemetry; its timings are excluded. A subsequent cached boot loaded 2,318,336 tokens and passed source checks inside the native container, strict C4/850k health and shape warmup. Through API qualification its observed minima were 10.086/12.364 GiB with no new link drops. Startup capacity depends on available memory; the larger cap does not guarantee a larger pool on every boot. Matched long-session performance is still pending. The dry-run fit estimate lacks model/architecture metadata and reports zero weights/cache; it does not establish memory fit. Image/model distribution and live checks proceed as provisional monitored trials; the unresolved network fault remains tracked separately.

The existing Mia image was built at `674155de`; the current mod pins `6278ecb0`, merged October 1 at 11:45 UTC. Only two locked runtime overlays changed: scheduler v7 handles native KV-allocation refusal without starving another prefill, and Mamba alignment accepts that helper. The mod also installs the pinned migration fixture and runs the source-only scheduler self-test on each rank before serving. Migration of the actual immutable base image’s v5+priority source passed, was idempotent, and passed the installer checks, 31 scheduling regressions, Mamba checkpoint/alignment checks and eight layout/progress/canary tests with 12 subtests. An independent source review found no issue affecting our FCFS/fair/C4 path. No CUDA/C++ source changes occur in this update. This does not establish live v7 performance. Upstream’s TP2 progress canary passed; several performance/identity cells were inconclusive because repeated baselines varied, so those are not speedup claims. FCFS and the existing chunk defaults remain in use.

For a fresh Mia build, check out the current source outside the recipe checkout and build its ARM/CUDA image:

```sh
git clone https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks.git ~/sparkrun/builds/mia-6278ecb0
git -C ~/sparkrun/builds/mia-6278ecb0 checkout --detach 6278ecb01034cea9ef6de0f851d09fccafe3e835
docker build --progress plain --build-arg GLM53_RECIPE_STAMP=6278ecb01034cea9ef6de0f851d09fccafe3e835 \
  -t glm53-mia:6278ecb0-20261001 ~/sparkrun/builds/mia-6278ecb0
docker image inspect --format '{{.Id}}' glm53-mia:6278ecb0-20261001
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

SparkRun 0.3.10's generic readiness probe checks HTTP status only. Both TensorFold candidates map fatal health to HTTP 503; W20 also detects stalls. The `post_exec` gate requires JSON `ok: true`, strict mode, exactly four loaded slots and the configured context ceiling, then checks live C4 shape warmup. Mia additionally requires a minimum reported pool size. A requested slot count alone is insufficient: W20 can load fewer slots when memory is tight.

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

SparkRun 0.3.10 shell-quotes its llama-benchy argument list before direct subprocess execution. The profile uses one shell-safe comma-separated `key=value` string for `extra_body`. A YAML list becomes multiple values after one flag, while llama-benchy accepts one value per flag; the first October 1 diagnostic failed argument parsing before sending requests. JSON-valued entries such as `chat_template_kwargs` acquire literal quotes and do not reach the intended API field. Inspect the constructed argv and parsed request fields before adding complex settings. The initial control sweep omitted explicit temperature and is excluded from matched comparisons.

Tool evaluation uses the upstream `tool-eval-bench` v1.8.0 CLI directly for 15 core scenarios. SparkRun 0.3.9's tool integration passes the model repository ID instead of the served alias, ignores a model argument override, and shell-quotes JSON arguments before direct subprocess execution. The direct command avoids those integration bugs without a wrapper or installed patch. Inspect `scores.scenario_results` and the native conversation report, not just the exit status: completion does not mean the scenarios passed. No results are submitted to Spark Arena.

TensorFold's [W21 tool report](https://github.com/jayleaton/glm53-tensorfold-spark/pull/14), published during this campaign, adds measurements on the existing production build. Its abliterated checkpoint and one run per configuration do not establish our stock-TR3 result. It supports testing low and high thinking for agent chains, retaining the W20 tool fixes, and supplying distinct explicit seeds when measuring variance. The report's full-suite score also has a different denominator from our 15-case core gate.

For the bounded chain follow-up, use `tool-eval-bench` at `c7b5b95504dbdd9e6c080aa74c776e9fe79c41c4` with `--categories C --temperature 0.3 --parallel 1 --timeout 120 --max-turns 8`. Compare thinking off, low and high across the same three seeds (`20260930`–`20260932`) on each recipe. Off uses `--no-think`; low/high use `--backend-kwargs` containing both top-level `reasoning_effort` and `chat_template_kwargs` with `enable_thinking: true` and the matching `reasoning_effort`. Save each run separately and inspect the four chain scenarios, output-cap failures and latency. This supplements the existing core/API gates; the primary long-session performance settings are temperature zero, seed `20260930` and thinking disabled.

The [acceptance gate](tests/acceptance.py) requires the colors of five distinct images in randomized input order and checks the known scheduler regression: a correct short reply must begin within five seconds while an earlier decode is still active. It exits nonzero on failure. `--url` accepts either the server root or the API base ending in `/v1`. The retained control passed this live gate on September 30, with 0.519s newcomer TTFT. For deeper mixed-prefill diagnosis, use Mia's [upstream probe at the locked revision](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks/blob/6278ecb01034cea9ef6de0f851d09fccafe3e835/tests/test_mixed_prefill_decode.py) on the head; it measures decode overlap and newcomer latency but does not enforce the short-arrival gate. Benchmark output and experimental receipts stay outside git.

The [agent gate](tests/agent_acceptance.py) exercises streaming/non-streaming tools, null assistant content, thinking, interleaved histories with identical tool arguments, structured responses, cache accounting, bounded execution of generated Python, and cancellation. Candidate runs require `--require-cache`; the older control omits the cache-details flag. Inspect per-case results, including model failures and format limitations, rather than counting a completed process as a pass. Generated-code execution permits restricted pure Python under CPU/memory limits.

The October 1 Mia TensorFold v1.2 live gate passed five-image input and short arrival during decode (0.203s TTFT), plus all 20 agent cases: streamed/non-streamed tools, null-content continuations, interleaved high-thinking histories, structured output, cache details, six bounded generated-code checks and cancellation. The original isolation prompt requested an immediate multiplication and legitimately emitted no reasoning text; it could not exercise restoration. A source-reviewed input-derivation prompt produced reasoning in both response formats and passed the unchanged history-isolation assertions. Use the revised prompt on both engines. These small checks do not establish long-context agent quality. Receipts are `mia-tf-v12-20261001c-agent.json`, section logs and `mia-v12-thinking-probe.json`.

Run [Mia's CUDA regression checks](tests/mia_gpu_fixes.py) via `docker exec` in its SparkRun-owned container while serving is idle. They test real ring allocation, production prefill/decode kernels against a rejected-draft negative control, padded-stride seed isolation, and draft/target noise independence in FP32/FP64 with greedy invariance. These checks validate the backports, rather than general model quality.

The [long-session client](benchmarks/long_sessions.py) complements the native throughput profile. Run it on the head using `uv run --with llama-benchy==0.4.0 python`, the pinned target `tokenizer.json`, an absolute recipe path, and output outside the checkout. `--suite full` covers C1/C2/C4 controls, unrelated/shared prefixes, warm replay, edits/forks, 250k with small and 32k output budgets, the 262k admission boundary, and a short newcomer during long prefill. `--suite growth` sends four unrelated 250k prompts with 4,096 output-token limits and requires at least 2,048 actual completion tokens per stream; a short response fails and retains its receipt. It exercises extent growth, rather than assuming a requested limit was generated, and does not qualify full 32k replies. `--suite repeat` repeats the C4 128k cold/warm pair after a fresh start; give each paired boot a new `--campaign` value so persistent W20 sessions do not turn a cold case into a disk replay. `--suite 250k` runs only the C4 250k cold/warm pair, including the link-recovery workload gate after an interrupted campaign; use a fresh instance and a new campaign value. `--suite ceiling` separately probes one request near 850k after cheaper gates pass. Payloads are synthetic repository text, with local and API token counts recorded; this is not a general long-context coding-quality benchmark.

The client records per-stream TTFT, elapsed time, chunk gaps, usage/cache details, aggregate end-to-end rate and both nodes' memory minima. Failed content checks and interrupted streams retain response text, timing and errors in a separate `.failed.jsonl` receipt and still fail the campaign. Aim for 8 GiB MemAvailable per node; abort on allocation/kernel errors or sustained headroom below 2 GiB. The temporary campaign monitor stops the corresponding recipe through SparkRun after three low-memory samples or lost telemetry. Use `--require-stable-links` during link recovery and the resumed campaign: it records `enP7s7` carrier counters and aborts on lost 10GbE carrier or any new drop, even if SSH recovers between samples. It does not install a persistent monitor. The retained control starts with about 6.4 GiB available on the head, already below the target.

The October 1 TensorFold v1.2 full sweep used temperature zero, seed `20260930`, thinking disabled and campaign `glm53-matched-20261001c`. All 13 cases passed on one native boot with a 2,318,336-token pool. Primary generation cases produced 256 tokens per stream; marker-only retrieval cases produced nine. These are end-to-end wall times for synthetic repository payloads, including prefill, rather than steady decode rates or a general coding-quality score.

| C4 workload | Cold wall | Replay wall | Cached tokens on replay |
| --- | --- | --- | --- |
| Unrelated 128k prefixes | 305.87s | 90.04s | Three streams at 131,072; one miss |
| Unrelated 250k prefixes | 626.21s | 14.55s | All four at 250,048 |
| Shared 128k prefix | 309.75s | 8.83s | All four at 131,072 |

The 128k middle-edit case took 283.96s with no cache hits; the fork case took 211.41s, reusing 65,536 tokens on two streams and none on two. Concurrent cold requests with a shared prefix did not collapse into one prefill. Four 262,167-token API prompts completed correct retrieval in 616.51s, and a short newcomer during two long prefills began its correct reply in 1.98s. Across the full sweep, observed minimum available memory was 8.731/12.028 GiB on head/worker, maximum swap use was 0.202/0.106 GiB, both links remained healthy with unchanged counters, and no pool pauses appeared in 1,699 two-second health samples. Cache retention and tail latency matter alongside the strong 250k replay result; repeatability and the matched v7 comparison remain pending.

The separate growth case passed with four 250,062-token prompts and 4,096 actual output tokens per stream. Three streams reused 249,984 tokens and one missed, so its 375.41s wall time is a mixed-cache capacity result. Minimum available memory was 8.795/11.943 GiB, with no new link drops or observed pool pauses. It qualifies growth beyond a 2,048-token extent, not full 32k replies. Receipts are `mia-tf-v12-long-20261001c.jsonl`, `mia-tf-v12-full-20261001c.result.json`, `mia-tf-v12-full-health-20261001c.jsonl`, `mia-tf-v12-growth-20261001c.jsonl` and their memory/health companions. A subsequent fresh repeat lost worker telemetry before readiness and contributes no timing result.

The September 30 fresh control's initial C4 128k cold/replay pair completed in 341.35/340.73 seconds, with slowest content TTFT 331.25/330.76 seconds. Each stream submitted 131,108 API prompt tokens and produced 256 completion tokens at temperature zero with thinking disabled. Minimum available memory across the pair was 5.73 GiB on the head and 8.46 GiB on the worker. Replay did not establish a useful improvement; this is a prefill-heavy end-to-end measurement, not a steady decode rate. Receipts are `receipts/baseline-long.jsonl` and its memory companion under the retained campaign directory.

Six 128k cases completed before the original C4 250k phase aborted on lost worker SSH/memory telemetry at 21:33 EDT, producing no valid 250k result. The worker's management NIC recorded carrier drops beginning at 20:42, with further drops after both inference containers were stopped through SparkRun; UniFi independently recorded SFP receive-signal loss on Core Aggregation port 8. The retained journal showed no OOM/Xid, and EEE was already disabled. This was a telemetry-loss abort, not a measured low-memory abort. The network incident limits confidence in the preliminary timing evidence. [Cable/module isolation and recovery acceptance](https://github.com/kelchm/home-lab/issues/649) remain open pending the switch fault-history recheck; [the recipe investigation](https://github.com/kelchm/home-lab/issues/645) and [draft PR](https://github.com/kelchm/home-lab/pull/648) remain open. Raw NIC and switch receipts are alongside the benchmark output.

After swapping the port 7/8 modules and replugging both cable ends, both links passed a 30-minute idle observation at 10GbE with unchanged carrier counters. The first recovery C4 250k cold phase also kept both links stable, but its content check failed when the stock model refused to quote the synthetic `REVIEW_SECRET` label as a credential. The client now names it `PUBLIC_REVIEW_MARKER`, explicitly identifies it as public synthetic data and asks for its assigned value. Both revised 8k retrieval/long-output probes passed before the fresh recovery pair. Use this same revised wording and fixed seed for every arm; do not mix its measurements with the earlier payloads. The manual swap/replug window does not establish whether the fault followed a module.

The revised C4 250k recovery pair passed at 23:39 EDT: cold/replay wall time was 628.25/628.34 seconds, with slowest content TTFT 617.76/616.20 seconds. Every stream submitted 250,054 API prompt tokens and produced 256 completion tokens at temperature zero, seed `20260930`, with thinking disabled. All eight responses passed the marker check; both links stayed at 10GbE with zero new carrier drops and no matching kernel OOM/Xid/allocation/PCIe errors. Minimum available memory was 5.124 GiB on the head and 8.037 GiB on the worker; maximum swap use was 0.206/0.106 GiB. This single pair shows no useful replay gain and does not establish repeatability or general long-context coding quality. Boot-cumulative prefix-cache counters, including warmup and probes, are retained separately and are not per-stream cache accounting. Receipts are `receipts/baseline-recovery-c-250k.jsonl`, its memory companion, `recovery-c-profile.json` and `recovery-c-summary.json`. Both inference containers were then stopped through SparkRun. Management/storage reachability and GPU-idle checks passed; the switch fault-history recheck still requires controller sign-in and is tracked separately from provisional recipe trials. The October 1 morning preflight found seven further spark-2 carrier drops at September 30 23:48–23:51 EDT, after the accepted recovery run; the operator confirmed no manual changes during that window, so the fault recurred after the module swap/replugs. Both hosts were idle at 10GbE with about 117 GiB available and clean gateway/management/storage pings, but the unchanged-counter gate failed. The recurrence remains tracked separately. Provisional recipe trials may proceed with a fresh healthy-link/idle baseline and finite monitoring that aborts on any new drop, lost telemetry or sustained sub-2 GiB memory; those trials do not accept the network fault as resolved.

### Token usage accounting

The refreshed Mia recipe includes [`--enable-prompt-tokens-details`](https://docs.vllm.ai/en/latest/cli/serve/#--enable-prompt-tokens-details). It exposes `usage.prompt_tokens_details.cached_tokens` in API responses and is independent of `--enable-prefix-caching`. TensorFold exposes the same usage breakdown from session reuse. `usage.prompt_tokens` includes both cached and fresh input; a missing cache breakdown does not mean every prompt token was freshly processed.

The [September 26 usage investigation](https://app.t3.codes/085bbae7-4da9-4bfa-86ed-ac1a17ad3763/8c1a694a-b03e-4b5d-80f1-79b1d9f778e4) confirmed that the previous GLM launch omitted this flag: vLLM recorded cache reuse internally, while OpenCode stored the whole prompt as ordinary input and Codeburn lost that distinction. The Mia TensorFold v1.2 trial verified this through an isolated OpenCode 1.18.33 session using the OpenAI-compatible provider and Codeburn 0.9.25. One tool read plus a continuation produced three API calls: 8,276 ordinary input tokens, 155 output tokens and 11,648 cache-read tokens (0/3,456/8,192 per call). Codeburn matched every saved OpenCode call. This establishes accounting for that client/provider configuration, not arbitrary clients or renamed tool IDs; refreshed Mia/vLLM still needs the same check. Local receipts are `client-summary.json`, `saved-messages.json` and `codeburn-export.json`.

After enabling it, repeat an identical long prompt while its prefix is still cached and confirm a warm response reports nonzero `usage.prompt_tokens_details.cached_tokens`. For streaming requests, send `"stream_options": {"include_usage": true}` and inspect the final usage chunk. Then verify the same call's cache reads reach OpenCode's saved `tokens.cache.read` and Codeburn's cache breakdown. Server metrics alone do not establish that the client captured them, and missing details must not be interpreted as zero cache reuse.

## Settings and limits

The [recipe](mia-glm53-exl3.yaml) keeps InstantTensor, EXL3/TR3 target TP2, DFlash2 k=7 with draft TP2, 850,000 context, four sequences, 7,168-token batches, FP8 KV, CUDA graphs, fair mixed-prefill scheduling, and abliteration disabled. Image and model revisions are immutable pins. Source and image are pinned separately; the source revision is not a claim about image build provenance.

`INSTANTTENSOR_BUFFER_SIZE=2147483648` caps loader I/O staging at 2 GiB instead of the installed InstantTensor 0.2.0 TP2 default of 4 GiB. The library automatically reduces I/O depth; the largest target tensor is about 1.18 GiB. This is a loading bound, not a promise of 2 GiB lower steady-state RAM. The refreshed Mia recipe's explicit 11 GiB KV pool governs serving allocation instead of the `.85` utilization setting; the retained control uses 14 GiB. Eight NCCL channels, right-sized indexer workspace, a 1 GiB multimodal processor cache and graph estimation disabled remain; graph execution stays enabled. Dense FP8 `all`, BF16 large-M KDA and compact draft KV follow the coherent current upstream bundle.

W20 uses q4mse non-experts, latent FP8 KV, multi-slot prefill, W19 scratch/NCCL settings, W20 tools, a 2 GiB RAM session store and a 64 GiB NVMe tier per rank. Its shared pool reserves prompt plus output plus 64 tokens per request. Mia TensorFold uses its separate q4/FP8/chunked-KDA, DFlash2/copy-draft and multi-prefill bundle; dense weights and KV are lossy and chunked KDA changes arithmetic. Its pool admits aligned prompt extents and grows them during decode. The 850k ceiling describes one request while pool space is available; slot admission, memory minima, cold-prefill latency and actual decode overlap must be measured. Vision is enabled in all candidates; logprobs and maximum-image/context capabilities are not assumed equivalent.

SparkRun's fit estimate does not account for the full hybrid cache state; use vLLM allocation and host telemetry to judge fit. The 850k allocation/retrieval test completed with cache preemptions and replay, not uninterrupted prefill. 850k is a per-request ceiling, not a promise of four simultaneous 850k requests or responsive long-prefill latency. Idle prefix retention is much smaller than equivalent-token capacity. The vLLM 48-image/1-video and Mia TensorFold v1.2 50-image/4-video maxima, simultaneous maximum-context workloads, general long-context quality and reboot recovery have not been qualified. Mia v1.2 bounds request-wide image/video tokens to 16,384/32,768; our media gate checks five images, not those full maxima.

## Update the upstream bundle

`mods/mia-glm53/prepare.py` fetches the reviewed files in `upstream.lock.json`, verifies their checksums, and materializes an ignored `upstream/` directory. SparkRun copies the mod to both ranks; `run.sh` verifies the bundle and applies the locked patch sequence. Missing/changed files, duplicate runtime basenames and inconsistent patch entries fail verification. The pinned image supplies compiled kernels.

For an upstream update, review Mia's launcher, runtime files and relevant tests together. Update the source revision, selected-file hashes and patch order in the lock, plus the recipe's source metadata and image/model pins as needed. Preserve the license files in the bundle. Check new tensor sizes before retaining the loader buffer cap. Re-prepare, run the dry run, then validate the deployed candidate. Do not update checksums merely to silence a failure.

Stop/relaunch after mod changes and use `--fresh` for new benchmark comparisons: SparkRun fingerprints declared mod references, not their contents. The README and recipe record deliberate site choices; there is no second parser mirroring all upstream launcher defaults.
