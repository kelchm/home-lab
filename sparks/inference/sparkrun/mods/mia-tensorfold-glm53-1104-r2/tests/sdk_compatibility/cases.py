#!/usr/bin/env python3
"""Focused integration tests, invoked by run.py in each installed SDK.

Only observation/execution boundaries are replaced. Runtime dispatch, native
orchestration, executor script generation, port polling, lifecycle and hook code
come from the selected installed SDK and the explicitly supplied repository.
"""

import argparse
import base64
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

HOSTS = ['192.0.2.31', '192.0.2.32']
FABRIC = dict(zip(HOSTS, ['198.18.0.31', '198.18.0.32']))
GATE = 29511
CID = 'offhost-sdk-compat'
REL = Path('sparks/inference/sparkrun')
MOD = Path('mods/mia-tensorfold-glm53-1104-r2')
RECIPE = Path('mia-tensorfold-glm53-exl3-tp2/glm53-exl3-tp2-mia-1104-r2-agent-1m.yaml')


class UnexpectedOperation(BaseException):
    """Bypass SDK best-effort exception handlers: unsafe fallthrough must fail."""


class OffHostGuard:
    def __init__(self):
        self.expected = False
        self.blocked = []

    def audit(self, event, args):
        if (event.startswith(('subprocess.', 'socket.')) and event != 'socket.gethostname') or event in {
            'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty', 'os.exec', 'os.spawn', 'os.kill', 'os.killpg',
        }:
            self.blocked.append({'event': event, 'expected_self_test': self.expected})
            raise UnexpectedOperation('Unmocked external operation: ' + event)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sdk_source_digest(root):
    sources = {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob('*'))
               if path.is_file() and path.suffix in ('.py', '.sh')}
    return hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CallEvidence:
    def __init__(self, roots):
        self.roots = roots
        self.calls = {}

    def profile(self, frame, event, arg):
        if event != 'call':
            return
        filename = frame.f_code.co_filename
        if filename in self.roots:
            self.calls.setdefault(filename, set()).add(frame.f_code.co_qualname)

    def manifest(self):
        return {path: {'sha256': sha(Path(path)), 'called': sorted(calls)}
                for path, calls in sorted(self.calls.items()) if Path(path).is_file()}


class JsonResponse(io.BytesIO):
    status = 200

    def __init__(self, body):
        super().__init__(json.dumps(body).encode())


