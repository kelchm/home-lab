# Native GLM release trial and rollback

The owner authorized the trial NOW on November 7 after confirming GLM is idle: capture the Python comparison first, launch native through the existing home-lab SparkRun recipe/mod/plugin/rsync flow, run matching measurements and qualification, then ask whether to keep native running or restore Python. Stop and report any unexpected launch, readiness, rail, memory-guard or thermal event. This authorization supersedes the earlier automatic rollback proposal.

## November 7 result

**Rejected as the production replacement.** Native is healthy on the main `agent-1m` trial profile pending the owner's keep-native/restore-Python decision. Python was stopped only after both flush receipts; its original image, recipe, controller, mods and spill remain available. The measured [qualification receipt](qualification.json) binds the release, tested inputs and 509 raw evidence files retained off-host.

| Matched exploratory measurement | Python | Native |
| --- | ---: | ---: |
| One-stream end-to-end decode | 49.08 tok/s | 42.82 tok/s |
| Four-stream aggregate end-to-end decode | 99.57 tok/s | 80.93 tok/s |
| 111,919-token cold TTFT | 53.88 s | 62.02 s |
| Same prompt repeated, TTFT | 0.36 s | 1.13 s |
| Pi-shaped 10,456-token tool result, TTFT | 0.46 s | 1.49 s |
| Reported reusable tool-result tokens | 10,368 | 8,192 |
| Initial nominal live-pool positions | 2,582,528 | 3,900,416 |

This is one matched batch with the same client/settings and rendered counts, comparing the intended production profiles. Python uses q4 dense/FP8 head and native q8 dense/q8 head, so the speed difference does not isolate engine implementation. Pi-shaped histories replay each engine's own delivered reasoning and call IDs. Bounded decode intentionally reaches its output limit and is not a completed quality task.

All five retrieval checkpoints at 301,087, 450,822, 601,306, 801,258 and 990,494 prompt tokens answered correctly. The first four retained states in RAM, but their disk saves were skipped with `StagingFull`. A separate 110,592-token state had already hit the same limit. The near-window 987,136-token snapshot needed 6,264 MiB and was refused by the configured 6,144 MiB RAM cache. These are the decisive durability failures.

The initial pool was two ownership parts of 3,809 pages × 512 tokens, each 1,950,208 tokens; the final launch reported 3,812 pages per part. This exceeds Python's nominal pool floor. Temporary twins borrow unpromised pages in the other part during prefill and return afterward; the source has an owner-computed fallback if borrowing fails. These counts do not establish four simultaneously resident 1M histories or qualify the concurrent long-history admission frontier.

Published small text/tool states restored 4,096, 24,576 and 8,192 tokens with correct answers after restart and after a verified KILL during a new write. A small text state also restored from disk after observed RAM eviction. Both four-image variants, padded to 18,843 tokens, wrote and correctly restored 16,384-token prefixes after restart and process crash. This corrects the earlier source-only assumption that this kit could not persist images. It does not establish universal image-collision safety or power-loss safety. Short 406-token image and 223/252-token tool histories were not retained below the 4,096-row grid.

The separate 1 GiB quota probe wrote three independent states and evicted the two older disk checkpoints, retaining 565,349,820 bytes and the newest 24,576-token state. It did not fill the actual 64 GiB store. Actual Pi CLI 1.3.0, using an unchanged copy of the Spark provider and isolated settings, passed text and image `read` loops; each tool-result step reported 12,288 cached tokens, about 96% of its prompt. Full 32K generation, four growing long sessions, a long soak and an actual Python relaunch remain unperformed. The sealed automated campaign reports failure; its separate 600K evictor was not run after the established large-save failure. Manual receipts cover the smaller real eviction, crash, quota and Pi checks.

No observed memory guard, kernel, RoCE or thermal slowdown occurred. The owner reported network intrusion alerts from repeated short SSH polling connections; polling was stopped and remaining operations moved to one persistent ControlMaster connection per Spark with automatic fallback disabled. Native's `InferenceEngineBroken`/`InferenceEngineStalled` alert coverage is still missing because the dashboards use Python-only `tensorfold_health:*` metrics; that follow-up belongs to the dashboards work.

At the owner's request, `--dashboard` is added to the frozen launcher and staged on the head for the next planned native relaunch. The final trial process had already started without it: `/stats` and `/dashboard` remain 404 in this run. Its original launch inputs are retained separately from the newly staged lock. Verify both endpoints and live prefill counters after the next authorized native launch; no extra relaunch was performed for this flag.

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

Exercise divergent image histories with identical text/geometry and changed pixels before/after restart. Correct durable image conversations remain non-negotiable in [#1285](https://github.com/kelchm/home-lab/issues/1285). Measure the actual kit: the November 7 trial wrote and correctly restored two padded image variants, despite earlier source-only concerns. Use affirmative chat disk-read logs and correct pixels; `/imprint/resume` reported no image state even when the subsequent chat restored it. Short histories below the 4,096-row cache grid need separate treatment. Do not reintroduce dropped video, fixed 50-image budgets, CMYK or body-size requirements.

For a clean native restart, first wait for acknowledged writes and idle requests. Identify the exact rank-0 native PID by its `/proc/<pid>/cmdline` beginning `/opt/tensorfold/native/bin/tensorfold-native`, `serve`; validate the recorded candidate container/image and signal that PID with TERM. Do not signal the container's `sleep infinity`, an unverified `/tmp/sparkrun_serve.pid`, or rank 1. Require rank-0 `shutting down (SIGTERM)` and `shutting down: drained`, followed by either `shutting down: stopped` or the documented zero-status exit for idle HTTP connections. The latter branch runs after `engine.drain()` in the pinned engine's `server/serve.zig`; the trial observed it with two idle connections. Verify both native processes are gone and both GPUs idle before SparkRun container cleanup and the same-profile relaunch. Missing drain or termination evidence cannot be labeled a clean restart.

For the separate process-crash probe, preserve an older published checkpoint, observe and retain the identity/timing of a new `.part` write, then KILL only the recorded native rank-0 container. If no active write is observed, report mid-write recovery unknown. Verify peer termination, clean up only candidate containers, then relaunch the identical profile/store and require restoration of the older point. File fsync/rename/checksums do not establish power-loss safety; directory-fsync is not demonstrated.

Use the separate `glm53-mia-2026.11.07-quota-probe.yaml` and its isolated 1 GiB store to exercise cap pressure; stop the main native run and verify both GPUs idle before this variant. Preserve its receipts and return to the main profile only with an intentional verified launch. Do not fill the host disk or inject real exhaustion. Record the smaller-quota test as a mechanism probe, not a measured fill of 64 GiB.

Stop new tests and report any unexpected launch/readiness/rail failure, rank exit, memory guard, thermal warning, identity mismatch, incorrect restored content or loss of telemetry/control. Do not reboot hosts, restart Docker/drivers/exporters, prune stores or change engine code as part of recovery.

## End-of-test decision

Record the side-by-side results and separate capacity, text durability, image persistence, quota and recovery verdicts in [#1280](https://github.com/kelchm/home-lab/issues/1280). Ask the owner whether to keep native running or restore Python; do not choose automatically. If restoration is selected, stop native cleanly as above, require both GPUs idle, launch the unchanged retained Python recipe with its original controller/config/mod/spill, and verify full-pool/active-spill readiness plus a Pi tool turn. Keep all stores as evidence.

The release stays qualification-pending until an actual result is recorded. Later update its qualification in spark-inference per `AGENTS.md`, with a durable receipt link/date. Automated client success leaves manual capacity, verified mid-write interruption, quota pressure, real Pi behavior and service-selection decisions for review.
