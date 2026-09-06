#!/usr/bin/env python3
"""Bind the pinned Mia TP2 runtime to Vonk placement and immutable artifacts."""

from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE = Path("/opt/vonk/source")
MODEL = Path("/models")
OUTPUTS = Path("/outputs")
sys.dont_write_bytecode = True


def value(arguments: list[str], option: str) -> str:
    if arguments.count(option) != 1:
        raise ValueError(f"Expected exactly one {option}")
    index = arguments.index(option)
    if index + 1 >= len(arguments):
        raise ValueError(f"Missing value for {option}")
    return arguments[index + 1]


def require(arguments: list[str], option: str, expected: str) -> None:
    if value(arguments, option) != expected:
        raise ValueError(f"{option} must be {expected}")


def validate(arguments: list[str], environment: dict[str, str]) -> tuple[str, str, str]:
    if arguments[:2] != ["serve", "/models"]:
        raise ValueError("The pinned /models artifact must be the model argument")
    require(arguments, "--distributed-executor-backend", "mp")
    require(arguments, "--nnodes", "2")
    require(arguments, "--tensor-parallel-size", "2")
    require(arguments, "--gpu-memory-utilization", "0.75")
    require(arguments, "--kv-cache-dtype", "auto")
    rank = value(arguments, "--node-rank")
    if rank not in {"0", "1"} or ("--headless" in arguments) != (rank == "1"):
        raise ValueError("Controller rank and headless role disagree")
    for name, low, high in (("--max-model-len", 4096, 262144), ("--max-num-seqs", 1, 4)):
        number = value(arguments, name)
        if not number.isascii() or not number.isdigit() or not low <= int(number) <= high:
            raise ValueError(f"{name} is outside its bounded evaluation range")
    if json.loads(value(arguments, "--speculative-config")) != {"method": "mtp", "num_speculative_tokens": 3}:
        raise ValueError("The initial MTP3 full-vocabulary profile is required")
    fixed = ("--enable-expert-parallel", "--all2all-backend", "--load-format", "--safetensors-load-strategy", "--hf-overrides", "--master-addr", "--master-port")
    if any(option in arguments for option in fixed):
        raise ValueError("A wrapper-owned launch option was supplied externally")
    for name in ("NCCL_SOCKET_IFNAME", "NCCL_IB_HCA", "NCCL_IB_GID_INDEX", "GLOO_SOCKET_IFNAME", "TP_SOCKET_IFNAME"):
        if not environment.get(name):
            raise ValueError(f"Controller fabric binding missing: {name}")
    local = environment["VONK_LOCAL_ADDR"]
    master = environment["VONK_MASTER_ADDR"]
    port = environment["VONK_MASTER_PORT"]
    ipaddress.ip_address(local)
    ipaddress.ip_address(master)
    if not port.isascii() or not port.isdigit() or not 1024 <= int(port) <= 65535:
        raise ValueError("Controller rendezvous port is invalid")
    return local, master, port


def checkpoint_view(model: Path, view: Path, source: Path) -> Path:
    expected = json.loads((source / "checkpoint-config-sha256.json").read_text())
    for name, digest in expected.items():
        path = model / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Pinned model config does not match: {name}")
    view.mkdir(parents=True, exist_ok=False)
    for path in model.iterdir():
        if path.name == ".vonk-manifest.json":
            continue
        if not path.is_file():
            raise ValueError(f"Unexpected checkpoint entry: {path.name}")
        (view / path.name).symlink_to(path)
    module_path = source / "upstream/files/patch_checkpoint_config.py"
    specification = importlib.util.spec_from_file_location("mia_checkpoint_patch", module_path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    overlay = view.parent / "checkpoint-overlays"
    overlay.mkdir(exist_ok=False)
    module.main(str(model), str(overlay))
    for name, patched_name in (("config.json", "config_patched.json"), ("hf_quant_config.json", "hf_quant_config_patched.json")):
        patched = overlay / patched_name
        if not patched.is_file():
            raise ValueError(f"Pinned checkpoint did not produce expected MTP alias overlay: {name}")
        (view / name).unlink()
        shutil.copyfile(patched, view / name)
    config = json.loads((view / "config.json").read_text())
    config["text_config"]["ple_embedding_dtype"] = "float8_e4m3fn"
    (view / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    if module.mtp_moe_algo(str(view)) != "FP8_BLOCK_SCALES":
        raise ValueError("Pinned MTP experts do not declare FP8_BLOCK_SCALES")
    print("VONK_CHECKPOINT_VIEW " + json.dumps({"config_sha256": hashlib.sha256((view / "config.json").read_bytes()).hexdigest(), "hf_quant_config_sha256": hashlib.sha256((view / "hf_quant_config.json").read_bytes()).hexdigest()}), flush=True)
    return view


def main() -> None:
    arguments = sys.argv[1:]
    local, master, port = validate(arguments, dict(os.environ))
    # Outputs survive some restart paths; each exec gets fresh small overlays.
    launch_root = Path(tempfile.mkdtemp(prefix="vonk-checkpoint-", dir=OUTPUTS))
    view = checkpoint_view(MODEL, launch_root / "model", SOURCE)
    arguments[1] = str(view)
    arguments.extend(("--enable-expert-parallel", "--all2all-backend", "allgather_reducescatter", "--load-format", "safetensors", "--safetensors-load-strategy", "lazy", "--master-addr", master, "--master-port", port))
    os.environ.update(VLLM_HOST_IP=local, MASTER_ADDR=master, MASTER_PORT=port)
    executable = "/usr/local/bin/vllm"
    if not Path(executable).is_file():
        raise ValueError("Pinned vLLM executable is absent")
    os.execv(executable, [executable, *arguments])


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError) as error:
        raise SystemExit(str(error)) from error
