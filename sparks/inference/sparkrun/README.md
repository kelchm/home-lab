# Mia GLM-5.3-Flash through SparkRun

Two DGX Sparks serve `GLM-5.3-Flash-EXL3` at `http://10.32.21.31:8888/v1`. SparkRun 0.3.9 owns model/image distribution, fabric detection, containers, runtime caches, readiness, warmup, logs, stop, and benchmarking. These hosts are outside Flux. Vonk, Qwen and DeepSeek remain stopped.

## Deploy

The saved `sparks` cluster uses head `10.32.21.31`, worker `10.32.21.32`, SSH user `kelchm`, cache `/opt/spark-cache/huggingface`, greedy placement, and management-network transfers. Use `transfer_mode: auto`: the optional `pull` path in SparkRun 0.3.9 fails at image distribution with an unexpected `ssh_options` argument. SparkRun detects both CX7 rails for inference.

Copy this directory to the head, keeping the mod beside the recipe, then prepare the pinned source bundle:

```sh
rsync -a --exclude __pycache__ sparks/inference/sparkrun/ kelchm@10.32.21.31:sparkrun/recipes/glm53/
ssh kelchm@10.32.21.31
export PATH="$HOME/.local/bin:$PATH"
cd ~/sparkrun/recipes/glm53
python3 mods/mia-glm53/prepare.py
sparkrun run ./mia-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
# Stop the current workload with its own recipe before launching on these GPUs.
sparkrun run ./mia-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
```

SparkRun downloads the pinned target and draft snapshots into its standard HF cache and synchronizes them to the worker. Existing snapshots are reused; initial provisioning can transfer about 164 GiB. The worker needs no Mia checkout or manually staged models. SparkRun clears page cache after distribution; if its sudo operation is unavailable, use `sparkrun setup clear-cache --cluster sparks --save-sudo`.

For a fresh control host, first provision Docker/SSH access and the cache directory on both hosts, owned by the SSH user. Install the control tools and create the cluster:

```sh
python3 -m venv ~/.local/share/sparkrun-venv
~/.local/share/sparkrun-venv/bin/pip install sparkrun==0.3.9 uv==0.12.6
export PATH="$HOME/.local/share/sparkrun-venv/bin:$PATH"
sparkrun cluster create sparks --hosts 10.32.21.31,10.32.21.32 --user kelchm --cache-dir /opt/spark-cache/huggingface --transfer-mode auto --transfer-interface mgmt --scheduler greedy
```

Keep the venv on PATH for subsequent commands; the existing head instead exposes these tools through `~/.local/bin`. Benchmarks need `uvx` on PATH.

## Operate and roll back

From `~/sparkrun/recipes/glm53` on the head:

```sh
sparkrun status --cluster sparks
sparkrun logs ./mia-glm53-exl3.yaml --cluster sparks
# When stopping or replacing the workload:
sparkrun stop ./mia-glm53-exl3.yaml --cluster sparks
```

`run --no-follow` waits for readiness and upstream shape warmup because the recipe has `post_exec`. A failed hook makes the command fail but does not automatically stop the server; inspect logs and stop the failed candidate before rollback. Containers require an explicit SparkRun launch after a host reboot. Persistent runtime caches are managed by SparkRun.

Before an upgrade, retain the exact working recipe and prepared mod bundle outside the checkout. Stop the candidate with its own recipe, then launch the retained recipe with the same cluster and launch flags. Recipe fingerprints can differ, so use the corresponding recipe for stop and logs. Never run two inference stacks on these GPUs; the legacy Qwen Taskfile has a SparkRun interlock.

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

The [throughput profile](benchmarks/throughput.yaml) uses native llama-benchy for 2k/32k prompts at C1/C4, with warmup and two measured repetitions. Run it on the head: its tokenizer path points to the pinned HF snapshot under the cluster cache. Adjust that path if the cache moves; an unreadable tokenizer can silently fall back to GPT-2 and invalidate comparisons.

Tool evaluation uses the upstream `tool-eval-bench` v1.8.0 CLI directly for 15 core scenarios. SparkRun 0.3.9's tool integration passes the model repository ID instead of the served alias, ignores a model argument override, and shell-quotes JSON arguments before direct subprocess execution. The direct command avoids those integration bugs without a wrapper or installed patch. Inspect `scores.scenario_results` and the native conversation report, not just the exit status: completion does not mean the scenarios passed. No results are submitted to Spark Arena.

The [acceptance gate](tests/acceptance.py) checks five-image input and the known scheduler regression: a correct short reply must begin within five seconds while an earlier decode is still active. It exits nonzero on failure. For deeper mixed-prefill diagnosis, use Mia's [upstream probe at the locked revision](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks/blob/11f6cd45d39d860e290362e22fb5df9ef42196e2/tests/test_mixed_prefill_decode.py) on the head; it measures decode overlap and newcomer latency but does not enforce the short-arrival gate. Benchmark output and experimental receipts are disposable working data, not tracked repository content.

## Settings and limits

The [recipe](mia-glm53-exl3.yaml) keeps InstantTensor, EXL3/TR3 target TP2, DFlash2 k=7 with draft TP2, 850,000 context, four sequences, 7,168-token batches, FP8 KV, CUDA graphs, fair mixed-prefill scheduling, and abliteration disabled. Image and model revisions are immutable pins. Source and image are pinned separately; the source revision is not a claim about image build provenance.

`INSTANTTENSOR_BUFFER_SIZE=2147483648` caps loader I/O staging at 2 GiB instead of the installed InstantTensor 0.2.0 TP2 default of 4 GiB. The library automatically reduces I/O depth; the largest target tensor is about 1.18 GiB. This is a loading bound, not a promise of 2 GiB lower steady-state RAM. The explicit 14 GiB KV pool governs serving allocation instead of the `.85` utilization setting. Eight NCCL channels, right-sized indexer workspace, a 1 GiB multimodal processor cache and graph estimation disabled remain; graph execution stays enabled.

SparkRun's fit estimate does not account for the full hybrid cache state; use vLLM allocation and host telemetry to judge fit. The 850k allocation/retrieval test completed with cache preemptions and replay, not uninterrupted prefill. 850k is a per-request ceiling, not a promise of four simultaneous 850k requests or responsive long-prefill latency. Idle prefix retention is much smaller than equivalent-token capacity. The configured 48-image/1-video maximum, simultaneous maximum-context workloads, general long-context quality and reboot recovery have not been qualified.

## Update the upstream bundle

`mods/mia-glm53/prepare.py` fetches the reviewed files in `upstream.lock.json`, verifies their checksums, and materializes an ignored `upstream/` directory. SparkRun copies the mod to both ranks; `run.sh` verifies the bundle and applies the locked patch sequence. Missing/changed files, duplicate runtime basenames and inconsistent patch entries fail verification. The pinned image supplies compiled kernels.

For an upstream update, review Mia's launcher, runtime files and relevant tests together. Update the source revision, selected-file hashes and patch order in the lock, plus the recipe's source metadata and image/model pins as needed. Preserve the license files in the bundle. Check new tensor sizes before retaining the loader buffer cap. Re-prepare, run the dry run, then validate the deployed candidate. Do not update checksums merely to silence a failure.

Stop/relaunch after mod changes and use `--fresh` for new benchmark comparisons: SparkRun fingerprints declared mod references, not their contents. The README and recipe record deliberate site choices; there is no second parser mirroring all upstream launcher defaults.
