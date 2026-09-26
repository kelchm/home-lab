#!/usr/bin/env python3
"""Fail closed if the digest-pinned Mia EXL3 image is incomplete."""

from pathlib import Path


required = (
    "/opt/glm53/chat_template.jinja",
    "/opt/glm53/dflash2_speculator.py",
    "/opt/glm53/qwen3_dflash2.py",
    "/opt/glm53/patch_dflash2.py",
    "/opt/glm53/patch_glm_eagle3.py",
    "/opt/glm53/patch_glm5_drafter_group.py",
    "/opt/glm53/patch_glm_video_placeholders.py",
    "/opt/glm53/patch_hybrid_prefix_hit.py",
    "/opt/glm53/patch_kpool_tail_slotmap.py",
    "/opt/glm53/patch_scheduler_decode_floor.py",
    "/opt/glm53/patch_suppress_stops_in_reasoning.py",
    "/opt/glm53/patch_xgrammar_termination.py",
    "/opt/glm53/test_kpool_tail_slotmap.py",
    "/usr/local/lib/python3.12/dist-packages/vllm/model_executor/layers/quantization/exl3.py",
)
missing = [path for path in required if not Path(path).is_file()]
if missing:
    raise SystemExit(f"incomplete Mia EXL3 runtime: {missing}")
print("Mia GLM 5.3 EXL3 DFlash2 runtime contract OK")
