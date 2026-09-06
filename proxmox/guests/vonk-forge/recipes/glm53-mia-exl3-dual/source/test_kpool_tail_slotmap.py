#!/usr/bin/env python3
"""Regression tests for the K-pool tail one-block circular slot-map clamp."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
PATCH = HERE / "patch_kpool_tail_slotmap.py"
sys.path.insert(0, str(HERE))
from patch_kpool_tail_slotmap import (  # noqa: E402
    ANCHOR,
    MARK,
    circular_slot_ids,
    count_overruns,
    prepare,
    verified_state,
)

PINNED_FIXTURE = '''import triton
import triton.language as tl


@triton.jit
def _compute_slot_mapping_kernel(
    num_tokens,
    max_num_tokens,
    query_start_loc_ptr,
    positions_ptr,
    block_table_ptr,
    block_table_stride,
    block_size,
    slot_mapping_ptr,
    KV_CACHE_BLOCK_SIZE: tl.constexpr,
    BLOCKS_PER_KV_BLOCK: tl.constexpr,
    TOTAL_CP_WORLD_SIZE: tl.constexpr,
    TOTAL_CP_RANK: tl.constexpr,
    CP_KV_CACHE_INTERLEAVE_SIZE: tl.constexpr,
    PAD_ID: tl.constexpr,
    BLOCK_SIZE: tl.constexpr,
):
    req_idx = tl.program_id(0)
    start_idx = tl.load(query_start_loc_ptr + req_idx).to(tl.int64)
    end_idx = tl.load(query_start_loc_ptr + req_idx + 1).to(tl.int64)
    virtual_block_size = KV_CACHE_BLOCK_SIZE * TOTAL_CP_WORLD_SIZE
    row_offset = req_idx * block_table_stride
    for i in range(start_idx, end_idx, BLOCK_SIZE):
        offsets = i + tl.arange(0, BLOCK_SIZE)
        mask = offsets < end_idx
        pos = tl.load(positions_ptr + offsets, mask=mask, other=0)
        virtual_block_indices = pos // virtual_block_size
        virtual_block_offsets = pos - virtual_block_indices * virtual_block_size
        is_local = (
            virtual_block_offsets // CP_KV_CACHE_INTERLEAVE_SIZE
        ) % TOTAL_CP_WORLD_SIZE == TOTAL_CP_RANK
        local_block_offsets = (
            virtual_block_offsets // (TOTAL_CP_WORLD_SIZE * CP_KV_CACHE_INTERLEAVE_SIZE)
        ) * CP_KV_CACHE_INTERLEAVE_SIZE + (
            virtual_block_offsets % CP_KV_CACHE_INTERLEAVE_SIZE
        )
''' + ANCHOR


def _run_patch(target: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GLM53_BLOCK_TABLE_PY"] = str(target)
    return subprocess.run(
        [sys.executable, str(PATCH)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_circular_math() -> None:
    row = [17]
    positions = list(range(64))
    assert count_overruns(positions, block_size=4, stride=1) == 60
    patched = circular_slot_ids(positions, row, 4, clamp=True)
    assert patched[:4] == [68, 69, 70, 71]
    assert patched[4:8] == patched[:4]
    assert patched[60:64] == patched[:4]
    assert circular_slot_ids(positions[:4], row, 4, clamp=False) == patched[:4]
    try:
        circular_slot_ids([4], row, 4, clamp=False)
    except IndexError:
        pass
    else:
        raise AssertionError("unpatched mapping must IndexError at pos >= block_size")

    wide = list(range(10))
    valid_positions = [0, 63, 64, 639]
    assert count_overruns(valid_positions, block_size=64, stride=10) == 0
    assert circular_slot_ids(valid_positions, wide, 64, clamp=True) == (
        circular_slot_ids(valid_positions, wide, 64, clamp=False)
    )


def test_fixture() -> None:
    with tempfile.TemporaryDirectory() as raw:
        target = Path(raw) / "block_table.py"
        target.write_text(PINNED_FIXTURE)
        first = _run_patch(target)
        assert first.returncode == 0, first.stderr
        text = target.read_text()
        assert verified_state(text)
        assert MARK in text
        second = _run_patch(target)
        assert second.returncode == 0, second.stderr
        assert second.stdout.count("already present") == 1
        again, action = prepare(text)
        assert action == "already present"
        assert again == text


def test_fail_closed() -> None:
    drifted = PINNED_FIXTURE.replace(
        "local_block_offsets // block_size",
        "local_block_offsets // kernel_block_size",
        1,
    )
    with tempfile.TemporaryDirectory() as raw:
        target = Path(raw) / "block_table.py"
        target.write_text(drifted)
        result = _run_patch(target)
        assert result.returncode != 0
        assert "preflight failed" in result.stderr
        assert target.read_text() == drifted

    partial = PINNED_FIXTURE.replace(ANCHOR, MARK + ANCHOR, 1)
    with tempfile.TemporaryDirectory() as raw:
        target = Path(raw) / "block_table.py"
        target.write_text(partial)
        result = _run_patch(target)
        assert result.returncode != 0
        assert "partial/inconsistent" in result.stderr


def main() -> int:
    test_circular_math()
    test_fixture()
    test_fail_closed()
    print("kpool tail slot-map patch OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
