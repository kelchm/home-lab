# GLM 5.3 Flash with SparkRun

This is the site-specific SparkRun deployment for `spark-1` (`10.32.21.31`) and `spark-2` (`10.32.21.32`). Both nodes are outside Flux. Vonk services remain stopped and disabled; its packages, model cache and rollback data are retained.

## Pins

- SparkRun: `0.3.9`, installed in `/home/kelchm/.local/share/sparkrun-venv` on spark-1, with `~/.local/bin/sparkrun` pointing to its executable.
- Mia source: [`ca8557665bffa6529758f2c330ba8fb44c1e801a`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks/tree/ca8557665bffa6529758f2c330ba8fb44c1e801a), staged at `/home/kelchm/sparkrun/mia-glm-ca855766` on both nodes. The recipe mounts that source's overlays and chat template into the published image.
- Published InstantTensor image: `ghcr.io/miaai-lab/glm-5.3-flash-2x-dgx-sparks@sha256:447114ee77d14c9b4732ee23978ada2a0ee9027868a231d6fd42700a8b25be1d`. This is an immutable registry digest, not a claim that the image was built from the overlay commit above.
- Target weights: EXL3/TR3 4bpw, 120 shards, 175,642,157,752 bytes, at `/opt/sparkrun/models/glm53-exl3` on both nodes.
- DFlash2 weights: upstream revision `dc77ff1c99eeb2df044ee3d4f0094eb033fee410`, one shard, 2,342,169,800 bytes, at `/opt/sparkrun/models/glm53-dflash2-dc77ff1c` on both nodes. SHA-256: `b33c03475ba7322cf398828f2d8d1be376df30dc05c6b40c28c8ea8da23e410b`.

The target and retained old draft directories are independent directory trees with hardlinked files from retained Vonk installation `17b3f02c-b091-42fc-9423-c7ba6c06ce9c`. The parity audit downloaded the pinned draft into a new independent directory because the retained draft did not match upstream. All 120 target shards were verified against the upstream manifest on both nodes. Treat these files as immutable: in-place edits would affect both links. Removing one directory tree does not remove the other links. Container root can read the retained file permissions; no recursive file ownership or permission changes were applied.

The saved `sparks` cluster has hosts `10.32.21.31,10.32.21.32`, SSH user `kelchm`, greedy scheduling, cache `/opt/spark-cache/huggingface`, and management-network image transfers. SparkRun detects the two ConnectX-7 adapters for inference communication. Management transfers avoid unknown fabric-address SSH host keys; they do not select the inference transport.

## Site settings

The [upstream parity audit](upstream-audit-20260919.md) records the corrected recipe, verified matches and intentional site differences. The configuration uses upstream `fair` scheduling, its 48-image/1-video limit and 1 GiB multimodal processor cache, persistent TileLang cache, and post-ready shape warmup. It keeps InstantTensor, 850,000 context, 0.85 memory utilization, four sequences, 7,168-token batches, DFlash2 k=7 with draft TP=2, FP8 KV, E3 grouped prefill, and unmodified target weights (`ABLIT=0`). It applies two supported settings: `NCCL_MIN_NCHANNELS=NCCL_MAX_NCHANNELS=8` (Mia's `NCCL_NCHANNELS=8`) and `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0` (Mia's `CG_ESTIMATE=0`). The latter disables the memory estimate, not CUDA graphs.

Before the parity correction, the first direct SparkRun attempt with automatic channel selection (64 channels) and graph estimation enabled failed with 9.98 GiB available KV against 13.46 GiB required. The two memory overrides passed allocation on that original deployment with 14.47 GiB available KV and a reported 913,909-token capacity equivalent. These two changes were tested together; this is not an isolated measurement of either change's contribution. InstantTensor weight loading took 34.49 seconds on the head. Neither the loader nor the serving framework was patched locally.

