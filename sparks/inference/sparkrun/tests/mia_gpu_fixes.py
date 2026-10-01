#!/usr/bin/env python3
"""Live GPU regression checks for the two site DFlash backports.

Run via docker exec in the SparkRun-owned Mia container, with serving idle.
The K-pool redo scenario adapts vLLM PR58454's Apache-2.0 regression test.
It checks production prefill/decode kernels, including a corrupt-ring control.
"""
import torch
from types import SimpleNamespace
from vllm.config import VllmConfig
from vllm.models.glm5next.nvidia.attention import Glm5NextTailCache
from vllm.triton_utils import tl, triton
from vllm.models.glm5next.nvidia.ops.kpool_compress import (
    kpool_compress_and_write_cache,
    kpool_decode_update_and_maybe_write_cache_batched,
    kpool_seed_tail_cache,
)
from vllm.v1.worker.gpu.sample.gumbel import gumbel_block_argmax
from vllm.v1.worker.gpu.spec_decode.dflash2.speculator import gumbel_noised_argmax


def check_geometry():
    config = object.__new__(VllmConfig)
    config.diffusion_config = None
    for pool, drafts, ring in ((4, 7, 16), (16, 7, 32), (4, 0, 4)):
        config.speculative_config = SimpleNamespace(num_speculative_tokens=drafts) if drafts else None
        layer = SimpleNamespace(_index_kpool=pool, cache_config=SimpleNamespace(block_size=256), head_dim=128)
        spec = Glm5NextTailCache.get_kv_cache_spec(layer, config)
        assert spec.block_size == ring and spec.sliding_window == ring
        assert spec.num_kv_heads == 1 and spec.head_size == 256 and spec.head_size_v == 0
    print("PASS real VllmConfig ring geometry and NVIDIA packed layout", flush=True)