class RecordedLaunch:
    """Allowlisted inert host substrate plus actual in-process hook execution."""

    def __init__(self, context, *, gate_fails=False, head_exits=False, capacity_fails=False, readiness_fails=False, health_fails=False):
        self.ctx = context
        self.gate_fails, self.capacity_fails = gate_fails, capacity_fails
        self.head_exits = head_exits
        self.readiness_fails, self.health_fails = readiness_fails, health_fails
        self.events, self.scripts, self.rank_env, self.native_args = [], {}, {}, {}
        self.container_env, self.started = {}, set()
        self.gate_probes, self.api_probes, self.sdk_ready = 0, 0, False
        self.active_hook = None
        self.smoke_barrier = threading.Barrier(4)
        self.runtime = context.runtime
        self.recipe = context.Recipe(context.data, source_path=str(context.recipe_path))
        self.executor = context.resolve_executor(recipe=self.recipe, runtime=self.runtime, rootless=False, auto_user=False, v=context.variables)
        self.runtime.executor = self.executor

    def event(self, kind, **fields):
        self.events.append({'kind': kind, **fields})

    def result(self, host, rc=0, stdout='', stderr=''):
        return self.ctx.RemoteResult(host, rc, stdout, stderr)

    def capture_script(self, kind, original, **kwargs):
        script = original(**kwargs)
        self.scripts[script] = (kind, kwargs)
        return script

    def remote_script(self, host, script, **kwargs):
        assert kwargs['dry_run'] is False
        assert host in HOSTS and script in self.scripts, 'Unregistered remote script'
        kind, fields = self.scripts[script]
        rank = int(fields['container_name'].rsplit('_', 1)[1])
        assert HOSTS[rank] == host
        if kind == 'container':
            assert fields['command'] == 'sleep infinity'
            self.container_env[rank] = {**(fields.get('nccl_env') or {}), **fields['env']}
        else:
            assert rank == 0 or self.gate_probes == 2, 'rank 1 raced the real gate'
            assert set(self.container_env) == {0, 1}
            tokens = shlex.split(fields['serve_command'])
            assert tokens[0] == 'env' and tokens[-1] == self.recipe.defaults['entrypoint']
            env = dict(token.split('=', 1) for token in tokens[1:-1])
            env = {**self.container_env[rank], **fields['env'], **env}
            args, child_env = self.ctx.serve.launch(env)
            self.rank_env[rank], self.native_args[rank] = child_env, args
            self.started.add(rank)
            self.ctx.native_launches[str(rank)] = {
                'argv': args,
                'env': {key: value for key, value in child_env.items()
                        if key in self.recipe.env or key.startswith(('NCCL_', 'TF_GLM_', 'TF_CUDA_'))
                        or key in {'RANK', 'MASTER', 'MASTER_PORT', 'FABRIC_MASTER', 'MODEL_PATH', 'DRAFTER', 'CONTEXT', 'SERVED_NAME', 'HOST', 'PORT', 'MAX_TOKENS'}},
            }
        self.event(kind, host=host, rank=rank, script_sha256=hashlib.sha256(script.encode()).hexdigest())
        return self.result(host)

    def remote_command(self, host, command, **kwargs):
        assert host in HOSTS
        if command == self.executor.stop_cmd(self.executor.node_container_name(CID, HOSTS.index(host))):
            self.event('recorded_cleanup', host=host)
        elif command == f'docker exec {CID}_node_0 cat /tmp/sparkrun_serve.log 2>/dev/null || true':
            self.event('recorded_failure_log', host=host)
        else:
            raise UnexpectedOperation('Unregistered remote command: ' + command)
        return self.result(host)

    def host_command(self, host, command, **kwargs):
        health = self.ctx.health
        if command == f'nc -z localhost {GATE}':
            assert not self.started
            self.event('collision_scan', host=host, port=GATE)
            return self.result(host, 1)  # free, before any rank executes
        if command.startswith('target=') and 'ping -c 1' in command:
            assert host == HOSTS[1] and HOSTS[0] in command
            self.event('recorded_reachability', host=host)
            return self.result(host)
        if command == health.listen_probe_cmd(GATE):
            assert self.started == {0}
            self.gate_probes += 1
            ready = self.gate_probes == 2 and not self.gate_fails
            self.event('gate_socket_table', host=host, port=GATE, ready=ready, command=command)
            return self.result(host, 0 if ready else 1)
        if command == health.listen_probe_cmd(8888):
            assert self.started == {0, 1}
            self.api_probes += 1
            ready = self.api_probes == 2
            self.event('api_socket_table', host=host, port=8888, ready=ready, command=command)
            return self.result(host, 0 if ready else 1)
        if command == f"docker inspect -f '{{{{.State.Running}}}}' {CID}_node_0 2>/dev/null":
            self.event('recorded_container_liveness', host=host, running=not self.head_exits)
            return self.result(host, stdout='false' if self.head_exits else 'true')
        if command == self.executor.status_cmd(CID + '_node_0'):
            self.event('recorded_executor_liveness', host=host, running=not self.head_exits)
            return self.result(host, 1 if self.head_exits else 0)
        raise UnexpectedOperation('Unregistered host command: ' + command)

    def urlopen(self, request, **kwargs):
        url = request.full_url
        body = json.loads(request.data) if request.data else None
        if self.active_hook is None:
            assert url == f'http://{HOSTS[0]}:8888/v1/models'
            assert self.started == {0, 1} and self.api_probes == 2
            self.event('sdk_http_readiness', url=url, ready=not self.health_fails)
            if self.health_fails:
                raise urllib.error.URLError('synthetic endpoint refusal')
            self.sdk_ready = True
            return JsonResponse(self.models())
        assert self.active_hook == 'readiness' and url.startswith('http://127.0.0.1:8888/')
        path = url.removeprefix('http://127.0.0.1:8888')
        self.event('hook_http', path=path)
        if path == '/health':
            return JsonResponse({'status': 'ok', 'warming': False, 'model': 'GLM-5.3-Flash', 'max_batch_size': 4})
        if path == '/v1/models':
            result = self.models()
            if self.readiness_fails:
                result['data'][0]['context_length'] = 4096
            return JsonResponse(result)
        if path == '/tokenize':
            # Synthetic independent renderer: the hook must validate each level
            # and the nearest-level mapping using its real checks.
            token = {'max': 1, 'high': 2, 'low': 3, 'none': 4, 'minimal': 3, 'medium': 2, 'xhigh': 1}[body['reasoning_effort']]
            return JsonResponse({'tokens': [token]})
        assert path == '/v1/chat/completions' and body['model'] == 'GLM-5.3-Flash'
        if 'tools' in body:
            return JsonResponse({'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
                {'function': {'name': 'get_weather', 'arguments': '{"city":"Oslo"}'}}]}}]})
        lane = int(re.search(r'Smoke stream (\d+)', body['messages'][0]['content'])[1])
        self.event('smoke_lane', lane=lane, thread=threading.current_thread().name)
        self.smoke_barrier.wait(timeout=5)
        return JsonResponse({'choices': [{'finish_reason': 'stop', 'message': {'content': '323'}}],
                             'usage': {'prompt_tokens': 12, 'completion_tokens': 1}})

    @staticmethod
    def models():
        return {'data': [{'id': 'GLM-5.3-Flash', 'owned_by': 'tensorfold', 'context_length': 1048576,
                          'max_model_len': 1048576, 'reasoning': {'supported_efforts': ['max', 'high', 'low', 'none']}}]}

    def hook_script(self, host, script, **kwargs):
        assert host == HOSTS[0] and kwargs['dry_run'] is False
        assert self.sdk_ready and self.started == {0, 1}, 'post_exec ran before endpoint readiness'
        tokens = shlex.split(script)
        assert tokens[:4] == ['docker', 'exec', '--user', 'root']
        at = tokens.index('bash')
        assert tokens[at - 1] == CID + '_node_0' and tokens[at + 1] == '-c'
        payload = tokens[at + 2]
        encoded = re.findall(r'printf %s ([A-Za-z0-9+/=]+) \| base64 -d --', payload)
        assert len(encoded) == 1
        command = base64.b64decode(encoded[0], validate=True).decode()
        assert command in self.recipe.post_exec, 'Unexpected hook command'
        self.active_hook = 'capacity' if command == self.recipe.post_exec[0] else 'readiness'
        self.event('post_exec', hook=self.active_hook, command=command, timeout=kwargs['timeout'],
                   watchdog='__sr_timed_out=0' in payload, metadata='SPARKRUN_HOOK=post_exec' in script)
        output = io.StringIO()
        try:
            with patch.object(sys, 'argv', shlex.split(command)[1:]), redirect_stdout(output):
                if self.active_hook == 'capacity':
                    def container_path(path):
                        assert path == '/tmp/sparkrun_serve.log'
                        class Log:
                            def read_text(inner):
                                pages = 5043 if self.capacity_fails else 5044
                                return (f'[tensorfold] CUDA native taking {pages} pages of 512 rows\n'
                                        'info: prompt cache: 3.6 GiB of device pages and 0.0 GiB of pinned host memory a rank\n')
                        return Log()
                    with patch.object(self.ctx.capacity, 'Path', container_path):
                        self.ctx.capacity.main()
                else:
                    self.ctx.readiness.main()
        except (ValueError, RuntimeError) as error:
            self.event('hook_failed', hook=self.active_hook, error=str(error))
            return self.result(host, 1, stdout=output.getvalue(), stderr=str(error))
        finally:
            self.active_hook = None
        self.event('hook_passed', output=output.getvalue())
        return self.result(host, stdout=output.getvalue())

    def __enter__(self):
        self.stack = ExitStack()
        def replace(obj, name, value):
            self.stack.enter_context(patch.object(obj, name, value))
        launch_original = self.executor.generate_launch_script
        exec_original = self.executor.generate_exec_serve_script
        replace(self.executor, 'generate_launch_script', lambda **kw: self.capture_script('container', launch_original, **kw))
        replace(self.executor, 'generate_exec_serve_script', lambda **kw: self.capture_script('serve', exec_original, **kw))
        replace(self.executor, 'readiness_observer', lambda: None)
        replace(self.ctx.ssh, 'run_remote_script', self.remote_script)
        replace(self.ctx.ssh, 'run_remote_command', self.remote_command)
        replace(self.ctx.ssh, 'start_log_capture', lambda *a, **kw: self.event('recorded_log_capture'))
        replace(self.ctx.ssh, 'stop_log_capture', lambda *a, **kw: [])
        replace(self.ctx.primitives, 'run_command_on_host', self.host_command)
        replace(self.ctx.primitives, 'run_script_on_host', self.hook_script)
        replace(self.ctx.primitives, 'detect_host_ip', lambda host, **kw: host)
        replace(self.ctx.utils, 'is_local_host', lambda host: False)
        replace(self.ctx.health, '_interruptible_sleep', lambda seconds, cancel: self.event('recorded_retry', seconds=seconds) or False)
        replace(urllib.request, 'urlopen', self.urlopen)
        def forbidden_stop(*args, **kwargs):
            raise UnexpectedOperation('runtime.stop must never be called by this harness')
        replace(self.runtime, 'stop', forbidden_stop)
        return self

    def __exit__(self, *exc):
        self.ctx.scenarios[self.ctx.test_name] = self.events
        return self.stack.__exit__(*exc)

    def launch(self):
        comm = self.ctx.ClusterCommEnv.from_shared({'NCCL_IB_HCA': 'rail0,rail1', 'NCCL_IB_GID_INDEX': '3'})
        return self.runtime.run(hosts=HOSTS, image=self.recipe.container, serve_command='', recipe=self.recipe, overrides={},
                                cluster_id=CID, env=self.recipe.env, cache_dir='/offhost/huggingface', dry_run=False,
                                detached=True, follow=False, executor=self.executor, trust=True, init_port=GATE,
                                comm_env=comm, ib_ip_map=FABRIC, ib_iface_map={h: 'cx7' for h in HOSTS})

    def finish(self):
        result = self.ctx.launcher.LaunchResult(rc=0, cluster_id=CID, host_list=HOSTS, is_solo=False,
                                              runtime=self.runtime, recipe=self.recipe, overrides={},
                                              container_image=self.recipe.container, effective_cache_dir='/offhost/huggingface',
                                              serve_port=8888, config=None)
        if self.ctx.version == '0.4.2':
            return self.ctx.launcher.complete_post_launch(result, '/offhost/huggingface', trust=True, dry_run=False)
        return self.ctx.launcher.post_launch_lifecycle(result, '/offhost/huggingface', trust=True, dry_run=False)


