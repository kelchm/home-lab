# Mia GLM-5.3-Flash through SparkRun

Two DGX Sparks serve `GLM-5.3-Flash-EXL3` at `http://10.32.21.31:8888/v1`. SparkRun 0.3.9 owns model/image distribution, fabric detection, containers, runtime caches, readiness, warmup, logs, stop, and standard benchmarking. These hosts are outside Flux. Vonk, Qwen and DeepSeek remain stopped.

## Deploy

On the workstation, copy this directory to the head; on a fresh control host install SparkRun 0.3.9 and uv (the benchmark runner needs `uvx` on PATH). Keep the relative mod directory beside the recipe. The worker needs no Mia source checkout.

```sh
rsync -a --exclude evidence --exclude __pycache__ sparks/inference/sparkrun/ kelchm@10.32.21.31:sparkrun/recipes/glm53/
ssh kelchm@10.32.21.31
export PATH="$HOME/.local/bin:$PATH"
cd ~/sparkrun/recipes/glm53
python3 mods/mia-glm53/prepare.py
sparkrun run ./mia-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
# Stop the current workload before running the candidate on these same GPUs.
sparkrun run ./mia-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
```

The saved `sparks` cluster uses head `10.32.21.31`, worker `10.32.21.32`, SSH user `kelchm`, cache `/opt/spark-cache/huggingface`, greedy placement, management-network transfers, and `transfer_mode: auto`. Set the latter with `sparkrun cluster update sparks --transfer-mode auto`. SparkRun downloads missing pinned models into its cache and synchronizes them to the worker; populated caches are reused. Initial provisioning can transfer about 164 GiB over management Ethernet. The optional `pull` path in 0.3.9 fails at image distribution with an unexpected `ssh_options` argument, so it is not used. SparkRun still detects both CX7 rails for inference. On fresh hosts create the cache directory owned by the SSH user and ensure Docker and SSH access first. SparkRun clears host page cache after distribution; its sudo cache-clear operation must be available (use `sparkrun setup clear-cache --cluster sparks --save-sudo` if it reports a permissions failure).

`prepare.py` fetches only the reviewed upstream files, verifies every checksum in `upstream.lock.json`, and materializes an ignored `upstream/` directory. It does not launch anything or manage weights. SparkRun's native `mods` mechanism copies that bundle into both containers; `run.sh` verifies it again and applies Mia's ordered patches. A missing or modified file fails before serving. The pinned image supplies compiled kernels. No custom loader, service supervisor, or Docker launcher is maintained here.

For an upstream update, review the image, launcher, runtime files and tests together; update the source revision and file hashes in the lock, update recipe metadata, and rerun parity and live acceptance. Do not update the lock merely to silence a checksum failure. Stop/relaunch after mod updates and use fresh benchmark runs: SparkRun fingerprints the declared mod reference, not its file contents. `check_upstream.py /path/to/pinned/mia` (PyYAML) checks upstream defaults, patch ordering and deliberate site settings.

For a fresh head (after Docker, SSH and cache-directory provisioning), the equivalent tool/cluster setup is:

```sh
python3 -m venv ~/.local/share/sparkrun-venv
~/.local/share/sparkrun-venv/bin/pip install sparkrun==0.3.9 uv==0.12.6
export PATH="$HOME/.local/share/sparkrun-venv/bin:$PATH"
sparkrun cluster create sparks --hosts 10.32.21.31,10.32.21.32 --user kelchm --cache-dir /opt/spark-cache/huggingface --transfer-mode auto --transfer-interface mgmt --scheduler greedy
```

## Serving profile and memory

[Recipe](mia-glm53-exl3.yaml): InstantTensor 0.2.0, EXL3/TR3 target, TP2, DFlash2 k=7 with draft TP2, 850,000 context, four sequences, 7,168-token batches, FP8 KV, E3 grouped prefill, CUDA graphs enabled, `fair` mixed-prefill scheduling, and abliteration disabled. Image, target and draft are immutable pins. The current Mia source pin has identical runtime files to the previous `ca855766` pin; its intervening change is launcher override reporting.

