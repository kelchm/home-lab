"""Off-host native release trial through the existing home-lab SparkRun 0.4.2 path."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
from unittest import mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins'))
from tensorfold import TensorFoldRuntime, hf_snapshot_path, _FABRIC_MASTER
from sparkrun.core.recipe import Recipe
from sparkrun.core.validation import ERROR
from sparkrun.orchestration.executors.docker import DockerExecutor
from sparkrun.runtimes._cluster_ops import ClusterContext

MOD = ROOT / 'mods/mia-tensorfold-glm53-13f2464e'
PROFILE = ROOT / 'mia-tensorfold-glm53-native-tp2/glm53-mia-2026.11.07-agent-1m.yaml'
LEARN = '/cache/huggingface/spark-inference/learn/glm53-mia-2026.11.07-agent-1m'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serve = load('release_trial_serve', MOD / 'serve.py')
capacity = load('release_trial_capacity', MOD / 'capacity.py')
validator = load('release_trial_validator', ROOT / 'validate_locks.py')


def health():
    # Native server /health schema, including Geometry.half's two ownership parts.
    return {'status': 'ok', 'warming': False, 'model': 'GLM-5.3-Flash', 'max_batch_size': 4,
            'kv_shard': {'parts': 2, 'page_tokens': 512, 'part_pages': 3071, 'per_part': [{}, {}]}}


def receipts(pages=6147, cache='4.1', host='0.0', store=None, quota=65536, ranks=2):
    # Literal startup formats from the released native engine; root/identity is intentional.
    return (f"[tensorfold] CUDA GLM: the engine's own and 3 more taking {pages} pages of 512 rows\n"
            f'info: prompt cache: {cache} GiB of device pages and {host} GiB of pinned host memory a rank\n'
            f"info: glm: conversation states on disk in {store or LEARN + '/0123456789abcdef'} (0 states, 0 MiB of {quota} MiB; {ranks} ranks' parts)\n")


def test_source_real_receipts_accept_identity_child_and_actual_shard():
    report = capacity.check(receipts(), health(), learn_dir=LEARN)
    assert report['aggregate_pool_tokens'] == 3144704
    assert report['actual_part_pages'] == report['minimum_part_pages'] == 3071
    assert report['learned_store_location'] == 'rank0'
    assert report['durability_status'] == 'unknown'
    assert capacity.check(receipts(quota=1024), health(), learn_gib=1, learn_dir=LEARN)
    boundary = health()
    boundary['kv_shard']['part_pages'] = 2522
    assert capacity.check(receipts(pages=5048, cache='2.0'), boundary, learn_dir=LEARN)


@pytest.mark.parametrize('changes', [
    {'pages': 5047}, {'pages': 4100}, {'cache': '1.9'}, {'host': '0.1'}, {'quota': 1024}, {'ranks': 1},
    {'store': LEARN}, {'store': LEARN + '/identity'}, {'store': LEARN + '/0123456789abcdeF'},
    {'store': LEARN + '/0123456789abcdef/child'}, {'store': LEARN + '/other/0123456789abcdef'},
    {'store': LEARN + '/../0123456789abcdef'}, {'store': LEARN + '-other/0123456789abcdef'},
])
def test_source_real_startup_receipts_reject_wrong_capacity_or_store(changes):
    with pytest.raises(ValueError):
        capacity.check(receipts(**changes), health(), learn_dir=LEARN)


@pytest.mark.parametrize('mutation', ['absent', 'small', 'aggregate', 'boolean', 'parts', 'page_tokens', 'ownership', 'warming', 'model', 'lanes'])
def test_actual_health_allocation_must_be_present_and_large_enough(mutation):
    body = health()
    if mutation == 'absent':
        body.pop('kv_shard')
    elif mutation in ('small', 'aggregate', 'boolean', 'parts', 'page_tokens', 'ownership'):
        field, value = {'small': ('part_pages', 2048), 'aggregate': ('part_pages', 2049), 'boolean': ('part_pages', True),
                        'parts': ('parts', 1), 'page_tokens': ('page_tokens', 256), 'ownership': ('per_part', [{}])}[mutation]
        body['kv_shard'][field] = value
    else:
        body[{'warming': 'warming', 'model': 'model', 'lanes': 'max_batch_size'}[mutation]] = {'warming': True, 'model': 'wrong', 'lanes': 1}[mutation]
    with pytest.raises(ValueError):
        capacity.check(receipts(), body, learn_dir=LEARN)


@pytest.mark.parametrize('log', ['', receipts() + receipts(), receipts().split('info: glm:')[0]])
def test_missing_or_ambiguous_startup_receipts_fail(log):
    with pytest.raises(ValueError):
        capacity.check(log, health(), learn_dir=LEARN)


def parse(command):
    tokens = shlex.split(command)
    env = {}
    for token in tokens[1:]:
        if '=' not in token or token.startswith('/'):
            break
        key, _, value = token.partition('=')
        env[key] = value
    return env, tokens[len(env) + 1:]


def test_actual_sdk042_profile_adjacent_mod_and_both_ranks(monkeypatch):
    assert importlib.metadata.version('sparkrun') == '0.4.2'
    def forbidden(*a, **kw):
        raise AssertionError('Off-host SDK test attempted a process or socket')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    recipe = Recipe(yaml.safe_load(PROFILE.read_text()), source_path=str(PROFILE))
    runtime = TensorFoldRuntime()
    assert not [issue for issue in runtime.validate_recipe(recipe) if issue.severity == ERROR]
    assert recipe.mods == ['../mods/tensorfold-readiness-v2', '../mods/mia-tensorfold-glm53-13f2464e']
    assert all((PROFILE.parent / mod / 'run.sh').is_file() for mod in recipe.mods)
    assert recipe.post_exec[1] == 'python3 /opt/tensorfold-readiness/readiness.py --model GLM-5.3-Flash --context 1048576 --lanes 4 --efforts max,high,low'
    ctx = ClusterContext.build(runtime, ['192.0.2.1', '192.0.2.2'], recipe.container, 'local-native-test', recipe.env,
                               None, None, dry_run=True, recipe=recipe)
    script = DockerExecutor().generate_launch_script(image=recipe.container, container_name='local-native-test',
                                                     command='sleep infinity', env=ctx.all_env, volumes=ctx.volumes)
    assert 'TF_GLM_KV_SHARD=1' in shlex.split(script)
    commands = []
    token = _FABRIC_MASTER.set('198.18.0.1')
    try:
        for rank in (0, 1):
            env, argv = parse(runtime.generate_node_command(recipe, {}, head_ip='192.0.2.1', num_nodes=2,
                              node_rank=rank, init_port=29511, hosts=['192.0.2.1', '192.0.2.2']))
            assert argv == ['/usr/local/bin/tensorfold-entrypoint']
            assert env['MODEL_PATH'] == hf_snapshot_path(recipe.model, recipe.model_revision)
            assert env['DRAFTER'] == hf_snapshot_path(recipe.defaults['drafter'], recipe.defaults['drafter_revision'])
            args, native_env = serve.launch({**recipe.env, **env, 'NCCL_IB_HCA': 'mlx5_0,mlx5_1'})
            assert native_env['TF_GLM_RANK'] == str(rank)
            assert native_env['NCCL_COMM_ID'] == '198.18.0.1:29510'
            assert runtime.native_rendezvous_port(recipe, {}, num_nodes=2, init_port=29511) == 29511
            commands.append(args)
    finally:
        _FABRIC_MASTER.reset(token)
    assert commands[0] == commands[1]
    for flag, value in {'--parallel': '4', '--context': '1048576', '--max-tokens': '32768', '--kv-cache': 'fp8',
                        '--reasoning-effort': 'xhigh', '--top-k': '20', '--prompt-cache-gib': '6',
                        '--learn-gib': '64', '--learn-min-free-gib': '50', '--learn-daily-gib': '128',
                        '--chat-template': '/opt/tensorfold-mod/chat_template.jinja'}.items():
        assert commands[0][commands[0].index(flag) + 1] == value
    assert '--learn-conversations' in commands[0] and '--vision' in commands[0]
    assert '--dashboard' in commands[0]
    assert not any('video' in arg for arg in commands[0])


@pytest.mark.parametrize('mutation', ['content', 'symlink', 'missing', 'manifest', 'prepare-link'])
def test_frozen_mod_fails_before_installation(tmp_path, mutation):
    bundle = tmp_path / MOD.name
    shutil.copytree(MOD, bundle, ignore=shutil.ignore_patterns('__pycache__'))
    check = [sys.executable, str(bundle / 'prepare.py'), '--check']
    subprocess.run(check, check=True, capture_output=True)
    if mutation == 'content':
        (bundle / 'serve.py').write_text('changed')
    elif mutation in ('symlink', 'missing'):
        (bundle / 'serve.py').unlink()
        if mutation == 'symlink':
            (bundle / 'serve.py').symlink_to(MOD / 'serve.py')
    elif mutation == 'manifest':
        (bundle / 'upstream.lock.json').unlink()
        (bundle / 'upstream.lock.json').symlink_to(MOD / 'upstream.lock.json')
    else:
        (bundle / 'serve.py').write_text('changed')
        (bundle / 'prepare.py').unlink()
        (bundle / 'prepare.py').symlink_to(MOD / 'prepare.py')
    assert subprocess.run(check, capture_output=True).returncode != 0


def test_native_lock_is_strict_and_bound_to_copied_release(tmp_path):
    assert all(validator.validate_repository(ROOT))
    bundle = tmp_path / MOD.name
    shutil.copytree(MOD, bundle, ignore=shutil.ignore_patterns('__pycache__'))
    original = json.loads((bundle / 'upstream.lock.json').read_text())
    for key, value in [('unreviewed', True), ('engine_revision', 'a' * 40), ('release_sha256', 'a' * 64), ('release_manifest', '../release.json')]:
        lock = dict(original, **{key: value})
        with pytest.raises(validator.InvalidManifest):
            validator.validate_lock(lock, bundle)
    (bundle / 'release.json').write_text('{}')
    with pytest.raises(validator.InvalidManifest):
        validator.validate_lock(original, bundle)


def test_quota_probe_is_isolated_and_only_changes_profile_store_and_quota():
    main = yaml.safe_load(PROFILE.read_text())
    probe = yaml.safe_load(PROFILE.with_name('glm53-mia-2026.11.07-quota-probe.yaml').read_text())
    for data in (main, probe):
        assert data['env']['GLM_LEARN_DIR'].endswith(data['metadata'].pop('profile'))
        data['metadata'].pop('serving_profile')
        data['env'].pop('GLM_LEARN_DIR')
        data['env'].pop('GLM_LEARN_GIB')
        data['post_exec'].pop(0)
    assert main == probe


def test_memory_supervisor_stops_private_group_after_continuous_pressure():
    clock = [0]
    class Child:
        def poll(self): return None
        def wait(self, timeout):
            clock[0] += timeout
            raise subprocess.TimeoutExpired('synthetic', timeout)
    with mock.patch.object(serve.subprocess, 'Popen', return_value=Child()) as popen, \
         mock.patch.object(serve.signal, 'signal'), \
         mock.patch.object(serve.time, 'monotonic', side_effect=lambda: clock[0]), \
         mock.patch.object(serve, 'available_gib', side_effect=[2, 2, 3, 2, 2, 2]), \
         mock.patch.object(serve, 'stop') as stop:
        assert serve.supervise(['synthetic'], {'RANK': '1'}) == 73
        assert clock[0] == 10
        assert popen.call_args.kwargs['start_new_session'] is True
        assert popen.call_args.kwargs['preexec_fn'] is serve.engine_oom_score
        stop.assert_called_once()
    with mock.patch.object(serve.subprocess, 'Popen', return_value=Child()), \
         mock.patch.object(serve.signal, 'signal'), \
         mock.patch.object(serve, 'available_gib', side_effect=OSError('synthetic')), \
         mock.patch.object(serve, 'stop') as stop:
        assert serve.supervise(['synthetic'], {'RANK': '0'}) == 72
        stop.assert_called_once()


def test_stop_reaps_private_group_even_if_leader_exits_on_term():
    child = mock.Mock(pid=12345)
    with mock.patch.object(serve.os, 'killpg') as kill:
        serve.stop(child)
    assert kill.call_args_list == [mock.call(12345, serve.signal.SIGTERM), mock.call(12345, serve.signal.SIGKILL)]
    assert child.wait.call_count == 2


@pytest.mark.parametrize('changes', [{'RANK': '2'}, {'MASTER_PORT': '1'}, {'FABRIC_MASTER': '::1'},
                                  {'CONTEXT': '524288'}, {'GLM_LEARN_DIR': '/cache/huggingface/tensorfold-spill'},
                                  {'GLM_LEARN_DIR': LEARN + '/../escape'}, {'EXTRA_ARGS': '--context 8'},
                                  {'GLM_LEARN_GIB': '0'}, {'NCCL_IB_HCA': ''}, {'NO_DRAFTS': '1'}])
def test_launcher_refuses_unsafe_profile_changes_before_spawn(changes):
    env = {'RANK': '0', 'FABRIC_MASTER': '192.0.2.1', 'MASTER_PORT': '25000', 'NCCL_IB_HCA': 'mlx5_0,mlx5_1',
           'CONTEXT': '1048576', 'MAX_TOKENS': '32768', 'MODEL_PATH': '/weights/target', 'DRAFTER': '/weights/draft',
           'SERVED_NAME': 'GLM-5.3-Flash', 'HOST': '0.0.0.0', 'PORT': '8888', 'GLM_LEARN_DIR': LEARN,
           'GLM_LEARN_GIB': '64', 'GLM_LEARN_MIN_FREE_GIB': '50', 'GLM_LEARN_DAILY_GIB': '128'}
    with pytest.raises(ValueError):
        serve.launch({**env, **changes})


def test_parked_memory_is_conservative_and_capped_by_total(tmp_path):
    zones = tmp_path / 'zoneinfo'
    zones.write_text('Node 0, zone Normal\n  pagesets\n    cpu: 0\n      count: 524288\n      high_min: 262144\n'
                     '  vm stats threshold: 1\n    cpu: 1\n      count: 524288\n      high_min: 262144\n'
                     '  next_zone_field\n      count: 999999999\n')
    memory = tmp_path / 'meminfo'
    memory.write_text('MemTotal: 8388608 kB\nMemAvailable: 2097152 kB\n')
    assert serve.available_gib(memory, zones, 4096) == 4
    memory.write_text('MemTotal: 3145728 kB\nMemAvailable: 2097152 kB\n')
    assert serve.available_gib(memory, zones, 4096) == 3
    assert serve.parked_bytes(zones.read_text().replace('count: 524288', 'count: -1'), 4096) == 0
