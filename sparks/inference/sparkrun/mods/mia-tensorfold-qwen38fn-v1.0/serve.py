#!/usr/bin/env python3
"""Map SparkRun's rank environment to Mia's pinned tensorfold-native CLI."""
import os
import shlex


def command(env):
    rank = env["RANK"]
    if rank not in ("0", "1"):
        raise ValueError("Mia TensorFold requires rank 0 or 1")
    args = ["tensorfold-native", "serve", env["MODEL_PATH"], "--name", env["SERVED_NAME"],
            "--host", env["HOST"], "--port", env["PORT"], "--context", env["CONTEXT"],
            "--parallel", "16", "--max-tokens", env["MAX_TOKENS"], "--thinking", "--kv-dtype", "fp8",
            "--vision", "--vision-max-images", env["TENSORFOLD_MAX_IMAGES"], "--vision-max-videos", "4",
            "--vision-image-tokens", env["TENSORFOLD_IMAGE_TOKENS"],
            "--tp", "2", "--rank", rank, "--master", env["MASTER"], "--master-port", env["MASTER_PORT"]]
    if env.get("NO_DRAFTS") == "1":
        args.append("--no-drafts")
    args += shlex.split(env.get("EXTRA_ARGS", ""))
    return args


if __name__ == "__main__":
    argv = command(os.environ)
    os.execvp(argv[0], argv)
