#!/usr/bin/env python3
"""Map SparkRun's rank environment to the dsv41 TensorFold TP2 engine: rank 0 serves the API, rank 1 runs the lanes.

The weights, kit and Engram tables are read-only HF snapshots, so each rank first links three views under /tmp: the
weights with this mod's chat template, the kit with the 1017 image routing bias made from the weights, and the
Engram shards with their embed-only index.
"""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

SITE = Path("/opt/dsv41-tensorfold")
VIEWS = Path("/tmp/dsv41")
HUB = Path("/cache/huggingface/hub")


def snapshot(repo, revision):
    return HUB / ("models--" + repo.replace("/", "--")) / "snapshots" / revision


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def link_view(src, dst, skip=()):
    dst.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src.iterdir()):
        if entry.name not in skip:
            (dst / entry.name).symlink_to(entry)


def views(lock, model_path):
    shutil.rmtree(VIEWS, ignore_errors=True)
    model, kit, engram = VIEWS / "model", VIEWS / "kit", VIEWS / "engram"
    link_view(Path(model_path), model, skip={"chat_template.jinja"})
    (model / "chat_template.jinja").symlink_to(SITE / "chat_template.jinja")

    kit_src = snapshot(lock["kit"]["repository"], lock["kit"]["revision"]) / "kit"
    link_view(kit_src, kit, skip={"vision"})
    link_view(kit_src / "vision", kit / "vision", skip={"gate_bias_vl.safetensors"})
    bias = kit / "vision" / "gate_bias_vl.safetensors"
    subprocess.run(["python3", str(kit_src / "tools" / "make_bias_vl.py"), str(model), str(bias)], check=True,
                   stdout=subprocess.DEVNULL)
    if sha256(bias) != lock["kit"]["bias_vl_sha256"]:
        raise SystemExit(f"{bias}: the image routing bias made from the weights does not match the lock")

    engram_src = snapshot(lock["engram"]["repository"], lock["engram"]["revision"])
    engram.mkdir(parents=True)
    for name in lock["engram"]["files"]:
        if not (engram_src / name).exists():
            raise SystemExit(f"{engram_src / name} is missing: stage the Engram shards (README, Engram tables)")
        (engram / name).symlink_to(engram_src / name)
    (engram / "model.safetensors.index.json").symlink_to(SITE / "engram.index.json")
    return model, kit, engram


def rdma_devices(env):
    hca = env.get("TF_RDMA_DEVICES") or env.get("NCCL_IB_HCA", "")
    return ",".join(dev.split(":")[0] for dev in hca.lstrip("=^").split(",") if dev)


def command(env, model, kit, engram):
    rank = env["RANK"]
    if rank not in ("0", "1"):
        raise ValueError("the dsv41 TP2 engine requires rank 0 or 1")
    master = env.get("DSV41_TP_MASTER") or env["MASTER"]
    devices = rdma_devices(env)
    for name in ("TF_DS_RANK_CACHE", "TF_DS_TOKEN_MAP"):
        if not Path(env[name]).exists():
            raise SystemExit(f"{name}={env[name]} is missing: stage the rank inputs (README, Rank caches)")
    child = dict(env, TF_TP_WORLD="2", TF_TP_MASTER=master, TF_TP_PORT=env["MASTER_PORT"], TF_RDMA_DEVICES=devices,
                 TF_RDMA_GID_INDEX=env.get("TF_RDMA_GID_INDEX") or env.get("NCCL_IB_GID_INDEX", ""))
    if rank == "1":
        flags = [f"--{flag}" for flag in ("graphs", "side", "prefetch", "aio")]
        values = [env.get(var, "0") for var in ("TF_DS_GRAPHS", "TF_DS_HC_SIDE", "TF_DS_L2_PREFETCH", "TF_DS_ENGRAM_AIO")]
        args = ["tf-dsv41-lanes", str(model), env["TF_DS_RANK_CACHE"], "1", "2", master, env["MASTER_PORT"], str(kit),
                "--engram", str(engram), "--token-map", env["TF_DS_TOKEN_MAP"], "--pool", env["CONTEXT"],
                "--rdma", devices] + [x for pair in zip(flags, values) for x in pair]
        return args, child
    child.update(TF_DS_KIT=str(kit), TENSORFOLD_CUDA_KERNELS=str(kit / "aot"), TF_DS_ENGRAM=str(engram))
    args = ["tensorfold-native", "serve", str(model), "--host", env["HOST"], "--port", env["PORT"],
            "--name", env["SERVED_NAME"], "--context", env["CONTEXT"], "--parallel", "4", "--thinking",
            "--max-tokens", env["MAX_TOKENS"], "--temperature", "1.0", "--top-p", "0.95", "--top-k", "20",
            "--backend", "cuda"] + shlex.split(env.get("EXTRA_ARGS", ""))
    return args, child


def main():
    lock = json.loads((SITE / "upstream.lock.json").read_text())
    model, kit, engram = views(lock, os.environ["MODEL_PATH"])
    args, env = command(os.environ, model, kit, engram)
    # The engine is the process to lose first when host memory runs out, as with docker run --oom-score-adj 1000.
    Path("/proc/self/oom_score_adj").write_text("1000")
    os.execvpe(args[0], args, env)


if __name__ == "__main__":
    main()