class CompatibilityTests(unittest.TestCase):
    context = None

    def setUp(self):
        self.ctx = self.context
        self.ctx.test_name = self._testMethodName

    def assert_lifecycle_failure(self, launch):
        if self.ctx.version == '0.3.10':
            with self.assertRaises(SystemExit) as raised:
                launch.finish()
            self.assertEqual(raised.exception.code, 1)
        else:
            with self.assertRaisesRegex(RuntimeError, 'Post-launch readiness or hooks failed'):
                launch.finish()

    def test_plugin_discovery_and_api1(self):
        import tensorfold
        self.assertEqual(Path(tensorfold.__file__).resolve(), self.ctx.root / 'plugins/tensorfold/__init__.py')
        self.assertEqual(tensorfold.SPARKRUN_PLUGIN_API_VERSION, 1)
        self.assertIn('tensorfold', self.ctx.loaded)
        self.assertIsInstance(self.ctx.runtime, tensorfold.TensorFoldRuntime)
        self.assertEqual(self.ctx.runtime.cluster_strategy(), 'native')
        self.assertFalse([i for i in self.ctx.runtime.validate_recipe(self.ctx.Recipe(self.ctx.data)) if i.severity == 'error'])

    def test_native_tp2_waits_for_socket_table_before_rank1(self):
        with RecordedLaunch(self.ctx) as launch:
            self.assertEqual(launch.launch(), 0)
            events = launch.events
            kinds = [e['kind'] for e in events]
            probes = [e for e in events if e['kind'] == 'gate_socket_table']
            self.assertEqual([e['ready'] for e in probes], [False, True])
            self.assertLess(kinds.index('serve'), kinds.index('gate_socket_table'))
            self.assertGreater(max(i for i, e in enumerate(events) if e['kind'] == 'serve'),
                               max(i for i, e in enumerate(events) if e['kind'] == 'gate_socket_table'))
            for probe in probes:
                self.assertIn(f'ss -tln "sport = :{GATE}"', probe['command'])
                self.assertIn('/proc/net/tcp /proc/net/tcp6', probe['command'])
                self.assertIn('$4=="0A"', probe['command'])
                self.assertNotIn('nc ', probe['command'])
            self.assertEqual(launch.started, {0, 1})
            for rank, env in launch.rank_env.items():
                self.assertEqual(env['TF_GLM_RANK'], str(rank))
                self.assertEqual(env['TF_GLM_WORLD'], '2')
                self.assertEqual(env['TF_CUDA_DEVICE'], '0')
                self.assertEqual(env['FABRIC_MASTER'], FABRIC[HOSTS[0]])
                self.assertEqual(env['NCCL_COMM_ID'], f'{FABRIC[HOSTS[0]]}:{GATE - 1}')
                self.assertEqual(int(env['NCCL_COMM_ID'].rsplit(':', 1)[1]) + 1, GATE)
                self.assertEqual(env['MASTER_PORT'], str(GATE))
                self.assertEqual(env['NCCL_IB_HCA'], 'rail0,rail1')
                self.assertEqual(env['NCCL_IB_GID_INDEX'], '3')
                self.ctx.check_native_contract(self, launch.native_args[rank], env)
            self.assertNotIn('post_exec', kinds)

    def test_closed_gate_never_admits_rank1(self):
        with RecordedLaunch(self.ctx, gate_fails=True) as launch:
            self.assertEqual(launch.launch(), 1)
            self.assertEqual(launch.started, {0})
            self.assertEqual(launch.gate_probes, 60)
            self.assertTrue(any(e['kind'] == 'recorded_failure_log' for e in launch.events))
            self.assertFalse(any(e['kind'] == 'post_exec' for e in launch.events))

    def test_head_exit_aborts_gate_without_rank1(self):
        with RecordedLaunch(self.ctx, gate_fails=True, head_exits=True) as launch:
            self.assertEqual(launch.launch(), 1)
            self.assertEqual(launch.started, {0})
            self.assertEqual(launch.gate_probes, 1)

    def test_real_capacity_then_readiness_hooks_after_endpoint_ready(self):
        with RecordedLaunch(self.ctx) as launch:
            self.assertEqual(launch.launch(), 0)
            self.assertFalse(any(e['kind'] == 'post_exec' for e in launch.events))
            self.assertIs(launch.finish(), None if self.ctx.version == '0.3.10' else False)
            hooks = [e for e in launch.events if e['kind'] == 'post_exec']
            self.assertEqual([e['hook'] for e in hooks], ['capacity', 'readiness'])
            self.assertEqual([e['command'] for e in hooks], launch.recipe.post_exec)
            self.assertEqual([e['timeout'] for e in hooks], [600, 600] if self.ctx.version == '0.3.10' else [3640, 3640])
            self.assertEqual([e['watchdog'] for e in hooks], [self.ctx.version == '0.4.2'] * 2)
            self.assertEqual([e['metadata'] for e in hooks], [self.ctx.version == '0.4.2'] * 2)
            lanes = [e for e in launch.events if e['kind'] == 'smoke_lane']
            self.assertEqual({e['lane'] for e in lanes}, set(range(4)))
            self.assertEqual(len({e['thread'] for e in lanes}), 4)
            self.assertTrue(any('TensorFold readiness passed' in e.get('output', '') for e in launch.events))

    def test_capacity_failure_propagates_and_skips_readiness(self):
        with RecordedLaunch(self.ctx, capacity_fails=True) as launch:
            self.assertEqual(launch.launch(), 0)
            self.assert_lifecycle_failure(launch)
            self.assertEqual([e['hook'] for e in launch.events if e['kind'] == 'post_exec'], ['capacity'])
            self.assertIn('Insufficient GLM pool', next(e['error'] for e in launch.events if e['kind'] == 'hook_failed'))

    def test_readiness_failure_propagates_after_capacity(self):
        with RecordedLaunch(self.ctx, readiness_fails=True) as launch:
            self.assertEqual(launch.launch(), 0)
            self.assert_lifecycle_failure(launch)
            self.assertEqual([e['hook'] for e in launch.events if e['kind'] == 'post_exec'], ['capacity', 'readiness'])
            self.assertIn('context_length', next(e['error'] for e in launch.events if e['kind'] == 'hook_failed'))

    def test_sdk_readiness_failure_prevents_both_hooks(self):
        with RecordedLaunch(self.ctx, health_fails=True) as launch:
            self.assertEqual(launch.launch(), 0)
            self.assert_lifecycle_failure(launch)
            self.assertEqual(sum(e['kind'] == 'sdk_http_readiness' for e in launch.events), 2)
            self.assertFalse(any(e['kind'] == 'post_exec' for e in launch.events))

    def test_unmocked_process_and_network_are_blocked(self):
        self.ctx.guard.expected = True
        try:
            with self.assertRaises(UnexpectedOperation):
                subprocess.run(['/unmocked-child-must-not-start'], check=True)
            with self.assertRaises(UnexpectedOperation):
                socket.socket()
        finally:
            self.ctx.guard.expected = False


