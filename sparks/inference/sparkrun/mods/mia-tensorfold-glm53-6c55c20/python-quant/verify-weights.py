#!/usr/bin/env python3
"""Compare GLM target weights and matmuls to the installed TensorFold Python GPU implementation."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import struct

POINTER = ctypes.c_void_p
INT = ctypes.c_int
LONG = ctypes.c_longlong
AFFINE4 = 20
FLOAT8 = 24


def extract(binary, fatbin):
    blob = binary.read_bytes()
    magic = struct.pack("<I", 0xBA55ED50)
    start = blob.find(magic)
    while start >= 0:
        if len(blob) - start >= 16:
            _, version, header, size = struct.unpack_from("<IHHQ", blob, start)
            if version == 1 and header == 16 and 0 < size <= len(blob) - start - header:
                contents = blob[start:start + header + size]
                if b"glm_mm_quant20" in contents and b"glm_mm24_16" in contents:
                    fatbin.write_bytes(contents)
                    print(hashlib.sha256(contents).hexdigest())
                    return
        start = blob.find(magic, start + 4)
    raise ValueError(f"No production weight fatbin in {binary}")


class Kernels:
    def __init__(self, fatbin, torch):
        self.driver = ctypes.CDLL("libcuda.so.1")
        self.module = POINTER()
        self.contents = ctypes.create_string_buffer(fatbin.read_bytes())
        self.stream = POINTER(torch.cuda.current_stream().cuda_stream)
        self.torch = torch
        self.call("cuModuleLoadData", ctypes.byref(self.module), ctypes.cast(self.contents, POINTER))

    def call(self, name, *args):
        status = getattr(self.driver, name)(*args)
        if status:
            raise RuntimeError(f"{name} failed with CUDA status {status}")

    def launch(self, name, grid, block, parameters, shared_bytes=0):
        function = POINTER()
        self.call("cuModuleGetFunction", ctypes.byref(function), self.module, name.encode())
        if shared_bytes >= 48 * 1024:
            self.call("cuFuncSetAttribute", function, 8, shared_bytes)
        pointers = (POINTER * len(parameters))(*(ctypes.cast(ctypes.byref(arg), POINTER) for arg in parameters))
        self.call("cuLaunchKernel", function, *grid, block, 1, 1, shared_bytes, self.stream, pointers, None)
        self.torch.cuda.synchronize()


def addresses(*tensors):
    return [POINTER(t.data_ptr()) for t in tensors]


def check_matrix(weights, kind, kernels, qmm, torch):
    weights = weights.cuda().to(torch.bfloat16).contiguous()
    columns, width = weights.shape
    groups = columns * width // (64 if kind == AFFINE4 else 128)
    raw = torch.empty((columns, width // (2 if kind == AFFINE4 else 1)), device=weights.device, dtype=torch.uint8)
    metadata = torch.empty((columns, width // 64), device=weights.device, dtype=torch.int32)
    kernels.launch(f"glm_mm_quant{kind}", ((groups + 7) // 8, 1, 1), 256, addresses(weights, raw, metadata) + [LONG(groups)])
    if kind == AFFINE4:
        words, scales, biases = qmm.quantize4_mse_raw(weights)
        reference_codes = words.contiguous().view(torch.uint8)
        reference_meta = (scales.view(torch.int16).to(torch.int32) & 65535) | (biases.view(torch.int16).to(torch.int32) << 16)
        reference_values = qmm.dequantize(words, scales, biases)
    else:
        quantized = qmm.make_f8(weights)
        reference_codes = quantized.weight.view(torch.uint8)
        reference_meta = quantized.scale.repeat_interleave(2, dim=-1).view(torch.int32)
        reference_values = quantized.weight.float() * quantized.scale.repeat_interleave(128, dim=-1)
    code_errors = (raw != reference_codes).sum().item()
    metadata_errors = (metadata != reference_meta).sum().item()
    print(f"quantization {kind} {columns} {width} code bytes differing {code_errors} metadata groups differing {metadata_errors}", flush=True)
    if code_errors or metadata_errors:
        raise AssertionError("Quantized weights differ from TensorFold Python")
    tiled, tiled_meta = torch.empty_like(raw), torch.empty_like(metadata)
    chunks = (columns // 8) * (width // 64) * 32
    kernels.launch(f"glm_mm_tile{kind}", ((chunks + 255) // 256, 1, 1), 256,
                   addresses(raw, metadata, tiled, tiled_meta) + [INT(columns), INT(width)])
    for rows in (1, 8, 29, 63, 139):
        tile = next((size for size in (16, 32, 64) if size >= rows), 128)
        stages = 2 if rows >= 128 else 3
        name = f"glm_mm{kind}_{tile}" + ("s2" if stages == 2 else "")
        shared = stages * (tile * 128 + 8 * 32 * ((kind - 16) // 2) * 4 + 256)
        for splits in ((1, 2) if rows <= 64 else (1,)):
            x = torch.randn((rows, width), device=weights.device, dtype=torch.bfloat16)
            out = torch.full((rows, columns), float("nan"), device=weights.device, dtype=torch.float32)
            partials = torch.full((splits, rows, columns), float("nan"), device=weights.device, dtype=torch.float32)
            params = addresses(x) + [LONG(width)] + addresses(tiled, tiled_meta, out, partials)
            params.extend(INT(value) for value in (1, rows, columns, width, splits))
            kernels.launch(name, ((columns + 63) // 64, (rows + tile - 1) // tile, splits),
                           128 if rows <= 32 else 256, params, shared)
            got = out if splits == 1 else partials.sum(0)
            reference = x.float() @ reference_values.T
            error = (got - reference).abs().max().item() / max(reference.abs().max().item(), 1.0e-7)
            if not error < 1.0e-5:
                raise AssertionError(f"{kind} {rows} rows, {splits} splits: relative max error {error}")
    print(f"matmul PASS {kind} {columns} {width}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, help="extract a fatbin from the built executable without a GPU")
    parser.add_argument("--fatbin", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, help="also check real weights in the pinned checkpoint")
    args = parser.parse_args()
    if args.binary:
        extract(args.binary, args.fatbin)
        return
    import torch
    from tensorfold.families.glm5_next.cuda import qmm

    torch.cuda.set_device(0)
    torch.cuda.init()
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.manual_seed(679318)
    kernels = Kernels(args.fatbin, torch)
    for width in (512, 1536, 4096):
        weights = torch.randn(136, width, device="cuda", dtype=torch.bfloat16)
        weights[0] = 0
        weights[1] = 0.25
        weights[2] = torch.linspace(-1, 3, width, device="cuda")
        for kind in (AFFINE4, FLOAT8):
            check_matrix(weights, kind, kernels, qmm, torch)
    if args.model_dir:
        from safetensors import safe_open
        index = json.loads((args.model_dir / "model.safetensors.index.json").read_text())["weight_map"]
        suffixes = ("layers.0.self_attn.q_proj.weight", "layers.1.self_attn.o_proj.weight",
                    "layers.4.self_attn.q_b_proj.weight", "layers.3.mlp.shared_experts.down_proj.weight")
        names = ["lm_head.weight"] + [name for name in index if name.endswith(suffixes)]
        if len(names) < 3:
            raise ValueError("Expected multiple representative GLM projection weights")
        for name in names:
            print(f"checking {name}", flush=True)
            with safe_open(str(args.model_dir / index[name]), framework="pt", device="cpu") as shard:
                weights = shard.get_slice(name)[:512, :]
            check_matrix(weights, FLOAT8 if name == "lm_head.weight" else AFFINE4, kernels, qmm, torch)


if __name__ == "__main__":
    main()
