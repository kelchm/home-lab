# Native GLM release trial and rollback

Status: off-host preparation only. The user approved preparing an end-to-end trial but requires approval of a specific operating window before any Spark contact, including read-only inspection, staging or dry runs. Nothing in this preparation has contacted either Spark, stopped Python, loaded a model or exercised a GPU. This procedure is intended behavior, not a qualification receipt.

The target is `glm53-mia-2026.11.07-agent-1m`, using [the first spark-inference release](https://github.com/kelchm/spark-inference/blob/main/recipes/glm53-mia/releases/glm53-mia-2026.11.07.json). `deployment.json` pins the exact external runtime/profile inputs and the image by digest; `preparation.json` retains the off-host image-attestation result. Reusable launch/readiness/qualification code lives in spark-inference. The published image has Mia's 110 patches and no engine changes of ours.

This is a temporary trial with rollback to the current Python durable-readiness-v2 profile at the end. Passing text tests does not authorize production promotion. Correct durable image conversations remain a non-negotiable gate in [#1285](https://github.com/kelchm/home-lab/issues/1285), expected to fail on this base. Qualification remains pending until the actual observations are recorded.

The retained rollback input is [the Python durable-readiness-v2 recipe](../../sparkrun/mia-tensorfold-glm53-exl3-tp2/glm53-exl3-tp2-mia-tf-bf5f9452-mia-1m-durable-readiness-v2.yaml). Its local preparation hash is recorded in `preparation.json`; verify that it still matches the running workload during the approved preflight. A local recipe hash is not evidence of live host identity.

## Prepare without contacting hosts

From this checkout, materialize and verify the pinned release into a new local directory:

```sh
python3 sparks/inference/releases.py materialize \
  --lock sparks/inference/deployments/glm53-mia-2026.11.07-agent-1m/deployment.json \
  --directory /tmp/glm53-mia-2026.11.07-trial
python3 sparks/inference/releases.py check \
  --lock sparks/inference/deployments/glm53-mia-2026.11.07-agent-1m/deployment.json \
  --directory /tmp/glm53-mia-2026.11.07-trial
gh attestation verify \
  oci://ghcr.io/kelchm/spark-inference/glm53-mia@sha256:0956a18406597abc252564b6f23e7f205dc35c5eac5b475ea6c0c229d10d4d9c \
  --repo kelchm/spark-inference
```

The materializer downloads only commit-qualified GitHub files, verifies every hash and image/release agreement, and refuses existing output directories. It never uses a moving registry or operates Docker/SSH/an endpoint. `--source-tree <local checkout>` supports offline verification against the same hashes. The external profile resolves its own adjacent mod; it cannot silently pick up a different registry mod. Do not edit generated/pinned values by hand.

Prepare the deterministic campaign offline from the materialized directory. Keep it outside Git; its manifest binds the exact profile, release, fixture seed and harness bytes. Preparing does not contact an endpoint:

```sh
cd /tmp/glm53-mia-2026.11.07-trial
python3 -m qualification prepare \
  --out /tmp/glm53-mia-2026.11.07-campaign \
  --release recipes/glm53-mia/releases/glm53-mia-2026.11.07.json \
  --profile recipes/glm53-mia/sparkrun/profiles/glm53-mia-2026.11.07-agent-1m.yaml
```

The campaign deliberately requests low effort with an 8,192-token reply cap for bounded retrieval probes; the serving profile remains thinking-on/xhigh by default. Record a separate real Pi/default-effort turn. The client fits the final history toward the 1M window with a recorded continuation margin; it refuses a request above its measured ceiling. Any harness change requires a new campaign.

## Window and stop ownership

Request a 2.5-hour window with a named start/end in the owner's timezone. Reserve the final 30 minutes for rollback and acceptance. The acting operator owns both ranks throughout the trial and must stop new test requests early enough to restore Python before the approved end. Ask for an extension if a test cannot finish; elapsed time never authorizes one. Expected scope is preflight/staging, one native service at a time, bounded text/image/Pi probes, native restart/eviction/crash/quota experiments, and restoration of Python. It excludes host reboot, Docker restart, driver reload, cache pruning or changes to unrelated services.

Begin only after the user approves that specific window. The preparation PRs can be reviewed independently; merging them does not authorize host operations.

## Approved-window preflight and staging

1. Confirm current service identity, exact running Python recipe, controller, both container IDs, health/idle counters and Pi clients. Stop incoming client work by agreement; do not kill an active user conversation. Verify the runtime adapter matches the pinned external copy (`028dbc5838ecc706f21fc2c110edc1490d7f504c9892b76477353483269ba5ab`) before relying on its TP2 contract.
2. Capture rollback inputs and receipts under a new timestamped result directory on the head: current recipe/mod/plugin hashes, image identity, job/container records, controller version, host configuration and the preserved Python spill path. Keep the existing files and store intact. Preserve the existing 0.3.10 controller rollback too; never restore job metadata over live containers.
3. Stage this deployment directory and `releases.py` under a new `~/sparkrun/deployments/` location. Materialize the external source with the same lock on the head under a new `~/sparkrun/releases/` directory. Do not rsync-delete or overwrite `~/sparkrun/recipes/glm53/` or Python's frozen mods.
4. Verify both exact HF revisions are already available to the controller and each host. They are the existing target `078455ffe6472f9a52fbc1139f58b9db2881b25c` and DFlash2 `bf582e4eacc1810f76656d1811693ff6c6737d2a`; do not begin the outage with an unexpected checkpoint download or duplicate copy.
5. Verify free disk and the dedicated native store location. The native store is `/opt/spark-cache/huggingface/spark-inference/learn/glm53-mia-2026.11.07-agent-1m` on the head (container `/cache/huggingface/...`). It contains both ranks' parts; KV sharding does not create a worker disk store. The profile selects 64 GiB capacity, a 50 GiB minimum free floor and 128 GiB daily writes. Verify at least quota plus floor before starting the bounded campaign. Python's `/opt/spark-cache/huggingface/tensorfold-spill` is never reused, renamed or removed.
6. Validate the head/worker management and both RDMA links and the 60-second stable-link gate from [Before a launch](../../sparkrun/README.md#before-a-launch). Inspect both the selected SparkRun gate port and its preceding NCCL port for collisions. Do not change HCA/GID order or network settings to make the trial pass.

Save raw logs and telemetry outside Git, with SHA-256 identities referenced by the final receipt. Observe both ranks independently: available memory including parked pages, swap/reclaim pressure, GPU processes/utilization, filesystem free space and learning bytes. The launcher's local memory guard is not a replacement for independent stop ownership.

## Switch and native startup

Use the absolute 0.4.2 controller `~/.local/share/sparkrun-042/bin/sparkrun`. First validate the current Python frozen inputs, then follow its [clean flush and stop procedure](../../sparkrun/README.md#launch-stop-and-roll-back), signaling rank 0 only and requiring both flush receipts before SparkRun removes the containers. A generic `sparkrun stop` or `docker stop` alone is not a Python flush. If flush fails, stop and resolve it rather than moving to native.

After both old ranks are absent and both GPUs are genuinely idle, pull the public image on the head by digest:

```sh
docker pull ghcr.io/kelchm/spark-inference/glm53-mia@sha256:0956a18406597abc252564b6f23e7f205dc35c5eac5b475ea6c0c229d10d4d9c
```

Check RepoDigests and labels for release recipe commit `23f17a3d56bc034de3b9b555948f20c58bbd823d`, engine `e52eb42b52b1e50f3f23a5eae8115bbe80c5e7d9`, lane and kit. The manifest digest differs from Docker's config image ID; capture both. SparkRun distributes the image to the worker. No workstation `docker save` or image stream over SSH is needed.

Set `candidate` to the absolute profile path printed by `releases.py check`, then preview and launch:

```sh
sparkrun="$HOME/.local/share/sparkrun-042/bin/sparkrun"
"$sparkrun" run "$candidate" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow --dry-run
"$sparkrun" run "$candidate" --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
```

These commands belong inside the approved window. Never use `stop --dry-run`; retained SparkRun versions can actually stop a workload. A failed post-launch hook requires an explicit stop of the candidate and a check of both ranks before another launch or rollback.

The profile does not retry at a smaller context. It explicitly selects four streams, 1M total context, 32K output allowance, FP8 pages, KV sharding, cache-first with 6 GiB requested cache, preserved thinking/xhigh, top-k 20, q8 dense/head and images on. The q8 head follows Mia's reported pair rather than her generic BF16 default. Video is absent from the image; do not send videos or claim clean video refusal semantics. The unmodified model discovery advertises `max,high,low`, not the retired site's `none` metadata fix.

Save both startup logs. Admission requires the historical 2,582,528-token aggregate floor, at least a full 1M request plus verification room in each sharded part, a funded cache of at least 2 GiB and a correctly opened 64 GiB store with both ranks' parts. These are smoke floors. Record actual funded cache and `/health.kv_shard` per-part pages; neither configured context nor Mia's 3.15M result proves our capacity or durable large-save support.

## Bounded qualification sequence

Use the materialized release's `qualification/README.md` and client, retaining exact fixture, profile, source, image, request and response hashes. Endpoint-bearing commands only run in this approved window. The client does not perform lifecycle operations; those stay under this procedure.

1. **Readiness and Pi loop.** Require the profile's health/discovery, four concurrent answers, tool and effort-render gates. Capture a real Pi tool loop using its OpenAI provider with unchanged `reasoning_content` and original call IDs. Record reported prompt/cache counts and corroborating engine cache receipts. Prompt accounting is a known follow-up; counts alone do not prove reuse.
2. **Large first save.** Start a new seeded text history whose actual rendered count is at least 300K tokens. Keep a bounded reply and the configured output reservation. Wait for its asynchronous published-write receipt before advancing to the next turn. Require correct planted facts and an affirmative receipt covering that prefix; a later large save, small tail save or warm in-RAM answer does not pass. Record skips such as staging/RAM-cache admission, quota, free floor or daily budget explicitly.
3. **Growth and eviction.** Grow the same history through measured token counts toward the 1M total limit, reserving output and verification room. Check oldest/middle/newest facts. Use unrelated, bounded synthetic sessions to pressure retained states while monitoring memory, until the target is evicted. Do not manufacture pressure with host allocations or fill the host disk. Returning to the target requires actual disk-read bytes/tokens and a correct continuation, not a full cold prefill.
4. **Clean restart.** Record the published state and current container IDs, stop only the native candidate, verify both ranks/GPU release, then launch the exact same image/profile/store. Record new IDs and actual disk restoration before validating the same continuation. An image/config/store identity change invalidates the comparison.
5. **Process crash.** First retain an acknowledged older point. For a separate synthetic pending save, observe a new `.part` write inside the dedicated native root and capture its identity/timing. Validate the exact native rank-0 container ID and image, then send KILL only to that recorded container. Never use a broad `pkill` or signal the Python profile. If no write is observed within the bounded wait, record the mid-write case as unknown; do not relabel an idle crash as a write interruption. Verify peer termination, explicitly stop any surviving candidate container, and restart the identical release. Require cleanup/refusal of the unfinished point and restoration of the older acknowledged point with correct bytes/results. This tests process-crash recovery, not power-loss durability; the implementation lacks directory-fsync evidence.
6. **Quota.** Observe head-store bytes and free space throughout. Use the same materialized release's generated `glm53-mia-2026.11.07-quota-probe.yaml` for a separate run: the image, sharding, cache, window and sampler stay identical, while its quota is 1 GiB and its dedicated store ends in `-quota-probe`. Stop the primary candidate and verify both GPUs idle before launching this variant. Do not hand-edit a pinned profile or fill NVMe to create ENOSPC. Require bounded stored usage, observed cap pressure/eviction or refusal, and healthy subsequent requests. Missing cap pressure remains unknown, never a pass. Record that this exercises the same quota mechanism at 1 GiB, not a measured fill of the normal 64 GiB store.
7. **Image-bearing histories.** Run the client's `image` and `image-fork` sessions before and after restart: their text, dimensions and positions are identical, but their pixels differ. Require the correct answer for each and positive durable-image write/read evidence. Leave over four seconds between small probes with the default two-second clock-skew allowance; a log read that could fit both requests is unknown and cannot prove reuse. Record exactly what this base does; the expected unsupported/unsafe persistence result fails the production gate even if text tests pass. Do not carry the dropped 50-image/fixed-token/video/CMYK/body-size requirements back into this trial.

Do not bypass staging/cache/part admission limits or change engine code in this campaign. Stop new probes on a rank failure, identity mismatch, incorrect restored content, persistent memory pressure, loss of control/telemetry or the rollback deadline. A known unsupported image-persistence result is a qualification finding, not permission to use another engine or continue serving it permanently.

## Roll back and record the result

Before the reserved rollback period, stop request generation, save native logs/health/metrics/store inventory and stop the candidate through its exact recipe. Confirm both native ranks are absent and both GPUs idle before relaunching the unchanged Python durable-readiness-v2 profile with its existing controller/config/mod/spill inputs. Require its normal full-pool and active-spill readiness, then validate a retained Python recovery conversation and a Pi tool turn. Confirm monitoring identifies Python again. Retain the native synthetic store as evidence; do not delete/prune it during rollback.

If GPU state or host control cannot recover through the documented ordinary stop, stop the campaign and ask for recovery authorization. This window does not authorize reboots, exporter/driver manipulation or unrelated service restarts.

Keep raw receipts outside Git and publish a durable report/link with hashes. Update [#1280](https://github.com/kelchm/home-lab/issues/1280) with text, image, capacity and recovery outcomes separately. In spark-inference, follow **Record qualification** in its AGENTS.md: set this release's qualification to qualified or rejected with the receipt link/date, run `tools/status.py`, and open a PR. No image-durability evidence means this release cannot be marked qualified for production. Its release manifest stays pending until an actual campaign result exists.

The client verdict covers its automated workload and disk probes only. Even a pass leaves production pending manual review of startup/four-stream capacity, an observed mid-write crash, quota pressure, actual Pi behavior and successful Python rollback. Lifecycle notes and image-digest artifacts do not by themselves prove that those operations occurred.