The reported capacity equivalent does not mean that much reusable prefix cache: the hybrid model's boot log reports approximately 53,760 tokens of aligned idle cached-conversation capacity with dense retention. Similarly, SparkRun's prelaunch memory estimate is approximate and does not replace vLLM's actual hybrid-KV allocation check. An 850k configured context is not evidence of a successful full-length workload test.

## Operations

Run on spark-1:

```sh
~/.local/bin/sparkrun run ~/sparkrun/recipes/mia-glm53-exl3.yaml --cluster sparks --no-auto-detect --no-sync-tuning --no-follow
~/.local/bin/sparkrun status --cluster sparks
~/.local/bin/sparkrun logs ~/sparkrun/recipes/mia-glm53-exl3.yaml --cluster sparks
~/.local/bin/sparkrun stop ~/sparkrun/recipes/mia-glm53-exl3.yaml --cluster sparks
```

`run` creates detached containers, waits for readiness, and runs upstream shape warmup through `post_exec`. A warmup failure surfaces as a failed launch command; inspect its output and serving logs. Run the API acceptance checks separately. Check `http://10.32.21.31:8888/health`, then use the verification and benchmark steps below. Do not run a second inference workload on either node while this two-node recipe is active.

The API base URL is `http://10.32.21.31:8888/v1`; the served model is `GLM-5.3-Flash-EXL3`. This direct LAN endpoint does not use Vonk's proxy or client credentials. Containers do not automatically restart after a host reboot; use the launch command after both hosts are ready.

The retained Vonk INPUT chain blocks port 8888 except from localhost and its controller. A separate INPUT rule permits only workstation `10.32.10.244/32` to `10.32.21.31:8888` via `enP7s7`, tagged `sparkrun-glm-workstation`; no Vonk chains were flushed. The rule is a live iptables change, not a new persistent firewall service. Check access after host reboot or a workstation address change. Its exact removal command on spark-1 is:

```sh
sudo iptables -D INPUT -i enP7s7 -s 10.32.10.244/32 -d 10.32.21.31/32 -p tcp --dport 8888 -m comment --comment sparkrun-glm-workstation -j ACCEPT
```

## Verification

The [post-audit validation](upstream-validation-20260919.json) records the current corrected deployment: upstream shape warmup completed, all six API checks and a five-image request passed, and the staggered newcomer received its first token in 0.865 seconds while the original response continued. Boot reported 15.03 GiB KV / 944,266 equivalent tokens. See the [parity audit](upstream-audit-20260919.md) for scope and limits.

The [initial 2026-09-19 validation summary](validation-20260919.json) records the deployment **before the parity correction**, using the old draft and `skip` scheduling. Its measurements below are historical. All six API checks passed both on spark-1 and from the workstation: arithmetic output, automatic tool call, tool-result round trip, JSON schema, reasoning output, and image input. Both containers remained running after the benchmarks.

| Workload | Samples | Measured result |
|---|---|---|
| Structured decode, C1, 400 output tokens | 3 | 67.4 tok/s median; 0.345 s median TTFT |
| Prose decode, C1, 400 output tokens | 3 | 26.0 tok/s median; 0.234 s median TTFT |
| Structured decode, C4, 400 tokens/request | 2 batches | 171.1 aggregate tok/s; 45.6 median stream tok/s |
| Unique ~8.5k-token prompts after first sample | 2 | 1,509–1,637 prompt tok/s; correct marker retrieval |
| Unique 32,561-token prompt | 1 | 1,723 prompt tok/s; 18.90 s TTFT; correct marker retrieval |

Decode uses Mia's `tests/bench_decode.py`, temperature 0 and thinking off. Its published stock reference is approximately 63–65 tok/s structured and 27 tok/s prose on two Sparks; these measurements are broadly consistent. The first 8.5k prompt was an outlier: 11.82 s TTFT and 64.20 s end-to-end for four output tokens, versus 5.30–5.75 s end-to-end for the following unique prompts. Its precise cause was not isolated. No full 850k request was tested.

