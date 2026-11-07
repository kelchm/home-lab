# Off-host SparkRun native-launch compatibility

This focused suite runs the native GLM r2 recipe from the immutable repository input `/tmp/glm1104-final16-repository` (provenance label `144eed00-plus-final16`) against the existing SparkRun 0.3.10 and 0.4.2 installations. It supplies actual 0.3.10 launch/lifecycle compatibility evidence. It does not claim that the merged 0.4.2-specific GPU placement and `complete_post_launch` tests work on 0.3.10.

From the home-lab checkout, run:

```sh
/tmp/glm1104-sdk042-tests/bin/python -B \
  sparks/inference/sparkrun/mods/mia-tensorfold-glm53-1104-r2/tests/sdk_compatibility/run.py \
  --repo /tmp/glm1104-final16-repository \
  --repo-revision 144eed00-plus-final16 \
  --native-source /tmp/glm1104-video16b-locked-source
```

The runner uses `/tmp/glm1104-qualification-venv/bin/python` for 0.3.10 and `/tmp/glm1104-sdk042-tests/bin/python` for 0.4.2, with `-I -B` in each worker. It installs nothing and changes neither environment. `--repo`, `--repo-revision`, `--native-source`, `--python-0310`, `--python-042`, and `--output` can select explicit alternate inputs. The recorded native input is `/tmp/glm1104-video16b-locked-source`; supply it explicitly when reproducing this selected bundle. The revision label is supplied provenance; content is verified with SHA-256 without reading Git HEAD.

The checked-in [receipt](receipt.json) records nine passing tests under each SDK, source hashes, generated native argv/environment, scenario event order, and the source functions actually called. Running the command replaces that receipt.

## What executes

The installed SDK discovers the unchanged TensorFold plugin through `load_external_plugins`, accepts its API 1 declaration, and returns the registered runtime. The 0.3.10 loader ignores that declaration; the 0.4.2 loader enforces it. Launches call the real `RuntimePlugin.run`, adapter `_run_cluster`, `ClusterContext.build`, `run_native_cluster`, container-script generators, and `run_pre_serve_hooks`. Only external observations and execution are replaced with inert recorders. The recipe's mods are already represented by the source input; the high-level mod installation path is outside this suite.

The real `wait_for_port` calls its real `listen_probe_cmd` to read the head's socket tables on `MASTER_PORT=29511`. The recorder supplies a failed probe, a live-container observation, and then a successful probe. The SDK must start rank 0 before these probes and admit rank 1 only afterward. A separate case exhausts all 60 attempts while the container remains alive; another reports a dead head. Both return launch failure without starting rank 1. The generated probe must use `ss` with the exact port, retain the LISTEN-only `/proc/net/tcp` and `tcp6` fallback, and avoid a connecting readiness probe. The earlier collision scan's `nc` command is also recorded and must precede rank execution.

Each recorded rank command is translated by the actual r2 `serve.launch`. Both ranks must retain the recipe's environment and detected HCA/GID values, use the head's fabric IPv4 address, and set `NCCL_COMM_ID` to port 29510. Its port plus one must equal the SDK's gated port. The native input's hashed `tp_watch.zig` supplies the corresponding port-plus-one contract.

After launch returns, neither hook has run. The installed SDK then performs its actual endpoint readiness path: port polling followed by an HTTP readiness check. Only then does its real `run_post_exec` / `_run_exec_command` wrap and submit the recipe's commands. The recorder decodes the real wrapper and calls the actual `capacity.main` and shared `readiness.main` in process. Capacity reads a synthetic startup log through a container-file stub. Readiness reads synthetic HTTP responses through a strict `urlopen` recorder and performs discovery, four simultaneous smoke requests, a tool call, reasoning-level render checks, and final health. A barrier requires all four smoke requests to overlap.

Both SDKs must propagate a real capacity rejection at one page below the pool floor, skip the later readiness hook, and propagate a real readiness rejection after capacity passes. An SDK HTTP readiness failure must prevent both hooks. No failing case calls runtime stop.

## SDK differences verified

SparkRun 0.3.10 uses its actual `post_launch_lifecycle`, which raises `SystemExit(1)` on endpoint or hook failure. Its hook wrapper has a 600-second control timeout, without the 0.4 watchdog or hook metadata.

SparkRun 0.4.2 uses its actual `complete_post_launch`, which raises `RuntimeError` on endpoint or hook failure and returns `False` after successful hooks when `stop_after_post` is false. Its default wrapper includes the 3600-second watchdog, hook metadata, and a 3640-second control timeout (3600 + 10-second kill interval + 30-second grace). Endpoint polling can use the executor's liveness command; the older path uses Docker inspect. These commands and wrappers are recorded, never executed. Timeout enforcement itself is not tested.

## Native declarations and limits of the proof

Every emitted flag is checked against the actual native `server/cli.zig` table for explicit native support, arity, and declared choices. Recipe values and every recipe environment key are checked against the captured launch and hashed native source consumers. This is source-level evidence; the Zig parser and its complete numeric/semantic validation do not execute. The native RoCE implementation accepts `NCCL_IB_HCA` as an HCA fallback and selects its own GID unless `TF_ROCE_GID_INDEX` is set. The adapter preserves `NCCL_IB_GID_INDEX` for NCCL and does not translate it into that separate RoCE override.

The full `launch_inference` pipeline, hardware scheduling, image/model distribution, mod injection, Docker startup observer, and target-host commands do not execute. The Docker startup observer is disabled at its observation boundary so the real endpoint fallback runs in both SDKs. There is no GPU, NCCL, RoCE, real capacity, live inference, native video, or deployment proof here. The socket-table command text is generated by the SDK; synthetic exit statuses stand in for executing `ss`, `awk`, or any shell.

The runner starts only the two specified Python workers. Before importing SparkRun, each worker installs an audit trap that fails unmocked process creation, network/DNS operations, and process signals. Host/script/HTTP recorders allow only known commands and fixtures; unknown calls fail rather than falling through. The trap's process and socket self-tests appear as expected blocked attempts in the receipt. No launcher CLI, stop CLI, `stop --dry-run`, actual container command, or native serve binary executes.

The receipt hashes both harness files, the recipe, each observed SDK/repository source, the SDK script templates, and the native files read. Native files must match `HOME-LAB-SOURCES.json`. The SDK tree digest is SHA-256 of the compact, key-sorted JSON map from relative `.py`/`.sh` paths to their SHA-256 hashes; it must match before and after the tests. Required real-code calls are asserted separately from unittest success.

The operating baseline is now SparkRun 0.4.2 with the Python durable readiness-v2 profile, promoted independently in [#1302](https://github.com/kelchm/home-lab/pull/1302). The retained 0.3.10 controller remains a rollback input. These tests perform no host operation, promote no controller and do not enable native durable serving.