def check_redo(pool, spec, ring_pools):
    head, page, nblk, ring = 128, 64, 2, pool * ring_pools
    torch.manual_seed(731)
    keys = torch.randn(4 * pool, head, dtype=torch.bfloat16, device="cuda")
    scores = torch.randn_like(keys)
    ape = torch.randn(pool, head, device="cuda")
    reference = torch.zeros(nblk, page, head + 4, dtype=torch.uint8, device="cuda")
    kpool_compress_and_write_cache(reference, keys.view(4, pool, head), scores.view(4, pool, head),
                                   ape, torch.arange(4, dtype=torch.int64, device="cuda"), pool, head)
    actual = torch.zeros_like(reference)
    tail = torch.zeros(nblk, 2, ring, head, dtype=torch.bfloat16, device="cuda")

    def step(positions, k, s):
        pos = torch.tensor([positions], dtype=torch.int32, device="cuda")
        slots = [p // pool if p % pool == pool - 1 else -1 for p in positions]
        kpool_decode_update_and_maybe_write_cache_batched(
            actual, tail, pos % ring, k.view(1, -1, head), s.view(1, -1, head), ape,
            torch.tensor([slots], dtype=torch.int32, device="cuda"), pos, pool, head)

    for position in range(2 * pool - 2):
        step([position], keys[position], scores[position])
    start = 2 * pool - 2
    drafts = torch.randn(spec, head, dtype=torch.bfloat16, device="cuda")
    draft_scores = torch.randn_like(drafts)
    step(list(range(start, start + spec + 1)), torch.cat([keys[start:start+1], drafts]),
         torch.cat([scores[start:start+1], draft_scores]))
    # The pool-completing draft was rejected; redo it with the correct keys.
    step(list(range(start + 1, start + spec + 2)), keys[start+1:start+spec+2], scores[start+1:start+spec+2])

    def pool_bytes(cache):
        flat = cache[0].flatten()
        return torch.cat([flat[head:2*head], flat[page*head+4:page*head+8]])

    matches = torch.equal(pool_bytes(actual), pool_bytes(reference))
    assert matches == (ring_pools > 1), (pool, spec, ring_pools, matches)
    print(f"PASS rejected-draft redo: pool={pool}, spec={spec}, ring={ring}, matches={matches}", flush=True)


def check_seed_stride():
    # Production tails alias a wider, padded indexer block. Verify two requests
    # and ring rollover, so a dense-stride or old modulo bug is observable.
    head, pool, ring, stride = 128, 4, 16, 64 * 132
    storage = torch.full((3, stride), -11, dtype=torch.bfloat16, device="cuda")
    tail = storage.as_strided((3, 2, ring, head), (stride, ring * head, head, 1))
    keys = torch.randn(38, head, dtype=torch.bfloat16, device="cuda")
    scores = torch.randn_like(keys)
    slots = torch.tensor([p % ring for p in range(19)] + [ring + p % ring for p in range(19)],
                         dtype=torch.int64, device="cuda")
    kpool_seed_tail_cache(tail, keys, scores, slots, pool)
    for block, offset in ((0, 0), (1, 19)):
        for position in range(15, 19):
            assert torch.equal(tail[block, 0, position % ring], keys[offset + position])
            assert torch.equal(tail[block, 1, position % ring], scores[offset + position])
    assert torch.all(storage[2] == -11)
    assert torch.all(storage[:2, 2*ring*head:] == -11)
    print("PASS ring seed with padded stride, rollover, and request isolation", flush=True)


@triton.jit
def sample_pair(logits_ptr, map_ptr, temps_ptr, seeds_ptr, positions_ptr, draft_ptr, target_ptr,
                WIDTH: tl.constexpr, FP64: tl.constexpr):
    row = tl.program_id(0)
    keys = tl.arange(0, WIDTH)
    logits = tl.load(logits_ptr + keys)
    seed = tl.load(seeds_ptr)
    position = tl.load(positions_ptr + row)
    temperature = tl.load(temps_ptr)
    _, draft = gumbel_noised_argmax(logits, keys, keys < WIDTH, seed, position, temperature, USE_FP64=FP64)
    _, target = gumbel_block_argmax(logits, keys, keys < WIDTH, row, map_ptr, temps_ptr, seeds_ptr,
                                   positions_ptr, None, 0, None, WIDTH, APPLY_TEMPERATURE=True, USE_FP64=FP64)
    tl.store(draft_ptr + row, draft)
    tl.store(target_ptr + row, target)


def check_noise():
    count, width = 8192, 64
    mapping = torch.zeros(count, dtype=torch.int32, device="cuda")
    positions = torch.arange(count, dtype=torch.int32, device="cuda")
    seeds = torch.tensor([4321], dtype=torch.int64, device="cuda")
    draft = torch.empty(count, dtype=torch.int32, device="cuda")
    target = torch.empty_like(draft)
    for fp64 in (False, True):
        temperature = torch.ones(1, device="cuda")
        logits = torch.zeros(width, device="cuda")
        sample_pair[(count,)](logits, mapping, temperature, seeds, positions, draft, target, WIDTH=width, FP64=fp64)
        agreement = (draft == target).float().mean().item()
        # Independent uniform draws match ~1/64; the unsalted bug matches 100%.
        assert 0.006 < agreement < 0.03, (fp64, agreement)
        for samples in (draft, target):
            bins = torch.bincount(samples.long(), minlength=width)
            assert bins.min().item() > 70 and bins.max().item() < 200, bins.tolist()
        temperature.zero_()
        logits[37] = 2
        sample_pair[(count,)](logits, mapping, temperature, seeds, positions, draft, target, WIDTH=width, FP64=fp64)
        assert torch.all(draft == 37) and torch.all(target == 37)
        print(f"PASS draft/target noise independence and greedy invariance: fp64={fp64}, agreement={agreement:.4f}", flush=True)


def main():
    assert torch.cuda.is_available()
    check_geometry()
    # k=7 production geometry: pool 4 needs four pools; pool 16 needs two.
    for pool, spec, good in ((4, 7, 4), (16, 7, 2)):
        check_redo(pool, spec, 1)
        check_redo(pool, spec, good)
    check_seed_stride()
    check_noise()
    torch.cuda.synchronize()
    print("PASS Mia site CUDA regressions", flush=True)


if __name__ == "__main__":
    main()
