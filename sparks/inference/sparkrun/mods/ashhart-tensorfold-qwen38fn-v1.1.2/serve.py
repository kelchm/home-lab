#!/usr/bin/env python3
"""Map SparkRun's rank environment to upstream tensorfold-native serving Qwen3.8-Flash-Next on two ranks.

Rank 0 serves the API and listens for rank 1 at MASTER_PORT, the port SparkRun's rendezvous gate watches. It listens on
FABRIC_MASTER, the head's CX7 address the adapter passes when SparkRun has one, or else on SparkRun's MASTER. The engine
keeps no MTP draft state across ranks, so with drafts on the prompt cache is 0. Both ranks assert the same
TENSORFOLD_CHECKPOINT_SHA256, which each first checks against its own snapshot.
"""
import hashlib
import os
import shlex
from pathlib import Path


def checkpoint_digest(snapshot):
    """sha256 of the snapshot's sorted "path blob" lines, each blob the repository blob that file links to.

    HF names a repository blob by its content hash (LFS SHA-256 or git SHA-1), whether it holds the bytes, as on a
    worker SparkRun copied to, or links on to the cache-wide shared store, as on a head with huggingface_hub 1.32 or
    later. Only that first link is read, so both layouts give the same digest.
    """
    root = Path(snapshot)
    blobs = (root.parents[1] / "blobs").resolve()
    lines = []
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        if not path.is_symlink():
            raise SystemExit(f"{path} is not a link into {blobs}: not an HF cache snapshot")
        blob = path.parent / os.readlink(path)
        if blob.parent.resolve() != blobs or not path.is_file():
            raise SystemExit(f"{path} does not link to a present blob in {blobs}")
        lines.append(f"{path.relative_to(root).as_posix()} {blob.name}\n")
    return hashlib.sha256("".join(sorted(lines)).encode()).hexdigest()


def command(env):
    rank = env["RANK"]
    if rank not in ("0", "1"):
        raise ValueError("Qwen3.8-Flash-Next on two ranks requires rank 0 or 1")
    devices = [d for d in env.get("NCCL_IB_HCA", "").removeprefix("=").split(",") if d]
    if len(devices) != 2 or env.get("NCCL_IB_HCA", "").startswith("^"):
        raise SystemExit(f"NCCL_IB_HCA={env.get('NCCL_IB_HCA', '')!r}: expected the two CX7 rails")
    digest = checkpoint_digest(env["MODEL_PATH"])
    if digest != env.get("TENSORFOLD_CHECKPOINT_SHA256"):
        raise SystemExit(f"{env['MODEL_PATH']} hashes to {digest}, not TENSORFOLD_CHECKPOINT_SHA256")
    args = ["tensorfold-native", "serve", env["MODEL_PATH"], "--backend", "cuda", "--name", env["SERVED_NAME"],
            "--host", env["HOST"], "--port", env["PORT"], "--context", env["CONTEXT"],
            "--max-tokens", env["MAX_TOKENS"], "--thinking", "--tp", "2", "--rank", rank,
            "--master", env.get("FABRIC_MASTER") or env["MASTER"], "--master-port", env["MASTER_PORT"]]
    extra = shlex.split(env.get("EXTRA_ARGS", ""))
    if env.get("NO_DRAFTS") == "1":
        args.append("--no-drafts")
    elif not any(a.startswith("--prompt-cache-gib") for a in extra):
        args += ["--prompt-cache-gib", "0"]
    return args + extra


def main():
    args = command(os.environ)
    # The engine is the process to lose first when host memory runs out, as with docker run --oom-score-adj 1000.
    Path("/proc/self/oom_score_adj").write_text("1000")
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
