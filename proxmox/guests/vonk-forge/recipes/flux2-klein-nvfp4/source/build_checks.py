#!/usr/bin/env python3
"""Fail early and retain a bounded, inspectable dependency/CLI receipt in the image."""
import json
from pathlib import Path
import subprocess
import sys

import torch
import torchaudio
import torchvision

for module, expected in ((torch, "2.13.0"), (torchvision, "0.28.0"), (torchaudio, "2.11.0")):
    if module.__version__.split("+", 1)[0] != expected:
        raise RuntimeError(f"Declared version changed during dependency resolution: {module.__name__}={module.__version__}, expected {expected}")

sys.path.insert(0, "/opt/comfyui")
from comfy.cli_args import parser

checked_arguments = ["--user-directory", "/tmp", "--temp-directory", "/tmp", "--disable-auto-launch", "--disable-metadata"]
parser.parse_args(checked_arguments)
subprocess.run([sys.executable, "-m", "pip", "check"], check=True, timeout=120)
receipt = {
    "schema_version": 1,
    "comfyui_revision": "12d5279438bfefc058a269eae805ceab6047777f",
    "python": sys.version.split()[0],
    "torch": torch.__version__,
    "torchvision": torchvision.__version__,
    "torchaudio": torchaudio.__version__,
    "pip_check_passed": True,
    "comfyui_cli_checked_arguments": checked_arguments,
    "gpu_execution_qualified": False,
}
payload = json.dumps(receipt, sort_keys=True, indent=2) + "\n"
assert len(payload.encode()) <= 4096
Path("/opt/vonk/runtime-build-checks.json").write_text(payload)
print(payload, flush=True)
