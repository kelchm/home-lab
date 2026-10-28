#!/usr/bin/env python3
"""Map SparkRun's rank environment to Mia's pinned TensorFold CLI."""
import os
import shlex


def command(env):
    rank = env["RANK"]
    if rank not in ("0", "1"):
        raise ValueError("Mia TensorFold requires rank 0 or 1")
    # The ranks meet on the head's CX7 address, as the kit's launcher does, when SparkRun supplies it.
    master = env.get("FABRIC_MASTER") or env["MASTER"]
    args = ["tensorfold", "serve", env["MODEL_PATH"], "--backend", "cuda", "--tp", "2",
            "--rank", rank, "--master", master, "--master-port", env["MASTER_PORT"],
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
    # The engine is the process to lose first when host memory runs out.
    with open("/proc/self/oom_score_adj", "w") as f:
        f.write("1000")
    os.execvp(argv[0], argv)
