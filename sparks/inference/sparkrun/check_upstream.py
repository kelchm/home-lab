#!/usr/bin/env python3
"""Check critical recipe parity against a reviewed upstream checkout (requires PyYAML).

This complements the manual audit and live/checkpoint checks; it does not claim
that static YAML can prove image contents, checkpoint bytes, or runtime defaults.
"""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path

import yaml

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("upstream", type=Path)
parser.add_argument("--recipe", type=Path, default=Path(__file__).with_name("mia-glm53-exl3.yaml"))
args = parser.parse_args()
r = yaml.safe_load(args.recipe.read_text())
start = (args.upstream / "start.sh").read_text()
up = {}
# Literal defaults only. Dynamic cases are checked explicitly below.
for key, value in re.findall(r'^([A-Z][A-Z0-9_]*)="\$\{[A-Z0-9_]+(?::-|-)([^{}$]*)\}"$', start, re.M):
    up.setdefault(key, value)
for line in (args.upstream / ".env.example").read_text().splitlines():
    if re.match(r"^[A-Z][A-Z0-9_]*=", line):
        words = shlex.split(line, comments=True)
        if words:
            key, value = words[0].split("=", 1)
            up[key] = value
errors = []
checks = 0

def check(name, actual, expected):
    global checks
    checks += 1
    if actual != expected:
        errors.append(f"{name}: got {actual!r}, expected {expected!r}")

head = subprocess.check_output(["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True).strip()
check("reviewed upstream commit", head, r["metadata"]["overlay_source_revision"])
for field, key in {"port":"PORT", "served_model_name":"SERVED_MODEL_NAME", "tensor_parallel":"TP", "gpu_memory_utilization":"GPU_MEM_UTIL", "max_model_len":"MAX_MODEL_LEN", "max_num_seqs":"MAX_NUM_SEQS", "max_num_batched_tokens":"MAX_NUM_BATCHED_TOKENS", "quantization":"QUANTIZATION", "load_format":"LOAD_FORMAT", "kv_cache_dtype":"KV_CACHE_DTYPE"}.items():
    check(field, str(r["defaults"][field]), up[key])
# Memory overrides are deliberate and backed by benchmark receipts.
for key, expected in {"NCCL_MIN_NCHANNELS":"8", "NCCL_MAX_NCHANNELS":"8", "VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS":"0"}.items():
    check(key, str(r["env"].get(key)), expected)
for key in r["env"]:
    if key in up and key not in {"TILELANG_CACHE_DIR"}:
        check(key, str(r["env"][key]), up[key])
check("model id", r["model"], "Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw")
check("native target pin", r["model_revision"], up["MODEL_REVISION"])
check("serve target pin", r["defaults"]["revision"], up["MODEL_REVISION"])
check("native models", r["distribution_config"]["models"]["entries"], [
    {"name": r["model"], "revision": up["MODEL_REVISION"], "target": [-1]},
    {"name": "incoai/GLM-5.3-Flash-DFlash2", "revision": up["DFLASH_REVISION"], "target": [-1]},
])
check("InstantTensor buffer", r["env"]["INSTANTTENSOR_BUFFER_SIZE"], "2147483648")
check("KV pool bytes", r["defaults"]["kv_cache_memory_bytes"], 15032385536)
lock = json.loads((args.recipe.parent / "mods/mia-glm53/upstream.lock.json").read_text())
check("mod source pin", lock["revision"], head)
for name, digest in lock["files"].items():
    check(f"mod checksum {name}", hashlib.sha256((args.upstream / name).read_bytes()).hexdigest(), digest)
check("target revision", r["metadata"]["target_model_revision"], up["MODEL_REVISION"])
check("draft revision", r["metadata"]["draft_model_revision"], up["DFLASH_REVISION"])
spec = json.loads(r["defaults"]["local_speculative_config"])
check("speculator", spec["method"], up["SPEC_METHOD"])
check("draft length", str(spec["num_speculative_tokens"]), up["DFLASH_TOKENS"])
check("draft TP", str(spec["draft_tensor_parallel_size"]), up["DFLASH_DRAFT_TP"])
check("draft path revision", Path(spec["model"]).name, up["DFLASH_REVISION"])
command = r["command"]
for key, value in r["defaults"].items():
    command = command.replace("{" + key + "}", str(value))
tokens = shlex.split(command.replace("\\\n", " "))
def flag(name):
    return tokens[tokens.index(name)+1] if name in tokens else None
check("MM limit", json.loads(flag("--limit-mm-per-prompt")), json.loads(up["LIMIT_MM"]))
check("MM cache", flag("--mm-processor-cache-gb"), up["MM_PROCESSOR_CACHE_GB"])
check("image token limit", json.loads(flag("--mm-processor-kwargs"))["max_image_tokens"], int(up["MM_IMAGE_TOKENS"]))
for flag_name in ["--enable-auto-tool-choice", "--enable-prefix-caching", "--no-enable-flashinfer-autotune", "--skip-mm-profiling"]:
    check(flag_name, flag_name in tokens, True)
check("serve revision flag", flag("--revision"), up["MODEL_REVISION"])
check("serve KV cap", flag("--kv-cache-memory-bytes"), "15032385536")
check("tool parser", flag("--tool-call-parser"), "glm47")
check("reasoning parser", flag("--reasoning-parser"), "glm45")
a = start.index("    patch_glm_video_placeholders.py\n")
b = start.index("\n)", a)
patches = re.findall(r"^    (patch_\w+\.py)$", start[a:b], re.M)
check("patch sequence", [Path(x).name for x in json.loads((args.recipe.parent / "mods/mia-glm53/upstream.lock.json").read_text())["patches"]], patches)
check("TileLang persistence", r["env"].get("TILELANG_CACHE_DIR"), "/cache/runtime/tilelang")
check("post-ready warmup", any("boot-shape-warmup.sh" in x for x in r.get("post_exec", [])), True)
check("upstream IPC", r["executor_config"].get("ipc"), "host")
check("upstream privileges", r["executor_config"].get("privileged"), False)
check("upstream capabilities", r["executor_config"]["cap_add"], ["IPC_LOCK"])
if errors:
    raise SystemExit("\n".join(errors))
print(f"PASS: {checks} parity checks against {head}")
