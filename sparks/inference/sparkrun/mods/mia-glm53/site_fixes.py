#!/usr/bin/env python3
"""Backport vLLM #54282 and #58454 after the pinned Mia overlays.

The image vendors an older NVIDIA attention layout and a draft-only Gumbel
helper. Preserve that layout while porting the upstream ring and noise fixes.
All files are prepared and compiled before any write; anchor drift is fatal.
"""
import ast
from pathlib import Path


def replace_once(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f"Expected {count} site-fix anchors: {old!r}")
    return text.replace(old, new)


def function(text, name, changes):
    nodes = [n for n in ast.walk(ast.parse(text))
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(nodes) != 1:
        raise ValueError(f"Expected one function {name}")
    node = nodes[0]
    lines = text.splitlines(keepends=True)
    body = "".join(lines[node.lineno - 1:node.end_lineno])
    for old, new, *counts in changes:
        body = replace_once(body, old, new, count=counts[0] if counts else 1)
    return "".join(lines[:node.lineno - 1]) + body + "\n" + "".join(lines[node.end_lineno:])


def patch_attention(text):
    # Restrict the change to Glm5NextTailCache; other indexer cache specs share
    # this method name. The power-of-two ring divides the hybrid cache block.
    node = next(n for n in ast.parse(text).body
                if isinstance(n, ast.ClassDef) and n.name == "Glm5NextTailCache")
    lines = text.splitlines(keepends=True)
    body = "".join(lines[node.lineno - 1:node.end_lineno])
    if "# homelab: vLLM #58454" in body:
        if "block_size=ring" not in body or "sliding_window=ring" not in body:
            raise ValueError("Incomplete ring backport")
        return text
    body = replace_once(body, "        return KpoolTailSpec(\n",
                        "        # homelab: vLLM #58454, rejected drafts need distinct ring slots.\n"
                        "        span = self._index_kpool + vllm_config.num_speculative_tokens\n"
                        "        pools = (span + self._index_kpool - 1) // self._index_kpool\n"
                        "        ring = self._index_kpool * (1 << (pools - 1).bit_length())\n"
                        "        assert self.cache_config.block_size % ring == 0\n"
                        "        return KpoolTailSpec(\n")
    body = replace_once(body, "block_size=self._index_kpool,", "block_size=ring,")
    body = replace_once(body, "sliding_window=self._index_kpool,", "sliding_window=ring,")
    return "".join(lines[:node.lineno - 1]) + body + "\n" + "".join(lines[node.end_lineno:])


def patch_kpool(text):
    if "# homelab: vLLM #58454" in text:
        for expected in ("RING=tail_kv_cache.shape[2]", "RING=ring", "safe_pos % RING"):
            if expected not in text:
                raise ValueError("Incomplete kpool ring backport")
        return text
    text = function(text, "_kpool_tail_seed_kernel", [
        ("    KPOOL: tl.constexpr,", "    KPOOL: tl.constexpr,\n    RING: tl.constexpr,"),
        ("blk = t // KPOOL", "blk = t // RING"),
        ("ahead // KPOOL == blk", "ahead // RING == blk"),
        ("(t % KPOOL) * HEAD_DIM", "(t % RING) * HEAD_DIM"),
    ])
    text = function(text, "kpool_seed_tail_cache", [
        ("        KPOOL=kpool,", "        KPOOL=kpool,\n        RING=tail_kv_cache.shape[2],"),
    ])
    text = function(text, "_kpool_decode_update_batched_kernel", [
        ("    POOL_SIZE: tl.constexpr,", "    POOL_SIZE: tl.constexpr,\n    RING: tl.constexpr,"),
        ("phys_slot = safe_pos % POOL_SIZE", "phys_slot = safe_pos % RING"),
        ("tl.maximum(tail_slot, 0).to(tl.int64) // POOL_SIZE", "tl.maximum(tail_slot, 0).to(tl.int64) // RING"),
        ("phys = (pool_logical_start + pool_slot) % POOL_SIZE", "phys = (pool_logical_start + pool_slot) % RING", 2),
    ])
    text = function(text, "kpool_decode_update_and_maybe_write_cache_batched", [
        ("    assert tail_kv_cache.shape[2] == pool_size", "    ring = tail_kv_cache.shape[2]\n    assert ring >= pool_size and ring % pool_size == 0, (ring, pool_size)"),
        ("        POOL_SIZE=pool_size,", "        POOL_SIZE=pool_size,\n        RING=ring,"),
    ])
    return "# homelab: vLLM #58454 rejected-draft ring backport\n" + text


def patch_draft(text):
    if "# homelab: vLLM #54282" in text:
        if "pos + (1 << 30)" not in text:
            raise ValueError("Incomplete draft-noise backport")
        return text
    return replace_once(text, "        gumbel_seed = tl.randint(seed, pos)\n",
                        "        # homelab: vLLM #54282, independent proposal/residual noise.\n"
                        "        gumbel_seed = tl.randint(seed, pos + (1 << 30))\n")


def main():
    site = Path("/usr/local/lib/python3.12/dist-packages/vllm")
    changes = {
        site / "models/glm5next/nvidia/attention.py": patch_attention,
        site / "models/glm5next/nvidia/ops/kpool_compress.py": patch_kpool,
        site / "v1/worker/gpu/spec_decode/dflash2/speculator.py": patch_draft,
    }
    prepared = {path: transform(path.read_text()) for path, transform in changes.items()}
    for path, text in prepared.items():
        compile(text, str(path), "exec")
    for path, text in prepared.items():
        temp = path.with_suffix(".homelab.tmp")
        temp.write_text(text)
        temp.chmod(path.stat().st_mode)
        temp.replace(path)
        for pyc in (path.parent / "__pycache__").glob(path.stem + ".*.pyc"):
            pyc.unlink()
    print("Verified site backports: vLLM #54282 and #58454")


if __name__ == "__main__":
    main()