The upstream coherence probe has one recorded failure: with thinking off, the model answered “No” to whether 9.9 exceeds 9.11, then correctly explained that 9.90 exceeds 9.11. The Paris and sky-color probes passed. This is a model-output limitation, not a clean quality-gate pass. Raw receipts live at `/home/kelchm/sparkrun/receipts/20260919` on spark-1 and the ignored `.private/sparkrun-20260919` directory in the deployment checkout.

From this checkout:

```sh
python3 sparks/inference/sparkrun/verify.py --out /tmp/glm53-api-acceptance.json
```

For upstream's bounded decode benchmarks, run on spark-1:

```sh
python3 ~/sparkrun/mia-glm-ca855766/tests/bench_decode.py --phase structured --structured --runs 3 --max-tokens 400 --out /tmp/glm53-structured.json
python3 ~/sparkrun/mia-glm-ca855766/tests/bench_decode.py --phase prose --runs 3 --max-tokens 400 --out /tmp/glm53-prose.json
python3 ~/sparkrun/mia-glm-ca855766/tests/bench_decode.py --phase concurrent --structured --concurrency 4 --runs 2 --max-tokens 400 --out /tmp/glm53-c4.json
```

## NCCL comparison, 2026-09-19

This historical comparison predates the parity correction and used the old draft checkpoint. A controlled 8 → 16 → 8 channel run kept InstantTensor, the image and overlays, 850k context, 0.85 utilization, graph estimation disabled, and `GLM53_MIXED_PREFILL_CHUNK=skip` fixed. The [comparison summary](benchmark-20260919-nccl.json) records medians and ranges. Each phase excluded one warmup per workload, then measured three 400-token decode requests and two unique-prefix ~32.6k-token prompts. An initial identical-prompt prefill series showed cache effects and was excluded.

| Measurement | 8 channels before | 16 channels | 8 channels restored |
|---|---:|---:|---:|
| Structured decode, tok/s | 64.1 | 64.8 | 64.1 |
| Prose decode, tok/s | 26.0 | 27.3 | 27.4 |
| Uncached ~32.6k prompt TTFT, seconds | 19.14 | 18.70 | 19.08 |
| Available KV, GiB | 14.47 | 13.52 | 14.68 |
| Reported capacity equivalent, tokens | 913,909 | 853,195 | 925,093 |
| Usable KV block IDs (532 required at 850k) | 571 | 533 | 578 |

Eight channels remains deployed. The decode ranges overlap, prose speculative acceptance varies, and the approximately 2% prefill difference is too small in this sample to justify the reduced KV margin. Allocation varies slightly between launches, but the restored eight-channel run recovered the margin. Six API acceptance checks passed after restoration; both hosts remained healthy. This experiment did not retest 64 channels or execute a full 850k request. Raw results, the harness, and serving logs are retained at `/home/kelchm/sparkrun/receipts/nccl-comparison` on spark-1 and `.private/nccl-comparison` in this checkout.

Earlier concurrency tests also exposed an independent limitation of `GLM53_MIXED_PREFILL_CHUNK=skip`: a one-word question submitted two seconds into a prose response waited 20.3 seconds for its first token. Mixed-topic four-request batches reached approximately 37 aggregate tok/s with 23–25 second waits for three requests, versus approximately 56 aggregate tok/s for repeated prompts admitted together. The pinned scheduler explicitly defers prefill while a peer decodes in `skip` mode. The parity correction switches to upstream `fair`; changing NCCL channels alone does not address this policy.

## Reprovisioning

The YAML intentionally contains site-specific absolute mounts. Copy it to `~/sparkrun/recipes/mia-glm53-exl3.yaml` on spark-1. Stage the exact Mia commit at the path above on both nodes and verify both model inventories before launching. On fresh hosts, obtain the target and draft snapshots through Mia's documented download process; this recipe skips download because its model is an absolute local path.

To roll back this deployment, stop it with SparkRun. That frees both GPUs while preserving all caches. Re-enabling Vonk is a separate operation and is not part of this rollback.
