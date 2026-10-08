#!/usr/bin/env python3
"""Map SparkRun's rank environment to Mia's pinned TensorFold CLI."""
import os
import shlex


def command(env):
    rank = env["RANK"]
    if rank not in ("0", "1"):
        raise ValueError("Mia TensorFold requires rank 0 or 1")
    args = ["tensorfold", "serve", env["MODEL_PATH"], "--backend", "cuda", "--tp", "2",
            "--rank", rank, "--master", env["MASTER"], "--master-port", env["MASTER_PORT"],
            "--drafter", env["DRAFTER"], "--context", env["CONTEXT"], "--parallel", "4",
            "--max-tokens", env["MAX_TOKENS"], "--thinking", "--vision", "--no-update-check"]
    if rank == "0":
        args += ["--name", env["SERVED_NAME"], "--host", env["HOST"], "--port", env["PORT"]]
    if env.get("NO_DRAFTS") == "1":
        args.append("--no-drafts")
    if "MTP_DRAFTS" in env:
        args += ["--mtp-drafts", env["MTP_DRAFTS"]]
    args += shlex.split(env.get("EXTRA_ARGS", ""))
    return args


if __name__ == "__main__":
    argv = command(os.environ)
    os.execvp(argv[0], argv)
