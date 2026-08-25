# DeepSeek-V4-Flash-0731 across both Sparks

Runs [tonyd2wild's DSpark guide](https://github.com/tonyd2wild/DeepSeek-v4-Flash-0731-DSpark-1M-NVFP4-KV-2x-DGX-Spark) — the recipe behind the [Level1Techs dual-Spark thread](https://forum.level1techs.com/t/dual-sparks-in-nvfp4-vs-4x-rtx-pro-6000-with-native-deepseek-v4-0731-quants-and-speed/253539). This directory carries only our site overrides; the guide is cloned onto each host and owns the build and launch.

**Do not substitute a prebuilt image.** The guide builds `vllm-dspark-runtime:dspark-nvfp4-stage-c` locally as a four-stage overlay on vLLM 0.21.x. That overlay is what supplies `nvfp4_ds_mla` and `--speculative-config method=dspark`; a stock image rejects both at argument parsing. An earlier attempt here read that rejection as "the published config cannot run" and switched to a different prebuilt image — that was wrong, and cost a working deployment.

## Setup

Run on **both** hosts. The revision is pinned deliberately: this is a third-party
repo whose `main` moves, and an unpinned clone is not reproducible.

```sh
git clone https://github.com/tonyd2wild/DeepSeek-v4-Flash-0731-DSpark-1M-NVFP4-KV-2x-DGX-Spark.git ~/dspark-guide
cd ~/dspark-guide && git checkout 0fec8084979040298fe2a3d4ee89abc640e6bc12

cp .env.dspark.example .env.dspark
# Apply this repo's overrides: every KEY= line in env.dspark.overrides replaces
# the guide's default of the same name; everything else is left alone.
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  key="${line%%=*}"
  sed -i "s|^${key}=.*|${line}|" .env.dspark
done < /path/to/home-lab/sparks/inference/deepseek/env.dspark.overrides

# Verify the overrides landed before building.
grep -E '^(MASTER_ADDR|VLLM_HOST_IP|WORKER_VLLM_HOST_IP|NCCL_IB_HCA|HF_CACHE|DSPARK_MODEL)=' .env.dspark
```

Then, on the head only (it rsyncs and rebuilds on the worker):

```sh
./build-dspark-vllm-runtime.sh
./start-deepseek-v4-flash-dspark.sh     # worker-first launch
```

Serves `deepseek-v4-flash-dspark` on **:8888** (not :8000, which is the Qwen stack's port). Mutually exclusive with Qwen: TP=2 claims both hosts.

## 0731 needs Patch 4, or you silently lose half your throughput

We serve `deepseek-ai/DeepSeek-V4-Flash-0731`, the official release — not the
`fraserprice/…-DSpark` preview the guide's defaults point at. 0731 requires
**Patch 4** (`patches/0004-dspark-shared-expert-gate-up-proj.patch`): vLLM's
DSpark draft weight loader drops twelve tensors, leaving the draft's always-on
shared expert uninitialised. Output stays correct and steps/s are unchanged —
only acceptance collapses, so nothing about it looks broken.

| 0731 | acceptance | tok/s |
|---|---|---|
| stock loader | 25.7% | 42.0 |
| with Patch 4 | 60.2% | 66.1 |

A build from the pinned revision includes it. Verify:

```sh
docker exec <container> python3 -c "import vllm,os,io; p=os.path.join(os.path.dirname(vllm.__file__),'v1/spec_decode/dspark.py'); print('gate_up_proj' in io.open(p).read())"
```

Two more 0731-specific notes from the guide: benchmark with `stream: false`
(under speculative decoding vLLM emits at most one SSE chunk, so streaming
timings mislead), and avoid harnesses that send `stop` sequences.

## Patch 3 is the thing that matters

Cold-prefill prompt-tail corruption — leaked tool markup, mid-word starts, agent doom-loops — is a scheduler bug, fixed by "Patch 3" in the guide's overlay. Upstream's measurement, forced cold prefill:

| | failures |
|---|---|
| without Patch 3 (k=3, k=5, every other documented setting) | 44/44 |
| with Patch 3 | 0/28 |

**`num_speculative_tokens` is not the variable.** Warm requests never fail in any configuration, which is why a short smoke test passes on a broken deployment. Verify before trusting a deployment:

```sh
docker exec <container> python3 -c "import vllm,os,io; print(io.open(os.path.join(os.path.dirname(vllm.__file__),'v1/core/sched/scheduler.py')).read().count('is_prefill_chunk'))"
```

`5` is the documented signature; a prebuilt image tried here reported `8` from
unrelated chunked-prefill code and was **not** patched. Treat the count as a
smoke test only — it matches a string, not behaviour. The real check is the
guide's `benchmarks/replay_hermes.py`, which forces a cold prefill on every
iteration by prepending a nonce and scores the output for leaked markup:

```sh
URL=http://localhost:8888/v1 N=10 COLD=1 python3 benchmarks/replay_hermes.py
```

Keep prefix caching **on**. It is the second defence: warm requests never hit the corrupting path, and it is worth ~10x on time-to-first-token.

## Measured here

94 requests, 6.76M prompt tokens, three concurrent sessions:

| | value |
|---|---|
| TTFT | 3.07 s mean, flat across the session |
| queue wait | 0.00 s |
| decode | ~30 tok/s single stream |
| prefix cache | 95-98% hit |
| spec-decode acceptance | 44-52% |
| preemptions | 0 |
| GPU | 63-75 C, 57-69 W, 96% util, zero thermal throttling |

`reasoning_effort` is effectively binary: `none` suppresses thinking entirely (26x fewer tokens on a trivial prompt); `minimal` through `max` are indistinguishable, because vLLM maps the parameter onto a boolean `enable_thinking` template kwarg.

## Operational limits

- **Host memory is the binding constraint, not heat.** Three concurrent sessions leave ~6 GB available of 121 GB. GB10 shares one pool between CPU and GPU, and the documented failure is MemAvailable collapse with the host dropping off SSH.
- **There is no hardware watchdog** — the ARM SBSA watchdog is present but permanently disabled, so a hang needs physical intervention. Run `scripts/diag/prepare-uma-memory.sh --apply` from the guide to reserve kernel headroom.
- `docker stats` memory is meaningless here; GPU allocations on unified memory bypass cgroup accounting. Use `free -g`.
- Large UVM allocations may not release on teardown; full recovery can require a reboot.
