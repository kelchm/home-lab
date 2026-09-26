#!/usr/bin/env python3
"""Correct one proven NVIDIA installed-wheel tag inside this pinned ARM64 image.

No wheel or executable is repackaged. Every original package byte is verified
before changing only WHEEL and its RECORD row. See CUSPARSELT-CORRECTION.md.
"""
import base64
import csv
import ctypes
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import sysconfig

DIST = "nvidia_cusparselt_cu13-0.8.0.dist-info/"
WHEEL = DIST + "WHEEL"
RECORD = DIST + "RECORD"
LIBRARY = "nvidia/cusparselt/lib/libcusparseLt.so.0"
SITE = Path("/opt/venv/lib/python3.12/site-packages")
SOURCE = Path(__file__).resolve().parent
EXPECTED_ERROR = "nvidia-cusparselt-cu13 0.8.0 is not supported on this platform"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_bytes(data, expected, name):
    require(len(data) == expected["bytes"], f"Unexpected size: {name}")
    require(hashlib.sha256(data).hexdigest() == expected["sha256"], f"Unexpected SHA-256: {name}")


def verify_file(path, expected):
    require(path.is_file() and not path.is_symlink(), f"Expected regular file: {path}")
    require(path.stat().st_size == expected["bytes"], f"Unexpected size: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    require(digest.hexdigest() == expected["sha256"], f"Unexpected SHA-256: {path}")


def record_rows(raw, files):
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))
    require(all(len(row) == 3 for row in rows), "Malformed RECORD")
    require(len(rows) == len(files) + 1, "Unexpected RECORD row count")
    require(len({row[0] for row in rows}) == len(rows), "Duplicate RECORD path")
    require({row[0] for row in rows} == set(files) | {RECORD}, "Unexpected RECORD paths")
    for name, digest, size in rows:
        if name == RECORD:
            require(digest == size == "", "RECORD self-entry must be unhashed")
            continue
        expected = files[name]
        encoded = base64.urlsafe_b64encode(bytes.fromhex(expected["sha256"])).decode().rstrip("=")
        require(digest == "sha256=" + encoded and size == str(expected["bytes"]), f"RECORD mismatch: {name}")
    return rows


def corrected_bytes(wheel, record, provenance):
    verify_bytes(wheel, provenance["files_before"][WHEEL], WHEEL)
    verify_bytes(record, provenance["record_before"], RECORD)
    rows = record_rows(record, provenance["files_before"])
    old = ("Tag: " + provenance["old_tag"] + "\n").encode()
    new = ("Tag: " + provenance["new_tag"] + "\n").encode()
    require(wheel.count(old) == 1, "Expected one exact original WHEEL tag")
    wheel_after = wheel.replace(old, new)
    verify_bytes(wheel_after, provenance["wheel_after"], "corrected WHEEL")
    digest = base64.urlsafe_b64encode(hashlib.sha256(wheel_after).digest()).decode().rstrip("=")
    for row in rows:
        if row[0] == WHEEL:
            row[1:] = ["sha256=" + digest, str(len(wheel_after))]
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(rows)
    record_after = output.getvalue().encode()
    verify_bytes(record_after, provenance["record_after"], "corrected RECORD")
    record_rows(record_after, dict(provenance["files_before"], **{WHEEL: provenance["wheel_after"]}))
    return wheel_after, record_after


def validate_platform(system, machine, normalized_platform, glibc, supported_tags, provenance):
    require(system == "Linux" and machine == "aarch64", "Only Linux aarch64 is supported")
    require(normalized_platform == "linux_aarch64", "Unexpected Python platform")
    require(tuple(map(int, glibc.split("."))) >= (2, 27), "cuSPARSELt requires glibc >= 2.27")
    require(provenance["new_tag"] in supported_tags, "Corrected platform tag is unsupported")
    require(provenance["old_tag"] not in supported_tags, "Unexpected original platform support")


def check_pip(result, before):
    lines = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines() if line.strip()]
    if before:
        require(result.returncode == 1 and lines == [EXPECTED_ERROR], f"Unexpected pre-correction pip check: {result.returncode}: {lines}")
    else:
        require(result.returncode == 0, f"Full post-correction pip check failed: {lines}")
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def pip_check():
    return subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True, timeout=120)


