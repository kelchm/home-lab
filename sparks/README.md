# DGX Spark workloads

The two DGX Spark hosts run mutually exclusive inference profiles, switched by an in-repo Ansible control plane that is invocable two equivalent ways: `task sparks:*` from an operator checkout, or the [controller API](controller/README.md) on spark-1 for programmatic callers. Hardware bring-up (network, RDMA, fabric isolation) lives in [`docs/runbooks/dgx-spark-bringup.md`](../docs/runbooks/dgx-spark-bringup.md); the transaction design and rejected alternatives live in [`docs/plans/20260828-spark-control-plane.md`](../docs/plans/20260828-spark-control-plane.md) and [`docs/plans/20260831-spark-llm-foundation.md`](../docs/plans/20260831-spark-llm-foundation.md).

These hosts are outside Flux and outside Talos automation, deliberately: profile transitions are hazardous (UMA collapse, no watchdog), so nothing reconciles autonomously. Git owns what *can* run and what runs *by default* after a boot; the switch transaction owns what runs *right now*; residency is operational state recorded on spark-1 (`/opt/spark-stack/resident`, `switch.log`), not in git.

## Default profile

| | |
|---|---|
| Host | `spark-1` (`10.32.21.31`) |
| Stack | vLLM serving `nvidia/Qwen3.6-35B-A3B-NVFP4` |
| Endpoint | `http://spark.home.kelch.io/v1` (Caddy; direct: `http://10.32.21.31:8000/v1`) |
| Model cache | `/opt/spark-models` on the host (22 GB) |
| Measured | ~80 tok/s decode, 131k context |

Two TP=2 profiles claim **both** nodes and serve on **:8888**: `deepseek-ai/DeepSeek-V4-Flash-0731` via [tonyd2wild's DSpark guide](inference/deepseek/), and `GLM-5.3-Flash-EXL3` via [MiaAI-Lab's EXL3 + DFlash2 guide](inference/glm/). The upstream guides are cloned and pinned on the hosts; this repo carries only site overrides, artifact pins, and the control-plane glue. All three profiles are mutually exclusive. Spark's unified memory disables GPUDirect RDMA, so NCCL all-reduce over the ConnectX-7 fabric runs far below raw RDMA line rate — two independent single-node servers beat tensor-parallel for any model that fits in one node. Reach for TP=2 only for models that genuinely exceed ~104 GB.

## Operating it

```sh
task sparks:switch PROFILE=glm53-exl3   # guarded switch: unpublish, teardown both
                                        #   hosts, reclaim gate, drop_caches,
                                        #   preflight, ready gate, smoke, publish
task sparks:publish PROFILE=glm53-exl3  # re-point the stable endpoint, no teardown
task sparks:baseline                    # converge hosts, deploy Caddy + controller
task sparks:status                      # both nodes: GPU, containers, endpoint
task sparks:logs HOST=10.32.21.31
task sparks:down                        # break-glass teardown, zero dependencies
```

A profile is one YAML file in [`ansible/profiles/`](ansible/profiles/): `qwen36-nvfp4` (single-node vLLM, `:8000`), `ds4f-dspark` and `glm53-exl3` (TP=2 pinned guides, `:8888`). Names follow `<family+version>-<quant/runtime lane>` so variants and new versions coexist. The file declares everything recipe-specific — pins, ports, start driver (`compose` or `guide`), preflight policy, and the smoke contract — and the generic `preflight`/`start`/`smoke` tasks are driven entirely by it; the playbooks discover profiles from the directory, so adding a recipe means adding one file, not editing the transaction.

`switch` refuses to proceed if MemAvailable stays low after teardown — that means the previous profile's UVM allocations did not release and the affected host needs a reboot first. It also refuses concurrent switches (lock on spark-1; remove `/tmp/spark-switch.lock` if stale). TP=2 switches never pull, download, sync, or build mid-switch — preflight verifies the pinned guide rev, every override line, exact weight refs, and cross-rank image identity, and fails with instructions instead. The qwen profile pulls only its digest-pinned image, explicitly, before start.

After a reboot the pair converges to the baseline: Caddy, the `qwen` profile, and the stable route to it come back (docker restart policy + `spark-baseline.service`, which also resets a route left pointing at a TP=2 port); TP=2 profiles never auto-start — rerun `switch` for those.

`task sparks:down` stops every profile on **both** hosts and needs nothing but SSH — by design it does not touch the resident marker, so `status` shows stale residency until the next switch (`ansible-playbook down.yaml` records `none`). To reclaim disk as well, on each host:

```sh
sudo rm -rf /opt/spark-models /opt/spark-stack /opt/spark-cache
rm -rf ~/dspark-guide ~/glm53-guide
rm -rf ~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-TR3-4bpw
rm -rf ~/.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2
# Only the images this work pulled or built - `prune -a` would take unrelated ones.
docker rmi vllm-dspark-runtime:dspark-nvfp4-stage-c \
           vllm-dspark-runtime:mia-raf-pr1 \
           vllm-dspark-runtime:mia-raf-pr1-nvfp4-a \
           vllm-dspark-runtime:mia-raf-pr1-nvfp4-b || true
docker rmi "$(grep -oE 'vllm/vllm-openai@sha256:[0-9a-f]+' /path/to/compose.yaml)" || true
docker rmi ghcr.io/miaai-lab/glm-5.3-flash-2x-dgx-sparks@sha256:9bb1557a4234fce63d59599e44d10747eabd742beb337eebf9e7070be8a0fd58 || true
```

