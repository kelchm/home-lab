#!/usr/bin/env python3
"""Map SparkRun's rank environment to Mia's pinned tensorfold-native CLI, rebuilt with this mod's discovery patches.

Rank 0 serves the API and listens for rank 1 at MASTER_PORT, the port SparkRun's rendezvous gate watches, on
FABRIC_MASTER, the head's CX7 address from the adapter, so the control link never takes the management network.
"""
import os
import shlex
from pathlib import Path

BINARY = "/opt/tensorfold-mod/bin/tensorfold-native"


def command(env):
    rank = env["RANK"]
    if rank not in ("0", "1"):
        raise ValueError("Mia TensorFold requires rank 0 or 1")
    master = env.get("FABRIC_MASTER", "").strip()
    if not master:
        raise SystemExit("FABRIC_MASTER is unset: SparkRun found no reachable CX7 address for the head, and the ranks "
                         "link only over the fabric")
    print(f"Mia TensorFold rank {rank}: control link on FABRIC_MASTER {master}:{env['MASTER_PORT']}", flush=True)
    args = [BINARY, "serve", env["MODEL_PATH"], "--name", env["SERVED_NAME"],
            "--host", env["HOST"], "--port", env["PORT"], "--context", env["CONTEXT"],
            "--parallel", "16", "--max-tokens", env["MAX_TOKENS"], "--thinking", "--kv-dtype", "fp8",
            "--vision", "--vision-max-images", env["TENSORFOLD_MAX_IMAGES"], "--vision-max-videos", "4",
            "--vision-image-tokens", env["TENSORFOLD_IMAGE_TOKENS"],
            "--tp", "2", "--rank", rank, "--master", master, "--master-port", env["MASTER_PORT"]]
    if env.get("NO_DRAFTS") == "1":
        args.append("--no-drafts")
    args += shlex.split(env.get("EXTRA_ARGS", ""))
    return args


def main():
    args = command(os.environ)
    # The engine is the process to lose first when host memory runs out, as with docker run --oom-score-adj 1000.
    Path("/proc/self/oom_score_adj").write_text("1000")
    os.execv(args[0], args)


if __name__ == "__main__":
    main()
