#!/usr/bin/env python3
"""Check baked dependencies and the real adapter interfaces without GPU inference."""
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

SOURCE = Path("/opt/vonk/source")
COMFY = Path("/opt/ComfyUI")
versions = {}
for name, distribution, expected in (
    ("torch", "torch", "2.9.1"),
    ("torchvision", "torchvision", "0.24.1"),
    ("torchaudio", "torchaudio", "2.9.1"),
    ("comfy_kitchen", "comfy-kitchen", "0.2.8"),
    ("triton", "triton", "3.5.1"),
):
    importlib.import_module(name)
    version = importlib.metadata.version(distribution)
    if version.split("+", 1)[0] != expected:
        raise RuntimeError(f"Unexpected installed {name}: {version}; expected {expected}")
    versions[name] = version
import torch
assert versions["torch"] == "2.9.1+cu130", versions["torch"]
assert torch.version.cuda == "13.0", torch.version.cuda
subprocess.run([sys.executable, "-m", "pip", "check"], check=True, timeout=120)

source_hashes = json.loads((SOURCE / "comfy-source-sha256.json").read_text())
for relative, expected in source_hashes.items():
    if hashlib.sha256((COMFY / relative).read_bytes()).hexdigest() != expected:
        raise RuntimeError(f"Core source differs from the reviewed image: {relative}")
sys.path.insert(0, str(COMFY))
import comfyui_version
assert comfyui_version.__version__ == "0.20.1"
from comfy.cli_args import parser, args
adapter = runpy.run_path(str(SOURCE / "comfyui_job.py"))
with tempfile.TemporaryDirectory(prefix="vonk-build-check-") as directory:
    temporary = Path(directory)
    for child in ("user", "temp", "output", "inputs"):
        (temporary / child).mkdir()
    adapter["server_command"].__globals__["INPUT_ROOT"] = temporary / "inputs"
    checked_arguments = adapter["server_command"](temporary)[2:]
    parsed = parser.parse_args(checked_arguments)
    assert parsed.disable_all_custom_nodes and parsed.disable_api_nodes
    assert parsed.database_url == "sqlite:///" + str(temporary / "comfyui.db")

# The build has no GPU device. CPU mode permits importing the real node classes
# and checking their schemas; no weights are loaded and no sampler is executed.
args.cpu = True
args.disable_all_custom_nodes = True
args.disable_api_nodes = True
args.use_pytorch_cross_attention = True
modules = [importlib.import_module(name) for name in (
    "nodes", "comfy_extras.nodes_custom_sampler", "comfy_extras.nodes_flux"
)]
workflow = json.loads((SOURCE / "workflows/flux-2-klein-4b.json").read_text())
checked_nodes = []
for node in workflow["prompt"].values():
    name = node["class_type"]
    matches = [getattr(module, name) for module in modules if hasattr(module, name)]
    assert len(matches) == 1, (name, len(matches))
    cls = matches[0]
    if hasattr(cls, "define_schema"):
        fields = {field.id for field in cls.define_schema().inputs}
    else:
        fields = set().union(*(group.keys() for group in cls.INPUT_TYPES().values()))
    assert set(node["inputs"]) <= fields, (name, set(node["inputs"]) - fields)
    checked_nodes.append(name)
subprocess.run([sys.executable, str(SOURCE / "comfyui_job.py"), "--help"], check=True, timeout=10)
receipt = {
    "schema_version": 1,
    "base_image_digest": "7fda74d7af1d86455bfa58df5d36e761964017c7bce6f5d2f3564ba0b2deee3a",
    "comfyui_revision": "fce0398470fe3ecdb7ab4c5c69555ad0fcbdc09e",
    "comfyui_version": comfyui_version.__version__,
    "python": sys.version.split()[0],
    "versions": versions,
    "torch_cuda": torch.version.cuda,
    "pip_check_passed": True,
    "core_source_hashes_passed": True,
    "adapter_cli_passed": True,
    "workflow_node_schemas": sorted(set(checked_nodes)),
    "node_import_device": "cpu",
    "gpu_execution_qualified": False,
}
payload = json.dumps(receipt, sort_keys=True, indent=2) + "\n"
assert len(payload.encode()) <= 4096
Path("/opt/vonk/runtime-build-checks.json").write_text(payload)
print(payload, flush=True)
