# DGX Spark workloads

Temporary, operator-driven deployment of the two DGX Spark hosts. Hardware bring-up (network, RDMA, fabric isolation) lives in [`docs/runbooks/dgx-spark-bringup.md`](../docs/runbooks/dgx-spark-bringup.md); this directory covers what runs *on* them.

**Not GitOps.** These hosts are outside Flux and outside Talos automation. The stack is committed here and pushed over SSH with `task sparks:*`. That is a deliberate placeholder for the deploy model in [`docs/plans/20260620-nas-out-of-cluster-workloads.md`](../docs/plans/20260620-nas-out-of-cluster-workloads.md) — when doco-cd lands, it can consume `sparks/inference/compose.yaml` unchanged.

## What runs today

| | |
|---|---|
| Host | `spark-1` (`10.32.21.31`) |
| Stack | vLLM serving `nvidia/Qwen3.6-35B-A3B-NVFP4` |
| Endpoint | `http://10.32.21.31:8000/v1` (OpenAI-compatible) |
| Model cache | `/opt/spark-models` on the host (22 GB) |
| Measured | ~80 tok/s decode, 131k context |

A second route runs `deepseek-ai/DeepSeek-V4-Flash-0731` across **both** nodes at tensor-parallel 2 on **:8888** — see [`inference/deepseek/`](inference/deepseek/). It is not deployed from this repo: it runs [tonyd2wild's DSpark guide](https://github.com/tonyd2wild/DeepSeek-v4-Flash-0731-DSpark-1M-NVFP4-KV-2x-DGX-Spark) cloned onto each host, which builds its own runtime image locally. This directory carries only the site overrides. The two routes are mutually exclusive: TP=2 claims both hosts. Spark's unified memory disables GPUDirect RDMA, so NCCL all-reduce over the ConnectX-7 fabric runs far below raw RDMA line rate — two independent single-node servers beat tensor-parallel for any model that fits in one node. Reach for TP=2 only for models that genuinely exceed ~104 GB.

## Operating it

```sh
task sparks:deploy HOST=10.32.21.31   # push compose + start
task sparks:status                    # both nodes: GPU, containers, endpoint
task sparks:logs HOST=10.32.21.31
task sparks:down HOST=10.32.21.31     # full teardown
```

`task sparks:down` stops both routes on **both** hosts. To reclaim disk as well, on each host:

```sh
sudo rm -rf /opt/spark-models /opt/spark-stack /opt/spark-cache
rm -rf ~/dspark-guide
# Only the images this work pulled or built - `prune -a` would take unrelated ones.
docker rmi vllm-dspark-runtime:dspark-nvfp4-stage-c \
           vllm-dspark-runtime:mia-raf-pr1 \
           vllm-dspark-runtime:mia-raf-pr1-nvfp4-a \
           vllm-dspark-runtime:mia-raf-pr1-nvfp4-b || true
docker rmi "$(grep -oE 'vllm/vllm-openai@sha256:[0-9a-f]+' /path/to/compose.yaml)" || true
```

The pulled images are the large residue — roughly 45 GB per host across the vLLM and DeepSeek tags.

## Using it from opencode

The provider block lives in `~/.config/opencode/opencode.jsonc` (not in this repo — it is per-machine):

```jsonc
"provider": {
  "spark": {
    "npm": "@ai-sdk/openai-compatible",
    "options": {
      "baseURL": "http://10.32.21.31:8000/v1",
      // No default; without it a dropped SSE stream hangs the client forever.
      "chunkTimeout": 120000
    },
    "models": {
      "qwen3.6-35b": {
        "reasoning": true,
        "limit": { "context": 131072, "output": 32000 }
      }
    }
  }
}
```

Then `opencode run --model spark/qwen3.6-35b "..."`, or pick it from `/models` interactively.

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
