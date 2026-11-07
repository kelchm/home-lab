# Native GLM release trial and rollback

The owner authorized the trial NOW on November 7 after confirming GLM is idle: capture the Python comparison first, launch native through the existing home-lab SparkRun recipe/mod/plugin/rsync flow, run matching measurements and qualification, then ask whether to keep native running or restore Python. Stop and report any unexpected launch, readiness, rail, memory-guard or thermal event. This authorization supersedes the earlier automatic rollback proposal.

## Selected inputs

Use [glm53-mia-2026.11.07-agent-1m](../../sparkrun/mia-tensorfold-glm53-native-tp2/glm53-mia-2026.11.07-agent-1m.yaml) and its frozen [native launcher mod](../../sparkrun/mods/mia-tensorfold-glm53-13f2464e/). The existing `tensorfold` plugin and `tensorfold-readiness-v2` stay unchanged. The mod contains a copy of the published release manifest and checksums; it installs the rank launcher/template/capacity check, with no engine patches or binary replacement. The image is pulled on the head by its published digest; SparkRun distributes it to the worker.

[spark-inference #3](https://github.com/kelchm/spark-inference/pull/3) stays draft for a later, separately reviewed migration. Nothing from that PR is fetched or installed in the launch path. Its qualification client can run off-host as a measurement tool; record its exact code hashes. No generic release materializer is used for this trial.

The image is `ghcr.io/kelchm/spark-inference/glm53-mia@sha256:0956a18406597abc252564b6f23e7f205dc35c5eac5b475ea6c0c229d10d4d9c`, config ID `sha256:ab992b177ec576277720a8acf71d36ed094f56b724d9acb8aebc0d83afad2a34`. Engine `e52eb42b52b1e50f3f23a5eae8115bbe80c5e7d9` is Mia's 110 patches at kit `13f2464` on TensorFold `f350cd3e`. Target/drafter revisions are unchanged from Python.

The profile selects four streams, 1M total context, 32K output allowance, FP8 pages, KV sharding, cache-first with 6 GiB requested cache, preserved thinking/xhigh, top-k 20, q8 dense/head, a 12 GiB reserve and images. Video is absent. Learning uses a separate 64 GiB store with a 50 GiB free floor and 128 GiB daily write budget. Both ranks' learned parts live on the head's NVMe. The engine creates a 16-hex identity child beneath the configured `GLM_LEARN_DIR`; it logs that child, not the bare root. Never reuse Python's spill directory.

## Baseline before the switch

Retain raw requests, SSE replies, body hashes, client settings and versions, host/container/image identity, health/metrics, server logs and independent telemetry outside Git. Run identical arm-independent bodies for one- and four-stream bounded decode; cold prefill at several lengths and repeats; exact reasoning/call-ID replay through a Pi-style tool loop; and ordinary divergent image histories. Explicit bounded decode may end at its output limit; label that as a speed probe, not a completed quality task. Use the same thinking/sampling/output settings on native. Record rendered token counts rather than assuming equal bytes imply equal templates.

Record Python's actual pool/free rows, spill capacity, pending writes, failures and observed read/write counters. Use normal bounded requests for safe durability observations. Do not crash/restart production, clear its cache, modify its quota or overwrite its spill to manufacture a baseline. An in-RAM repeat alone does not prove disk restoration. The rollback input remains [Python durable-readiness-v2](../../sparkrun/mia-tensorfold-glm53-exl3-tp2/glm53-exl3-tp2-mia-tf-bf5f9452-mia-1m-durable-readiness-v2.yaml).

## Preflight and existing launch flow

Verify current Python identity/idle counters, both ranks, existing controller/plugin, exact cached HF snapshots, API access and free disk. Retain the running Python recipe/mod/readiness/plugin/config and job/container receipts under `~/sparkrun/rollback/` before stopping it; keep the 0.3.10 fallback intact. Run the frozen checks and dry-run the retained rollback with the absolute 0.4.2 controller. Job metadata is evidence, not a file to restore over live containers.

Require both RoCE rails and 60 seconds of stable management links using [Before a launch](../../sparkrun/README.md#before-a-launch). Inspect the SDK gate port and preceding NCCL port. The launcher maps `NCCL_COMM_ID` to fabric address plus `MASTER_PORT-1`, so its watch line opens on the SDK gate before rank 1 is dispatched. Sample both hosts independently throughout: memory, swap, links, GPU/temperature and new kernel warnings. A lost sampler is a stop condition.

Stage only the new recipe directory and native mod into the existing tree; do not overwrite the plugin, Python mod, readiness-v2 or old profiles:

```sh
rsync -a sparks/inference/sparkrun/mia-tensorfold-glm53-native-tp2/ kelchm@10.32.21.31:~/sparkrun/recipes/glm53/mia-tensorfold-glm53-native-tp2/
rsync -a sparks/inference/sparkrun/mods/mia-tensorfold-glm53-13f2464e/ kelchm@10.32.21.31:~/sparkrun/recipes/glm53/mods/mia-tensorfold-glm53-13f2464e/
```

Run the staged mod's `prepare.py --check`. Flush Python by its [rank-0-only procedure](../../sparkrun/README.md#launch-stop-and-roll-back), require both flush receipts, then stop its exact recipe. Verify both old ranks are absent and both GPUs idle before pulling or launching native. A generic SparkRun/container stop does not flush Python.

On the head, using the existing controller/config:

```sh
sparkrun="$HOME/.local/share/sparkrun-042/bin/sparkrun"
candidate="$HOME/sparkrun/recipes/glm53/mia-tensorfold-glm53-native-tp2/glm53-mia-2026.11.07-agent-1m.yaml"
docker pull ghcr.io/kelchm/spark-inference/glm53-mia@sha256:0956a18406597abc252564b6f23e7f205dc35c5eac5b475ea6c0c229d10d4d9c
"$sparkrun" run "$candidate" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
"$sparkrun" run "$candidate" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
```

Never use `stop --dry-run`. Record both native container IDs, manifest/config identities and startup logs. A failed hook leaves containers running: stop and report; do not retry with smaller context or altered settings. Admission requires the historical usable 2,582,528-token aggregate floor, a full 1M plus verification room in each shard part, funded device cache of at least 2 GiB, no pinned host tier, and the expected learned identity child/quota. These are smoke gates, not proof of durability.

## Qualification and comparison

Repeat the baseline requests, client and settings. Keep per-stream and aggregate throughput, TTFT, measured counts, cache receipts and content/finish outcomes separate. Count differences from templates and arithmetic are observations, not a reason to alter one arm. A single batch is an exploratory comparison, not a statistical performance claim.

Use the off-host qualification client with the local home-lab recipe and copied release manifest. It performs requests and records evidence only; the operator owns lifecycle actions. Run at least a 300K measured first save and wait for its published-write receipt before another turn, then grow toward 1M with output/continuation room. Require correct planted facts and affirmative disk bytes/tokens for restart and eviction reuse. A cold prefill is not durable restoration. Record staging/cache admission refusals explicitly.

Exercise divergent image histories with identical text/geometry and changed pixels before/after restart. Correct durable image conversations remain non-negotiable in [#1285](https://github.com/kelchm/home-lab/issues/1285); expected unsupported persistence fails that production gate. Do not reintroduce dropped video, fixed 50-image budgets, CMYK or body-size requirements.

For a clean native restart, first wait for acknowledged writes and idle requests. Identify the exact rank-0 native PID by its `/proc/<pid>/cmdline` beginning `/opt/tensorfold/native/bin/tensorfold-native`, `serve`; validate the recorded candidate container/image and signal that PID with TERM. Do not signal the container's `sleep infinity`, an unverified `/tmp/sparkrun_serve.pid`, or rank 1. Require rank-0 `shutting down (SIGTERM)`, `shutting down: drained`, `shutting down: stopped`, both native processes gone, and both GPUs idle before SparkRun container cleanup and the same-profile relaunch. Missing evidence cannot be labeled a clean restart.

For the separate process-crash probe, preserve an older published checkpoint, observe and retain the identity/timing of a new `.part` write, then KILL only the recorded native rank-0 container. If no active write is observed, report mid-write recovery unknown. Verify peer termination, clean up only candidate containers, then relaunch the identical profile/store and require restoration of the older point. File fsync/rename/checksums do not establish power-loss safety; directory-fsync is not demonstrated.

Use the separate `glm53-mia-2026.11.07-quota-probe.yaml` and its isolated 1 GiB store to exercise cap pressure; stop the main native run and verify both GPUs idle before this variant. Preserve its receipts and return to the main profile only with an intentional verified launch. Do not fill the host disk or inject real exhaustion. Record the smaller-quota test as a mechanism probe, not a measured fill of 64 GiB.

Stop new tests and report any unexpected launch/readiness/rail failure, rank exit, memory guard, thermal warning, identity mismatch, incorrect restored content or loss of telemetry/control. Do not reboot hosts, restart Docker/drivers/exporters, prune stores or change engine code as part of recovery.

## End-of-test decision

Record the side-by-side results and separate capacity, text durability, image persistence, quota and recovery verdicts in [#1280](https://github.com/kelchm/home-lab/issues/1280). Ask the owner whether to keep native running or restore Python; do not choose automatically. If restoration is selected, stop native cleanly as above, require both GPUs idle, launch the unchanged retained Python recipe with its original controller/config/mod/spill, and verify full-pool/active-spill readiness plus a Pi tool turn. Keep all stores as evidence.

The release stays qualification-pending until an actual result is recorded. Later update its qualification in spark-inference per `AGENTS.md`, with a durable receipt link/date. Automated client success leaves manual capacity, verified mid-write interruption, quota pressure, real Pi behavior and service-selection decisions for review.