The pulled images and pinned model caches are the large residue; remove only the explicitly listed paths and tags, never an indiscriminate Docker or Hugging Face cache prune.

## Programmatic control

The controller ([`controller/`](controller/README.md), deployed by `task sparks:baseline`) exposes the same transactions over an authenticated API at `http://spark.home.kelch.io/admin/v1/` — agents and tools switch, publish, tear down, and read state without a repo checkout, ansible, or SSH keys. It executes the playbooks from its own checkout of `main`, so a merged profile becomes switchable within a minute of landing; it never initiates anything itself. Both invocation paths contend on the same lock on spark-1, so they cannot interleave.

## The stable endpoint

Clients talk to **`http://spark.home.kelch.io/v1`** — Caddy on spark-1, deployed by `task sparks:baseline` from `sparks/ansible/roles/caddy/`. The route is an admission decision, not health-based discovery: the switch transaction publishes maintenance (an OpenAI-shaped 503) before any teardown, and publishes the profile's exact port only after the model has passed its identity check and coherence smoke. A wedged switch leaves the endpoint in maintenance rather than routing to whatever happens to answer; `task sparks:publish` re-points it at a verified healthy profile without a teardown cycle (after a Caddy or spark-1 restart while a TP=2 profile was resident, this is the recovery path). `curl http://spark.home.kelch.io/v1/models` answers "what is live"; the `/admin/*` route to the controller survives every mode. Direct ports keep working if Caddy is down, as explicit break-glass.

## Using it from opencode

The provider block lives in `~/.config/opencode/opencode.jsonc` (not in this repo — it is per-machine). The provider ID stays `spark` and the `models` map carries a **superset** across profiles — only the resident one responds:

```jsonc
"provider": {
  "spark": {
    "npm": "@ai-sdk/openai-compatible",
    "options": {
      "baseURL": "http://spark.home.kelch.io/v1",
      // No default; without it a dropped SSE stream hangs the client forever.
      "chunkTimeout": 120000
    },
    "models": {
      "qwen3.6-35b": {
        "reasoning": true,
        "limit": { "context": 131072, "output": 32000 }
      },
      "deepseek-v4-flash-dspark": {
        "reasoning": true,
        "limit": { "context": 1000000, "output": 32000 }
      },
      "GLM-5.3-Flash-EXL3": {
        "reasoning": true,
        "limit": { "context": 1000000, "output": 32000 }
      }
    }
  }
}
```

Then `opencode run --model spark/GLM-5.3-Flash-EXL3 "..."`, or pick a resident model from `/models` interactively. Never let any tool rewrite this block: opencode keys sampling overrides on the provider ID and on model-ID substrings.

## Landmines

These are load-bearing. Each one was hit or verified during bring-up.

- **`--gpu-memory-utilization` is taken against all 121 GiB of system memory**, not a GPU-only pool — `nvidia-smi` reports no dedicated VRAM on GB10. Run `sync; echo 3 > /proc/sys/vm/drop_caches` before a large load; `cudaMemGetInfo` ignores reclaimable page cache and vLLM will refuse to start on a budget it actually has.
- **vLLM selects the Marlin NVFP4 kernel on `sm_121`, not CUTLASS.** That is the safe outcome — the CUTLASS FP4 path on SM121 produces *silently wrong* results (rows of identical values, output collapsing to `!`), with no CUDA error. If a future image switches to CUTLASS, force it back with `VLLM_NVFP4_GEMM_BACKEND=marlin`, and check output for row-identical logits.
- **`temperature` in a model block is a boolean capability flag, not a value.** Left unset it defaults to `false` and opencode sends no temperature at all. Numeric values belong on `agent.*`, and `reasoning_effort` / `top_p` are rejected outright by the config schema.
- **`options.chunkTimeout` has no default.** A silently dropped SSE stream then hangs the client forever with no error ([opencode#37580](https://github.com/anomalyco/opencode/issues/37580)).
- **`limit.output` is clamped to 32,000 globally, and reasoning tokens count against it.** A thinking model can spend the entire budget reasoning and return empty content with `finish_reason: length` ([opencode#29363](https://github.com/anomalyco/opencode/issues/29363)) - which also breaks the agent loop mid-tool-call ([opencode#18108](https://github.com/anomalyco/opencode/issues/18108)).
- **Set `limit.input` or `compaction.reserved` is silently discarded** ([opencode#38835](https://github.com/anomalyco/opencode/issues/38835)).
- **Keep the provider ID `spark`.** opencode hardcodes sampling overrides keyed on provider ID and on model-ID substrings (`deepseek`, `glm-4.7`, `minimax-m2`, `kimi-k2`); a differently-named provider silently changes sampling.
- **Stay on driver 580.x.** 590.x has a CUDA-graph capture deadlock.
- **Pre-pull images before any coordinated multi-node run.** An implicit pull inside `docker run` delays that node by however long the image takes and silently desynchronises the run.
- Qwen3.6 emits long reasoning traces (~6 k characters for a trivial function). Budget output tokens generously; a low `max_tokens` truncates mid-thought and returns empty content.

## Thermal

Measured thermal and memory limits: [`docs/dgx-spark-thermal.md`](../docs/dgx-spark-thermal.md). Heat is not the constraint for these workloads; host memory is.
