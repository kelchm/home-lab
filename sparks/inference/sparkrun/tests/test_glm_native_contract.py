"""Native GLM candidate: SparkRun translation, bounded rank cleanup, capacity receipts and immutable inputs."""
import importlib.util
import json
import os
from pathlib import Path
import selectors
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

MOD = ROOT / 'mods/mia-tensorfold-glm53-1104'
RECIPE = ROOT / 'mia-tensorfold-glm53-exl3-tp2/glm53-exl3-tp2-mia-1104-agent-1m.yaml'


@pytest.fixture(autouse=True, params=['1104', '1104-r2'])
def native_revision(request, monkeypatch):
    monkeypatch.setitem(globals(), 'MOD', ROOT / ('mods/mia-tensorfold-glm53-' + request.param))
    monkeypatch.setitem(globals(), 'RECIPE', ROOT / ('mia-tensorfold-glm53-exl3-tp2/glm53-exl3-tp2-mia-' + request.param + '-agent-1m.yaml'))


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
    assert env['NCCL_COMM_ID'] == '198.18.0.1:24999'
    recipe = Recipe(yaml.safe_load(RECIPE.read_text()), source_path=str(RECIPE))
    gate = TensorFoldRuntime().native_rendezvous_port(recipe, num_nodes=2)
    assert int(env['NCCL_COMM_ID'].rsplit(':', 1)[1]) + 1 == gate == int(env['MASTER_PORT'])
    assert env['TF_GLM_RANK'] == str(rank) and env['TF_GLM_WORLD'] == '2'
    assert env['NCCL_IB_HCA'] == 'rail0,rail1' and env['NCCL_IB_GID_INDEX'] == '3'
    assert args[:2] == [serve.BINARY, 'serve']
    for key, value in {'--context': '1048576', '--parallel': '4', '--max-tokens': '32768', '--kv-cache': 'fp8',
                       '--drafter-bits': '4', '--reasoning-effort': 'xhigh', '--vision-max-images': '50',
                       '--vision-image-tokens': '102400' if MOD.name.endswith('-r2') else '4096'}.items():
        assert args[args.index(key) + 1] == value
    if MOD.name.endswith('-r2'):
        for key, value in {'--vision-max-videos': '4', '--vision-video-tokens': '32768', '--max-frames': '128', '--request-body-mib': '96'}.items():
            assert args[args.index(key) + 1] == value
        assert env['TF_PREPARE_RESIDENTS'] == '4' and env['TF_PREPARE_WAITING'] == '32'
        assert env['TF_PREPARE_WAIT_MS'] == '300000'
    assert '--thinking' in args and '--no-thinking' not in args
    assert env['TF_GLM_CLEAR_THINKING'] == '0'
    assert env['TF_GLM_DENSE_BITS'] == env['TF_GLM_HEAD_BITS'] == '8'
    assert env['TF_KV_PAGES'] == 'on' and env['TF_GLM_HOST_CACHE_GIB'] == '0'
    assert not any('spill' in a or 'snapshot-dir' in a for a in args)
    assert not any(a in args for a in ('--tp', '--rank', '--master'))  # native GLM uses rank environment
    args, _ = serve.launch({**env_for(rank), 'NO_DRAFTS': '1'})
    assert '--no-drafts' in args and '--drafter' not in args


@pytest.mark.parametrize('change', [{'RANK': '2'}, {'FABRIC_MASTER': ''}, {'FABRIC_MASTER': '::1'},
                                   {'NCCL_IB_HCA': ''}, {'MASTER_PORT': '1'}, {'MASTER_PORT': '65536'}])
def test_bad_rank_or_missing_fabric_refuses_before_start(change):
    with pytest.raises(ValueError):
        load('serve').launch({**env_for(0), **change})


def test_guard_uses_elapsed_time_resets_on_recovery_and_reads_memavailable(tmp_path):
    serve = load('serve')
    meminfo = tmp_path / 'meminfo'
    meminfo.write_text('MemFree: 1 kB\nMemAvailable: 3145728 kB\n')
    assert serve.available_gib(meminfo, tmp_path / 'missing-zoneinfo') == 3
    since = serve.low_since(None, 10, 2.49, 2.5)
    assert since == 10
    assert serve.low_since(since, 12, 2, 2.5) == 10
    assert serve.low_since(since, 13, 2.5, 2.5) is None
    assert serve.low_since(None, 14, 2, 2.5) == 14
    meminfo.write_text('MemFree: 1 kB\n')
    with pytest.raises(ValueError, match='MemAvailable'):
        serve.available_gib(meminfo, tmp_path / 'missing-zoneinfo')


