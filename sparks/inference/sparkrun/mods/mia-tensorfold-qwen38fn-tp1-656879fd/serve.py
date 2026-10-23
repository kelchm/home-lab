#!/usr/bin/env python3
"""Map SparkRun's one-node environment to the kit's tensorfold-native serve command, as its start.sh builds it.

The recipe states the kit's TENSORFOLD_* and TF_FLASHNEXT_* settings, which the engine reads from the environment.
EXTRA_ARGS go last, so a recipe's own serve flags win over the kit's defaults.
"""
import os
import shlex
from pathlib import Path

BINARY = "/opt/tensorfold/native/bin/tensorfold-native"


def command(env):
    if "RANK" in env or "MASTER" in env:
        raise SystemExit("This mod serves one rank: a two-rank launch set RANK or MASTER")
    args = [BINARY, "serve", env["MODEL_PATH"], "--name", env["SERVED_NAME"],
            "--host", env["HOST"], "--port", env["PORT"], "--context", env["CONTEXT"],
            "--parallel", "5", "--max-tokens", env["MAX_TOKENS"],
            "--temperature", "1.0", "--top-p", "0.95", "--top-k", "20", "--thinking", "--kv-dtype", "fp8",
            "--vision", "--vision-max-images", env["TENSORFOLD_MAX_IMAGES"], "--vision-max-videos", "4",
            "--vision-image-tokens", env["TENSORFOLD_IMAGE_TOKENS"]]
    if env.get("NO_DRAFTS") == "1":
        args.append("--no-drafts")
    return args + shlex.split(env.get("EXTRA_ARGS", ""))


def main():
    args = command(os.environ)
    # The engine is the process to lose first when host memory runs out, as with docker run --oom-score-adj 1000.
    Path("/proc/self/oom_score_adj").write_text("1000")
    os.execv(args[0], args)


if __name__ == "__main__":
    main()
