"""Native GLM candidate: SparkRun translation, bounded rank cleanup, capacity receipts and immutable inputs."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys

import pytest
import yaml
from sparkrun.core.recipe import Recipe

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins'))
from tensorfold import TensorFoldRuntime  # noqa: E402

MOD = ROOT / 'mods/mia-tensorfold-glm53-20261104'
RECIPE = ROOT / 'mia-tensorfold-glm53-exl3-tp2/glm53-exl3-tp2-mia-20261104-agent-1m.yaml'


def load(name):
    spec = importlib.util.spec_from_file_location('glm_native_' + name, MOD / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def env_for(rank):
    recipe = Recipe(yaml.safe_load(RECIPE.read_text()), source_path=str(RECIPE))
    runtime = TensorFoldRuntime()
    assert not [i for i in runtime.validate_recipe(recipe) if i.severity == 'error']
    command = runtime.generate_node_command(recipe, {}, head_ip='192.0.2.1', num_nodes=2, node_rank=rank)
    tokens = shlex.split(command)
    env = dict(token.split('=', 1) for token in tokens[1:-1])
    return {**recipe.env, **env, 'FABRIC_MASTER': '198.18.0.1', 'NCCL_IB_HCA': 'rail0,rail1', 'NCCL_IB_GID_INDEX': '3'}


@pytest.mark.parametrize('rank', [0, 1])
def test_adapter_launches_the_native_rank_and_preserves_detected_fabric(rank):
    serve = load('serve')
    args, env = serve.launch(env_for(rank))
    assert env['NCCL_COMM_ID'] == '198.18.0.1:25000'
    assert env['TF_GLM_RANK'] == str(rank) and env['TF_GLM_WORLD'] == '2'
    assert env['NCCL_IB_HCA'] == 'rail0,rail1' and env['NCCL_IB_GID_INDEX'] == '3'
    assert args[:2] == [serve.BINARY, 'serve']
    for key, value in {'--context': '1048576', '--parallel': '4', '--max-tokens': '32768', '--kv-cache': 'fp8',
                       '--drafter-bits': '4', '--reasoning-effort': 'xhigh', '--vision-max-images': '50',
                       '--vision-image-tokens': '4096'}.items():
        assert args[args.index(key) + 1] == value
    assert '--thinking' in args and '--no-thinking' not in args
    assert env['TF_GLM_CLEAR_THINKING'] == '0'
    assert env['TF_GLM_DENSE_BITS'] == env['TF_GLM_HEAD_BITS'] == '8'
    assert env['TF_KV_PAGES'] == 'on' and env['TF_GLM_HOST_CACHE_GIB'] == '0'
    assert not any('spill' in a or 'snapshot-dir' in a for a in args)
    assert not any(a in args for a in ('--tp', '--rank', '--master'))  # native GLM uses rank environment
    args, _ = serve.launch({**env_for(rank), 'NO_DRAFTS': '1'})
    assert '--no-drafts' in args and '--drafter' not in args


@pytest.mark.parametrize('change', [{'RANK': '2'}, {'FABRIC_MASTER': ''}, {'FABRIC_MASTER': '::1'},
                                   {'NCCL_IB_HCA': ''}, {'MASTER_PORT': '65535'}])
def test_bad_rank_or_missing_fabric_refuses_before_start(change):
    with pytest.raises(ValueError):
        load('serve').launch({**env_for(0), **change})


def test_guard_uses_elapsed_time_resets_on_recovery_and_reads_memavailable(tmp_path):
    serve = load('serve')
    meminfo = tmp_path / 'meminfo'
    meminfo.write_text('MemFree: 1 kB\nMemAvailable: 3145728 kB\n')
    assert serve.available_gib(meminfo) == 3
    since = serve.low_since(None, 10, 2.49, 2.5)
    assert since == 10
    assert serve.low_since(since, 12, 2, 2.5) == 10
    assert serve.low_since(since, 13, 2.5, 2.5) is None
    assert serve.low_since(None, 14, 2, 2.5) == 14
    meminfo.write_text('MemFree: 1 kB\n')
    with pytest.raises(ValueError, match='MemAvailable'):
        serve.available_gib(meminfo)


def test_guard_kills_only_its_child_group_even_if_term_is_ignored():
    serve = load('serve')
    child = subprocess.Popen([sys.executable, '-u', '-c',
                              'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready"); time.sleep(60)'],
                             start_new_session=True, stdout=subprocess.PIPE, text=True)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        serve.stop(child, grace=0.05)
        assert child.returncode == -signal.SIGKILL
        assert unrelated.poll() is None
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        unrelated.terminate()
        unrelated.wait()


@pytest.mark.parametrize('sample', [OSError('unreadable'), ValueError('no MemAvailable')])
def test_guard_fails_closed_on_lost_sampler(monkeypatch, sample):
    serve = load('serve')
    monkeypatch.setattr(serve, 'engine_oom_score', lambda: None)
    monkeypatch.setattr(serve, 'available_gib', lambda: (_ for _ in ()).throw(sample))
    # Avoid replacing this pytest process's signal handlers.
    monkeypatch.setattr(serve.signal, 'signal', lambda *args: None)
    assert serve.supervise([sys.executable, '-c', 'import time; time.sleep(60)'], {'RANK': '0'}) == 72


def test_supervisor_waits_for_a_continuous_low_memory_interval(monkeypatch):
    serve = load('serve')
    clock = [0]
    samples = iter([2, 2, 3, 2, 2, 2])
    stopped = []

    class Child:
        def poll(self):
            return None

        def wait(self, timeout):
            clock[0] += timeout
            raise subprocess.TimeoutExpired('fake engine', timeout)

    monkeypatch.setattr(serve.subprocess, 'Popen', lambda *args, **kwargs: Child())
    monkeypatch.setattr(serve.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(serve.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(serve, 'available_gib', lambda: next(samples))
    monkeypatch.setattr(serve, 'stop', lambda child: stopped.append(child))
    assert serve.supervise(['fake'], {'RANK': '1'}) == 73
    assert clock[0] == 10 and len(stopped) == 1  # recovery at t=4 reset the initial low sample


RECEIPT = ('info: prompt cache: 3.6 GiB of device pages and 0.0 GiB of pinned host memory a rank (0.0 GiB over the 2 ranks)\n'
           "[tensorfold] CUDA sm_121 device 0 (NVIDIA GB10, memory shared with the host): model 83.29 GiB; 4 streams at once, the engine's own and 3 more taking 4098 pages of 512 rows (12.47 GiB) as they need them (enough for one at a 1048576-token window)\n")


def test_capacity_gate_requires_the_actual_pool_and_nonzero_cache():
    check = load('capacity').check
    assert check(RECEIPT) == (2098176, 3.6)
    for bad in ('', RECEIPT + RECEIPT, RECEIPT.replace('4098 pages', '2048 pages'), RECEIPT.replace('512 rows', '256 rows'),
                RECEIPT.replace('3.6 GiB', '0.0 GiB'),
                RECEIPT.replace('0.0 GiB of pinned', '4.0 GiB of pinned')):
        with pytest.raises(ValueError):
            check(bad)


@pytest.mark.parametrize('change', ['edit', 'extra', 'linked-file', 'linked-directory', 'linked-lock'])
def test_frozen_bundle_rejects_drift_and_links(tmp_path, change):
    bundle = tmp_path / 'mod'
    shutil.copytree(MOD, bundle, ignore=shutil.ignore_patterns('__pycache__'))
    command = [sys.executable, str(bundle / 'prepare.py'), '--check']
    assert subprocess.run(command, capture_output=True).returncode == 0
    if change == 'edit':
        (bundle / 'serve.py').write_text('changed')
    elif change == 'extra':
        (bundle / 'extra.txt').write_text('unreviewed')
    else:
        path = bundle / {'linked-file': 'serve.py', 'linked-directory': 'build', 'linked-lock': 'upstream.lock.json'}[change]
        target = tmp_path / 'elsewhere'
        path.rename(target)
        path.symlink_to(target)
    assert subprocess.run(command, capture_output=True).returncode != 0


def test_recipe_retains_existing_weight_snapshots_and_has_both_readiness_gates():
    recipe = yaml.safe_load(RECIPE.read_text())
    old = yaml.safe_load((RECIPE.parent / 'glm53-exl3-tp2-mia-tf-bf5f9452-mia-1m-durable.yaml').read_text())
    lock = json.loads((MOD / 'upstream.lock.json').read_text())
    assert recipe['model_revision'] == old['model_revision'] == lock['weights']['revision']
    assert recipe['defaults']['drafter_revision'] == old['defaults']['drafter_revision'] == lock['drafter']['revision']
    assert recipe['container'] == lock['image_id']
    assert recipe['metadata']['profile'] == RECIPE.stem
    assert recipe['metadata']['status'] == 'candidate'
    assert recipe['post_exec'][0] == 'python3 /opt/glm53-20261104/capacity.py'
    assert recipe['post_exec'][1].endswith('--lanes 4 --efforts max,high,low,none')
