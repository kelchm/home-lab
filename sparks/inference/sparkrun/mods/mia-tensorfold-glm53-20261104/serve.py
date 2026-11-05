#!/usr/bin/env python3
"""Launch one native GLM rank under SparkRun and watch only this rank's child process.

Each host samples its own MemAvailable; the engine's TP peer watcher ends the other rank when this one exits.
This is a recipe-scoped guard, not the host-wide earlyoom proposal in #944.
"""
import ipaddress
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time

BINARY = '/opt/tensorfold/native/bin/tensorfold-native'


def launch(env):
    env = dict(env)
    rank = env.get('RANK')
    if rank not in ('0', '1'):
        raise ValueError('GLM 20261104 requires SparkRun TP2 ranks 0 and 1')
    master = env.get('FABRIC_MASTER')
    if not master or not isinstance(ipaddress.ip_address(master), ipaddress.IPv4Address):
        raise ValueError('FABRIC_MASTER must be the head CX7 IPv4 address')
    port = int(env['MASTER_PORT'])
    if not 1 <= port <= 65534:
        raise ValueError('MASTER_PORT must leave port + 1 for the TP peer watcher')
    if not env.get('NCCL_IB_HCA'):
        raise ValueError('SparkRun must detect the active RDMA devices')
    env.update(TF_GLM_RANK=rank, TF_GLM_WORLD='2', TF_CUDA_DEVICE='0',
               NCCL_COMM_ID=f'{master}:{port}', NCCL_MIN_NCHANNELS='4', NCCL_MAX_NCHANNELS='4')
    # The RoCE implementation uses SparkRun's NCCL_IB_HCA when TF_ROCE_HCA is absent.
    # Keep its exact detected list and GID index rather than hard-coding either host's device names.
    args = [BINARY, 'serve', env['MODEL_PATH'], '--name', env['SERVED_NAME'],
            '--host', env['HOST'], '--port', env['PORT'], '--context', env['CONTEXT'],
            '--parallel', '4', '--max-tokens', env['MAX_TOKENS'], '--kv-cache', 'fp8',
            '--thinking', '--reasoning-effort', 'xhigh', '--no-update-check',
            '--vision', '--vision-max-images', '50',
            '--vision-image-tokens', '4096',
            '--chat-template', '/opt/glm53-20261104/chat_template.jinja']
    if env.get('NO_DRAFTS') == '1':
        args.append('--no-drafts')
    else:
        args += ['--drafter', env['DRAFTER'], '--drafter-bits', '4']
    args += shlex.split(env.get('EXTRA_ARGS', ''))
    return args, env


def available_gib(path='/proc/meminfo'):
    for line in Path(path).read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) / 1024**2
    raise ValueError('MemAvailable missing')


def low_since(previous, now, available, floor):
    return (now if previous is None else previous) if available < floor else None


def stop(child, grace=10):
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        child.wait()
        return
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def engine_oom_score():
    Path('/proc/self/oom_score_adj').write_text('1000')


def supervise(args, env):
    # Fixed initial policy: both ranks watch locally every 2 s, with a 2.5 GiB floor held for 4 actual seconds.
    # No SSH/Docker socket or host PID namespace is exposed to a container for this guard.
    interrupted = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda number, frame: interrupted.append(number))
    child = subprocess.Popen(args, env=env, start_new_session=True, preexec_fn=engine_oom_score)
    since = None
    try:
        while child.poll() is None:
            if interrupted:
                return 128 + interrupted[0]
            try:
                free = available_gib()
            except (OSError, ValueError) as error:
                print(f'[memguard] cannot sample host memory: {error}; stopping rank', flush=True)
                return 72
            now = time.monotonic()
            since = low_since(since, now, free, 2.5)
            if since is not None and now - since >= 4:
                print(f'[memguard] rank {env["RANK"]}: {free:.2f} GiB available, below 2.5 for 4 s; stopping rank', flush=True)
                return 73
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        return child.returncode if child.returncode >= 0 else 128 - child.returncode
    finally:
        stop(child)


def main():
    args, env = launch(os.environ)
    raise SystemExit(supervise(args, env))


if __name__ == '__main__':
    main()