def main():
    provenance = json.loads((SOURCE / "cusparselt-provenance.json").read_text())
    require(Path(sys.prefix).resolve() == Path("/opt/venv"), "Expected pinned image venv")
    libc = ctypes.CDLL(None)
    libc.gnu_get_libc_version.restype = ctypes.c_char_p
    glibc = libc.gnu_get_libc_version().decode("ascii")
    from pip._vendor.packaging.tags import sys_tags
    supported_tags = {str(tag) for tag in sys_tags()}
    normalized_platform = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    validate_platform(platform.system(), platform.machine(), normalized_platform, glibc, supported_tags, provenance)
    require(importlib.metadata.version("nvidia-cusparselt-cu13") == "0.8.0", "Unexpected cuSPARSELt version")
    require(importlib.metadata.version("torch") == "2.9.1+cu130", "Unexpected Torch version")
    torch_metadata = SITE / "torch-2.9.1+cu130.dist-info/METADATA"
    require(hashlib.sha256(torch_metadata.read_bytes()).hexdigest() == provenance["torch_metadata_sha256"], "Torch dependency metadata changed")
    require('nvidia-cusparselt-cu13==0.8.0; platform_system == "Linux"' in importlib.metadata.requires("torch"), "Expected Torch dependency missing")
    for name, expected in provenance["files_before"].items():
        verify_file(SITE / name, expected)
    verify_file(SITE / RECORD, provenance["record_before"])
    wheel_before, record_before = (SITE / WHEEL).read_bytes(), (SITE / RECORD).read_bytes()
    wheel_after, record_after = corrected_bytes(wheel_before, record_before, provenance)
    library = SITE / LIBRARY
    with library.open("rb") as stream:
        header = stream.read(64)
    require(header[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", header, 18)[0] == 183, "Expected ELF64 little-endian AArch64 library")
    import torch
    require(torch.__version__ == "2.9.1+cu130" and torch.version.cuda == "13.0", "Unexpected actual Torch/CUDA import")
    mapped = {Path(line.split()[-1]).resolve() for line in Path("/proc/self/maps").read_text().splitlines() if "libcusparseLt.so" in line}
    require(mapped == {library.resolve()}, f"Torch loaded an unexpected cuSPARSELt library: {mapped}")
    loaded = ctypes.CDLL(str(library), mode=os.RTLD_NOW | ctypes.RTLD_GLOBAL)
    require(loaded._handle != 0, "cuSPARSELt dynamic-loader check failed")
    before_check = check_pip(pip_check(), before=True)

    # Every compatibility, integrity and pre-check gate above precedes both writes.
    # A failed Dockerfile layer is discarded; the immutable parent stays untouched.
    (SITE / WHEEL).write_bytes(wheel_after)
    (SITE / RECORD).write_bytes(record_after)
    expected_after = dict(provenance["files_before"], **{WHEEL: provenance["wheel_after"]})
    for name, expected in expected_after.items():
        verify_file(SITE / name, expected)
    verify_file(SITE / RECORD, provenance["record_after"])
    record_rows((SITE / RECORD).read_bytes(), expected_after)
    after_check = check_pip(pip_check(), before=False)
    receipt = {
        "schema_version": 1,
        "scope": "installed metadata in one pinned image; no repackaged wheel or binary change",
        "base_image_digest": provenance["base_image_digest"],
        "official_wheel": provenance["official_wheel"],
        "library_sha256": provenance["files_before"][LIBRARY]["sha256"],
        "library_path_loaded_by_torch": str(library),
        "elf_machine": 183,
        "platform": normalized_platform,
        "glibc": glibc,
        "old_tag": provenance["old_tag"],
        "new_tag": provenance["new_tag"],
        "wheel_before": provenance["files_before"][WHEEL],
        "wheel_after": provenance["wheel_after"],
        "record_before": provenance["record_before"],
        "record_after": provenance["record_after"],
        "all_package_hashes_and_record_verified": True,
        "pip_check_before": before_check,
        "pip_check_after": after_check,
        "gpu_execution_qualified": False,
    }
    payload = json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    require(len(payload.encode()) <= 8192, "Correction receipt too large")
    Path("/opt/vonk/cusparselt-metadata-correction.json").write_text(payload)
    print(payload, flush=True)


if __name__ == "__main__":
    main()