@pytest.mark.parametrize('port', ['2', '65535'])
def test_gate_port_boundaries_leave_a_valid_nccl_bootstrap_port(port):
    _, env = load('serve').launch({**env_for(0), 'MASTER_PORT': port})
    assert int(env['NCCL_COMM_ID'].rsplit(':', 1)[1]) == int(port) - 1


def test_memory_budget_includes_only_parked_pages_above_the_resting_level(tmp_path):
    serve = load('serve')
    meminfo, zones = tmp_path / 'meminfo', tmp_path / 'zoneinfo'
    meminfo.write_text('MemTotal: 8388608 kB\nMemAvailable: 2097152 kB\n')
    text = ('Node 0, zone Normal\n  pagesets\n    cpu: 0\n      count: 524288\n      high_min: 262144\n'
            '  vm stats threshold: 1\n    cpu: 1\n      count: 524288\n      high_min: 262144\n'
            '  next_zone_field\n      count: 999999999\n')
    zones.write_text(text)
    assert serve.parked_bytes(text, 4096) == 2 * 1024**3
    assert serve.available_gib(meminfo, zones, 4096) == 4
    meminfo.write_text('MemTotal: 3145728 kB\nMemAvailable: 2097152 kB\n')
    assert serve.available_gib(meminfo, zones, 4096) == 3  # never exceed MemTotal
    old = '  pagesets\n    cpu: 0\n      count: 262144\n'
    assert serve.parked_bytes(old, 4096) == 512 * 1024**2
    for bad in (text.replace('count: 524288', 'count: -1'), text.replace('high_min: 262144', 'high_min: nope'),
                text.replace('count: 524288', 'count:'), text.replace('count: 524288', f'count: {2**64}')):
        assert serve.parked_bytes(bad, 4096) == 0
    assert serve.parked_bytes(text.replace('count: 524288', 'count: 1'), 4096) == 0


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


@pytest.mark.parametrize('leader_ignores_term', [False, True])
def test_guard_reaps_descendants_when_the_leader_exits_first(leader_ignores_term):
    serve = load('serve')
    descendant = 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("descendant",flush=True); time.sleep(60)'
    code = ('import signal,subprocess,sys,time; '
            + ('signal.signal(signal.SIGTERM,signal.SIG_IGN); ' if leader_ignores_term else '')
            + f'subprocess.Popen([sys.executable,"-u","-c",{descendant!r}],stdout=sys.stdout); '
            + 'print("leader",flush=True); time.sleep(60)')
    child = subprocess.Popen([sys.executable, '-u', '-c', code], start_new_session=True,
                             stdout=subprocess.PIPE, text=True)
    try:
        assert {child.stdout.readline().strip(), child.stdout.readline().strip()} == {'leader', 'descendant'}
        serve.stop(child, grace=0.05)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=2), 'a descendant still holds the output pipe open'
            assert child.stdout.read() == ''
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


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
    receipt = RECEIPT
    if MOD.name.endswith('-r2'):
        with pytest.raises(ValueError, match='Insufficient GLM pool'):
            check(receipt)  # the original Mia smoke floor cannot replace production's measured floor
        receipt = receipt.replace('4098 pages', '5044 pages')
        assert check(receipt) == (2582528, 3.6)
        with pytest.raises(ValueError, match='Insufficient GLM pool'):
            check(receipt.replace('5044 pages', '5043 pages'))
    else:
        assert check(receipt) == (2098176, 3.6)
    for bad in ('', receipt + receipt, receipt.replace('4098 pages', '2048 pages').replace('5044 pages', '2048 pages'),
                receipt.replace('512 rows', '256 rows'), receipt.replace('3.6 GiB', '0.0 GiB'),
                receipt.replace('0.0 GiB of pinned', '4.0 GiB of pinned')):
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
    suffix = '-r2' if MOD.name.endswith('-r2') else ''
    assert recipe['post_exec'][0] == f'python3 /opt/glm53-1104{suffix}/capacity.py'
    hook = shlex.split(recipe['post_exec'][1])
    assert hook[:2] == ['python3', '/opt/tensorfold-readiness/readiness.py']
    for key, value in {'--model': 'GLM-5.3-Flash', '--context': '1048576', '--lanes': '4', '--efforts': 'max,high,low,none'}.items():
        assert hook.count(key) == 1 and hook[hook.index(key) + 1] == value
