#!/usr/bin/env python3
"""Bind Controller placement to MiaAI-Lab's native-mp EXL3 TP2 runtime."""

from __future__ import annotations

import json
import os
import sys
from ipaddress import ip_address
from pathlib import Path


TARGET = Path("/models/target")
DRAFTER = Path("/models/drafter")
CAPTURE_SIZES = ("1", "2", "4", "8", "16", "24", "32")


def _value(arguments: list[str], option: str) -> str | None:
    if option not in arguments:
        return None
    index = arguments.index(option)
    if index + 1 >= len(arguments):
        raise SystemExit(f"{option} requires a value")
    return arguments[index + 1]


def _require_value(arguments: list[str], option: str, expected: str) -> None:
    value = _value(arguments, option)
    if value != expected:
        raise SystemExit(f"{option} must be {expected!r}, got {value!r}")


arguments = sys.argv[1:]
node_count = _value(arguments, "--nnodes")
node_rank = _value(arguments, "--node-rank")
mechanism = _value(arguments, "--distributed-executor-backend")
headless = "--headless" in arguments

local_address = os.environ.get("VONK_LOCAL_ADDR")
master_address = os.environ.get("VONK_MASTER_ADDR")
master_port = os.environ.get("VONK_MASTER_PORT")
fabric_names = (
    "NCCL_SOCKET_IFNAME",
    "NCCL_IB_HCA",
    "NCCL_IB_GID_INDEX",
    "TP_SOCKET_IFNAME",
    "GLOO_SOCKET_IFNAME",
)
try:
    if (
        mechanism != "mp"
        or node_count != "2"
        or node_rank not in {"0", "1"}
        or headless != (node_rank == "1")
        or not local_address
        or not master_address
        or not master_port
        or any(not os.environ.get(name) for name in fabric_names)
        or not os.environ["NCCL_IB_GID_INDEX"].isascii()
        or not os.environ["NCCL_IB_GID_INDEX"].isdigit()
        or not master_port.isascii()
        or not master_port.isdigit()
        or not 1024 <= int(master_port) <= 65535
    ):
        raise ValueError
    ip_address(local_address)
    ip_address(master_address)
except ValueError:
    raise SystemExit(
        "EXL3 TP2 requires exact Controller rendezvous, ranks, and fabric"
    ) from None

if str(TARGET) not in arguments:
    raise SystemExit("the immutable /models/target checkpoint argument is required")
for path in (TARGET / "config.json", DRAFTER / "config.json", DRAFTER / "model.safetensors"):
    if not path.is_file():
        raise SystemExit(f"immutable model artifact is missing: {path}")

if "--quantization" in arguments:
    raise SystemExit("EXL3 quantization is owned by the pinned runtime")
if "--chat-template" in arguments:
    raise SystemExit("the GLM 5.3 chat template is owned by the pinned runtime")
_require_value(arguments, "--kv-cache-dtype", "fp8")
context = _value(arguments, "--max-model-len")
if context is None or not context.isascii() or not context.isdigit() or not 4096 <= int(context) <= 1000000:
    raise SystemExit("--max-model-len must be an integer from 4096 through 1000000")
_require_value(arguments, "--gpu-memory-utilization", "0.80")
sequences = _value(arguments, "--max-num-seqs")
if sequences not in {"1", "2", "3", "4"}:
    raise SystemExit("--max-num-seqs must be an integer from 1 through 4")
_require_value(arguments, "--max-num-batched-tokens", "2048")

speculative = _value(arguments, "--speculative-config")
try:
    specification = json.loads(speculative or "")
except json.JSONDecodeError as error:
    raise SystemExit("--speculative-config must contain JSON") from error
expected_specification = {
    "method": "dflash",
    "model": str(DRAFTER),
    "num_speculative_tokens": 7,
    "kv_cache_dtype": "auto",
    "draft_tensor_parallel_size": 2,
    "draft_sample_method": "probabilistic",
    "rejection_sample_method": "standard",
}
if specification != expected_specification:
    raise SystemExit("the exact DFlash2 K7 specification is required")

if "--cudagraph-capture-sizes" in arguments:
    raise SystemExit("CUDA graph capture sizes are owned by the pinned runtime")
arguments.extend(("--cudagraph-capture-sizes", *CAPTURE_SIZES))
arguments.extend(("--quantization", "exl3"))
arguments.extend(("--chat-template", "/opt/glm53/chat_template.jinja"))
arguments.extend(("--master-addr", master_address, "--master-port", master_port))

os.environ["VLLM_HOST_IP"] = local_address
os.environ["MASTER_ADDR"] = master_address
os.environ["MASTER_PORT"] = master_port

vllm = next(
    (
        candidate
        for candidate in ("/usr/local/bin/vllm", "/opt/vllm/.venv/bin/vllm")
        if Path(candidate).is_file() and os.access(candidate, os.X_OK)
    ),
    None,
)
if vllm is None:
    raise SystemExit("the pinned vLLM executable is missing")
os.execv(vllm, (vllm, *arguments))
