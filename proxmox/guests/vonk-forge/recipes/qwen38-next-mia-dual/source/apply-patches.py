#!/usr/bin/env python3
"""Apply unchanged Mia source patches to the exact pinned image at build time."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    expected = json.loads((root / "upstream-sha256.json").read_text())
    for relative, digest in expected.items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Pinned upstream source mismatch: {relative}")
    specification = importlib.util.find_spec("vllm")
    if specification is None or not specification.submodule_search_locations:
        raise SystemExit("Pinned vLLM package is absent")
    package = Path(next(iter(specification.submodule_search_locations)))
    files = root / "upstream/files"
    targets = {
        "ple_layer_patched.py": package / "models/qwen3_8_flash_next/nvidia/ple_layer.py",
        "modelopt_patched.py": package / "model_executor/layers/quantization/modelopt.py",
    }
    before = {}
    for name, target in targets.items():
        before[str(target.relative_to(package))] = hashlib.sha256(target.read_bytes()).hexdigest()
        shutil.copyfile(target, files / (name + ".orig"))
    for script in ("patch_ple_layer.py", "patch_modelopt_mxfp8.py", "patch_modelopt_fp8_block_moe.py"):
        subprocess.run([sys.executable, str(files / script)], check=True)
    after = {}
    for name, target in targets.items():
        payload = (files / name).read_bytes()
        ast.parse(payload, filename=name)
        shutil.copyfile(files / name, target)
        after[str(target.relative_to(package))] = hashlib.sha256(payload).hexdigest()
    report = {"base_image": "sha256:3b0e188ffceb3d07e09c3cb5215433a0020eacf02d7f882ed3a8bfd15454477e", "upstream_revision": "c2325b22602b51a5faf55fc2bebccc34f3f80b9f", "before": before, "after": after}
    (root / "applied-patches.json").write_text(json.dumps(report, indent=2) + "\n")
    print("VONK_PATCH_RECEIPT " + json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