The 2 GiB `INSTANTTENSOR_BUFFER_SIZE` replaces the installed loader's 4 GiB TP2 default I/O buffer. InstantTensor automatically reduces I/O depth to fit; the largest target tensor is 1,268,776,960 bytes. This is a loading allocation, not a promise of 2 GiB lower steady-state RAM. The separate 14 GiB `kv_cache_memory_bytes` setting bounds the serving KV pool instead of allowing `.85` utilization to consume reclaimed memory. With an explicit KV size vLLM uses that size rather than deriving the pool from utilization. Eight NCCL channels, right-sized indexer workspace, 1 GiB multimodal processor cache, and graph estimation disabled remain; graph execution is enabled. The latest [evaluation](evidence/20260919-integration/README.md) records the measurements and limits.

SparkRun’s fit estimate does not account for the full hybrid cache state; vLLM’s actual allocation and host telemetry determine fit. The synthetic 850k-budget request completed correctly, with five KV preemptions/replays; this is not an uninterrupted-prefill guarantee. 850k is a per-request ceiling, not four guaranteed simultaneous 850k requests. The hybrid cache also has much smaller idle prefix retention than its advertised equivalent-token capacity. Multimodal configuration remains upstream's 48 images/1 video with 2048 tokens/image; configured maxima are not a workload qualification.

## Operate and validate

On the head, from `~/sparkrun/recipes/glm53`:

```sh
sparkrun status --cluster sparks
sparkrun logs ./mia-glm53-exl3.yaml --cluster sparks
sparkrun stop ./mia-glm53-exl3.yaml --cluster sparks
# Against an already running instance; never submit to Spark Arena implicitly.
sparkrun benchmark ./mia-glm53-exl3.yaml --cluster sparks --skip-run --no-stop --fresh --profile ./benchmark.yaml --output /tmp/glm53-benchmark.yaml
python3 verify.py --url http://127.0.0.1:8888 --out /tmp/glm53-api.json
python3 mixed_workload.py --url http://127.0.0.1:8888 --out /tmp/glm53-mixed.json
# Optional expensive boundary qualification; run alone, after the bounded checks.
python3 long_context.py --url http://127.0.0.1:8888 --out /tmp/glm53-long.json
```

Run the profile on the head; its tokenizer path follows the saved cluster cache directory (adjust it if that cache moves). The native benchmark sweeps 2k/32k prompts at C1/C4 with the tokenizer from the pinned native cache snapshot, warmups and two measured repetitions. The complementary SSE probe submits a short newcomer and a roughly 36k prompt during an existing long decode, records TTFT/delivery gaps, and requires short-newcomer content before the original finishes. Its short-request threshold is five seconds; the longer prefill may finish later and its timings are reported without an artificial latency pass threshold. It is a behavioral regression check, not a benchmark framework.

`run --no-follow` still waits for readiness and upstream shape warmup because the recipe has `post_exec`. A failed hook makes the command fail but does not automatically stop the server; inspect logs and stop the failed candidate before rollback. Containers do not automatically restart after a host reboot. Start again with SparkRun after both hosts are ready. Persistent caches are managed by SparkRun under the head/worker user's cache directory.

The endpoint is unauthenticated on the trusted LAN. The existing, nonpersistent INPUT exception permits workstation `10.32.10.244/32` to head port 8888 via `enP7s7`; retained Vonk firewall rules remain. Check access after a host reboot or workstation address change. This work does not introduce another firewall manager.

## Rollback and retained data

Before replacing a working recipe, retain its exact YAML, mod bundle and logs under `~/sparkrun/receipts/`. Stop the candidate with its own recipe path, then run the retained recipe through SparkRun. Do not run two workloads on these GPUs.

The permanent deployment path is `~/sparkrun/recipes/glm53/mia-glm53-exl3.yaml`; the old top-level deployed YAML was archived to avoid leaving two apparently current launch recipes. For this transition the original working recipe is retained at `~/sparkrun/receipts/integration-20260919/baseline.yaml`, with its original Mia checkout and `/opt/sparkrun/models/` paths on both hosts. Stop the current recipe, then run that baseline with the same cluster and launch flags. Its recipe fingerprint differs from the new recipe; stop/status/log commands must address the appropriate recipe. `cluster-before.yaml` records the old transfer configuration if needed.

A one-time hash-verified import populated standard HF `hub/models--…/blobs` and pinned snapshot symlinks from the retained models, without redownloading their weights. These blobs are hardlinked to the old immutable files: do not edit them in place. SparkRun normalized cache ownership during native synchronization, which also changes ownership of those retained links; the root-run baseline remains readable. Fresh provisioning uses SparkRun's native downloads; the import is not required. Removing one link does not remove the retained weight data. Historical pre-integration results are isolated under [baseline evidence](evidence/20260919-baseline/).