class Context:
    def __init__(self, args, guard):
        self.guard, self.args = guard, args
        self.version = importlib.metadata.version('sparkrun')
        assert self.version == args.expected_sdk
        assert sys.dont_write_bytecode, 'Use -B; immutable sources and SDKs must not get bytecode writes'
        self.root = args.repo / REL
        self.recipe_path = self.root / RECIPE
        self.scenarios, self.native_files, self.native_launches = {}, {}, {}
        import sparkrun
        self.sdk_root = Path(sparkrun.__file__).parent
        self.sdk_digest = sdk_source_digest(self.sdk_root)
        observed_sdk = ['core/bootstrap.py', 'core/external_plugins.py', 'core/launcher.py',
                        'core/registration.py', 'runtimes/base.py', 'runtimes/_cluster_ops.py',
                        'orchestration/health.py', 'orchestration/hooks.py', 'orchestration/executor.py',
                        'orchestration/executors/_base.py', 'orchestration/executors/docker.py']
        observed_repo = ['plugins/tensorfold/__init__.py', str(MOD / 'serve.py'), str(MOD / 'capacity.py'),
                         'mods/tensorfold-readiness/readiness.py']
        self.evidence = CallEvidence({str(base / path) for base, paths in [(self.sdk_root, observed_sdk), (self.root, observed_repo)] for path in paths})
        sys.setprofile(self.evidence.profile)
        threading.setprofile(self.evidence.profile)
        import yaml
        from sparkrun.core.bootstrap import init_sparkrun, get_runtime
        from sparkrun.core.external_plugins import load_external_plugins
        from sparkrun.core.recipe import Recipe
        from sparkrun.core import launcher
        from sparkrun.orchestration import health, ssh, primitives
        from sparkrun.orchestration.comm_env import ClusterCommEnv
        from sparkrun.orchestration.executor import resolve_executor
        from sparkrun.orchestration.ssh import RemoteResult
        import sparkrun.utils as utils
        self.Recipe, self.launcher, self.health, self.ssh, self.primitives = Recipe, launcher, health, ssh, primitives
        self.ClusterCommEnv, self.resolve_executor, self.RemoteResult, self.utils = ClusterCommEnv, resolve_executor, RemoteResult, utils
        self.data = yaml.safe_load(self.recipe_path.read_text())
        self.variables = init_sparkrun()
        self.loaded = load_external_plugins(self.variables, paths=[self.root / 'plugins'])
        self.runtime = get_runtime('tensorfold', self.variables)
        self.serve = load_file('offhost_glm_serve', self.root / MOD / 'serve.py')
        self.capacity = load_file('offhost_glm_capacity', self.root / MOD / 'capacity.py')
        self.readiness = load_file('offhost_glm_readiness', self.root / 'mods/tensorfold-readiness/readiness.py')
        self.native_manifest = json.loads((args.native_source / 'HOME-LAB-SOURCES.json').read_text())

    def missing_required_calls(self):
        required = {
            'core/bootstrap.py': ['init_sparkrun', 'get_runtime'],
            'core/external_plugins.py': ['load_external_plugins'],
            'runtimes/base.py': ['RuntimePlugin.run', 'RuntimePlugin._run_native_cluster'],
            'runtimes/_cluster_ops.py': ['ClusterContext.build', 'run_native_cluster', 'launch_containers_parallel', 'run_pre_serve_hooks'],
            'orchestration/executors/_base.py': ['Executor.generate_launch_script', 'Executor.generate_exec_serve_script'],
            'orchestration/health.py': ['wait_for_port', 'listen_probe_cmd', 'proc_listen_probe_cmd', 'wait_for_healthy'],
            'orchestration/hooks.py': ['run_post_exec', '_run_exec_command'],
            'core/launcher.py': ['wait_for_serve_ready', 'wait_for_endpoint_ready',
                                 'complete_post_launch' if self.version == '0.4.2' else 'post_launch_lifecycle'],
        }
        if self.version == '0.4.2':
            required['core/registration.py'] = ['load_and_register_plugin']
        missing = [f'{path}:{name}' for path, names in required.items() for name in names
                   if name not in self.evidence.calls.get(str(self.sdk_root / path), set())]
        for path, names in {MOD / 'serve.py': ['launch'], MOD / 'capacity.py': ['main', 'check'],
                            Path('mods/tensorfold-readiness/readiness.py'): ['main', 'check_health', 'check_models', 'smoke', 'tool_call', 'check_renders']}.items():
            missing += [f'{path}:{name}' for name in names if name not in self.evidence.calls.get(str(self.root / path), set())]
        return missing

    def native(self, relative):
        path = self.args.native_source / 'zig/src' / relative
        digest = sha(path)
        assert self.native_manifest['source_files'][str(Path('zig/src') / relative)] == digest
        self.native_files[str(path)] = digest
        return path.read_text()

    def check_native_contract(self, case, argv, env):
        cli = self.native('server/cli.zig')
        declarations = {re.search(r'\.name = "([^"]+)"', line)[1]: line for line in cli.splitlines() if '.name = "--' in line}
        case.assertEqual(argv[:3], [self.serve.BINARY, 'serve', env['MODEL_PATH']])
        index = 3
        while index < len(argv):
            flag = argv[index]
            declaration = declarations[flag]
            case.assertIn('.native = true', declaration, flag)
            index += 1
            if '.kind = .store_true' not in declaration:
                value = argv[index]
                index += 1
                choices = re.search(r'\.choices = &\.\{([^}]+)\}', declaration)
                if choices:
                    case.assertIn(value, re.findall(r'"([^"]+)"', choices[1]))
        for flag, value in {'--parallel': '4', '--context': '1048576', '--max-tokens': '32768', '--kv-cache': 'fp8',
                            '--drafter-bits': '4', '--request-body-mib': '96', '--reasoning-effort': 'xhigh',
                            '--vision-max-images': '50', '--vision-max-videos': '4', '--vision-image-tokens': '102400',
                            '--vision-video-tokens': '32768', '--max-frames': '128'}.items():
            case.assertEqual(argv[argv.index(flag) + 1], value)
        case.assertFalse({'--tp', '--rank', '--master', '--master-port', '--spill-gib', '--snapshot-dir'} & set(argv))
        consumers = {
            'server/cli.zig': ['TENSORFOLD_NO_UPDATE_CHECK', 'TENSORFOLD_NO_LIVE', 'TENSORFOLD_MEMORY_RESERVE_GIB'],
            'core/lane_host_fill.zig': ['TENSORFOLD_PREFILL_SHARE'],
            'families/glm/cuda_ranks.zig': ['TF_GLM_DENSE_BITS', 'TF_GLM_HEAD_BITS', 'TF_GLM_ABSORB_MMA', 'TF_GLM_W8A8'],
            'server/hf_text.zig': ['TF_GLM_CLEAR_THINKING'],
            'families/glm/cuda_native.zig': ['TF_KV_PAGES', 'TF_ROCE_MAX_KB', 'TF_GLM_RANK', 'TF_GLM_WORLD', 'NCCL_COMM_ID', 'TF_GLM_HOST_CACHE_GIB'],
            'families/glm/cuda_ce.zig': ['TF_GLM_CE_EXCHANGE'],
            'server/prepare_gate.zig': ['TF_PREPARE_RESIDENTS', 'TF_PREPARE_WAITING', 'TF_PREPARE_WAIT_MS'],
            'cuda/roce.zig': ['TF_ROCE', 'NCCL_IB_HCA'],
            'native/cuda.zig': ['TF_CUDA_DEVICE'],
        }
        covered = set()
        for relative, names in consumers.items():
            source = re.sub(r'//[^\n]*', '', self.native(relative))
            for name in names:
                case.assertIn('"' + name + '"', source)
                case.assertIn(name, env)
                covered.add(name)
        case.assertTrue(set(self.data['env']) <= covered)
        for key, value in self.data['env'].items():
            case.assertEqual(env[key], value)
        watch = self.native('cuda/tp_watch.zig')
        case.assertIn('id[colon + 1 ..], 10) catch return error.NoCommId) +% 1', watch)
        # The native RoCE code selects its own GID unless TF_ROCE_GID_INDEX is
        # supplied. NCCL_IB_GID_INDEX is preserved for NCCL, not translated.
        roce = self.native('cuda/roce.zig')
        case.assertIn('std.c.getenv("TF_ROCE_GID_INDEX")', roce)
        case.assertNotIn('TF_ROCE_GID_INDEX', env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--native-source', type=Path, required=True)
    parser.add_argument('--expected-sdk', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    guard = OffHostGuard()
    sys.addaudithook(guard.audit)
    test_output, incidental = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory(prefix='offhost-sdk-state-') as scratch, patch.dict(os.environ, {
        'STATEFUL_ROOT': scratch, 'XDG_CONFIG_HOME': scratch, 'XDG_CACHE_HOME': scratch,
        'SPARKRUN_NO_EXTERNAL_PLUGINS': '1',
    }), redirect_stdout(incidental), redirect_stderr(incidental):
        context = Context(args, guard)
        CompatibilityTests.context = context
        result = unittest.TextTestRunner(stream=test_output, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CompatibilityTests))
        sys.setprofile(None)
        threading.setprofile(None)
    unexpected = [event for event in guard.blocked if not event['expected_self_test']]
    missing = context.missing_required_calls()
    sdk_unchanged = context.sdk_digest == sdk_source_digest(context.sdk_root)
    report = {
        'sdk_version': context.version, 'sdk_root': str(context.sdk_root), 'python': sys.version,
        'lifecycle_entry': 'post_launch_lifecycle (SystemExit(1) on failure)' if context.version == '0.3.10' else 'complete_post_launch (RuntimeError on failure)',
        'plugin_api': 'API 1 declaration accepted; 0.3.10 loader ignores the declaration' if context.version == '0.3.10' else 'API 1 declaration enforced by external loader',
        'tests_run': result.testsRun, 'passed': result.wasSuccessful() and not unexpected and not missing and sdk_unchanged,
        'missing_required_calls': missing, 'sdk_sources_unchanged': sdk_unchanged,
        'sdk_py_sh_tree_sha256': context.sdk_digest,
        'script_templates_sha256': {str(path.relative_to(context.sdk_root)): sha(path)
                                     for path in [context.sdk_root / 'scripts/container_launch.sh', context.sdk_root / 'scripts/exec_serve_detached.sh']},
        'test_output': test_output.getvalue(), 'blocked_external_attempts': guard.blocked,
        'scenarios': context.scenarios, 'native_launch': context.native_launches,
        'exercised_sources': context.evidence.manifest(),
        'recipe_sha256': sha(context.recipe_path), 'native_source_sha256': context.native_files,
        'native_source_manifest_sha256': sha(args.native_source / 'HOME-LAB-SOURCES.json'),
        'native_revision': {key: context.native_manifest[key] for key in ('engine_revision', 'kit_revision', 'site_patch_count', 'site_patches_sha256')},
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
