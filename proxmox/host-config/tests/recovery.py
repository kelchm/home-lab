#!/usr/bin/python3
"""Scoped timeout/recovery qualification ONLY on the disposable Debian VM 990.
Run from a reviewed engine snapshot. Restores the initial exporter unit and leaves paused.
"""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
assert os.geteuid() == 0
assert subprocess.check_output(['hostname', '-s'], text=True).strip() == 'monitoring-eval-990'
assert not Path('/etc/pve').exists()
SOURCE = Path(__file__).resolve().parents[1] / 'engine'
spec = importlib.util.spec_from_file_location('hostcfg', SOURCE / 'controller.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REAL_BASE = Path('/var/lib/pve-hostcfg')
UNIT = Path('/etc/systemd/system/prometheus-node-exporter.service')
POLICY = Path('/usr/sbin/policy-rc.d')
assert (REAL_BASE / 'paused').exists()
assert UNIT.is_file() and not UNIT.is_symlink()
original_unit = UNIT.read_bytes()
original_policy = POLICY.read_bytes() if POLICY.exists() else None
original_policy_mode = POLICY.stat().st_mode if POLICY.exists() else None
policy = original_policy if original_policy is not None else b'#!/bin/sh\n# retained qualification policy\nexit 0\n'
evidence = {}


def run(args, check=True):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout, flush=True)
    if check:
        assert result.returncode == 0, (args, result.returncode)
    return result


def git(work, *args):
    return run(['git', '-C', str(work), *args]).stdout.strip()


def pid():
    return int(run(['systemctl', 'show', 'prometheus-node-exporter.service', '-p', 'MainPID', '--value']).stdout)


def active():
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = subprocess.run(['systemctl', 'is-active', '--quiet', 'prometheus-node-exporter.service'])
        result = subprocess.check_output(['ss', '-H', '-lnt', 'sport', '=', ':9100'], text=True)
        if state.returncode == 0 and result and all('127.0.0.1:9100' in line for line in result.splitlines()):
            print(result, flush=True)
            return
        time.sleep(0.05)
    raise AssertionError('restored exporter did not become active on loopback')


run(['systemctl', 'stop', 'pve-hostcfg.timer', 'pve-hostcfg.service'])
with (REAL_BASE / 'lock').open('a') as real_lock, tempfile.TemporaryDirectory(prefix='pve-hostcfg-recovery-') as temp:
    fcntl.flock(real_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    base = Path(temp)
    engine = base / 'engine'
    shutil.copytree(SOURCE, engine)
    native_play = (engine / 'node-exporter.yml').read_text()
    work = base / 'work'
    work.mkdir()
    remote = base / 'remote.git'
    run(['git', 'init', '--bare', str(remote)])
    git(work, 'init', '-b', 'main')
    git(work, 'config', 'user.name', 'qualification')
    git(work, 'config', 'user.email', 'qualification@invalid')
    git(work, 'remote', 'add', 'origin', str(remote))
    payload = work / module.PAYLOAD
    payload.mkdir(parents=True)
    node = dict(enabled=True, node_exporter='present', collectors=['systemd'])
    config = dict(state_dir=str(base / 'state'), engine_dir=str(engine), textfile_dir=str(base / 'textfile'),
                  repository=str(remote), branch='main', host_id='pve-sbx-1', apply_timeout=300)

    def commit(message):
        (payload / 'nodes.json').write_text(json.dumps({'pve-sbx-1': node}))
        git(work, 'add', '.')
        git(work, 'commit', '-m', message)
        git(work, 'push', 'origin', 'main')
        return git(work, 'rev-parse', 'HEAD')

    def tick(command='sync', expected=0, selected=config):
        result = module.Controller(selected).tick(command)
        assert result == expected, (command, result, expected)
        assert POLICY.read_bytes() == policy
        assert POLICY.stat().st_mode == policy_mode
        return json.loads((Path(selected['state_dir']) / 'status.json').read_text())

    stop = threading.Event()
    bad_listeners, policy_changes = [], []

    def watch():
        while not stop.is_set():
            listeners = subprocess.run(['ss', '-H', '-lnt', 'sport', '=', ':9100'], text=True, capture_output=True)
            bad_listeners.extend(line for line in listeners.stdout.splitlines() if '127.0.0.1:9100' not in line)
            if not POLICY.exists() or POLICY.read_bytes() != policy or POLICY.stat().st_mode != policy_mode:
                policy_changes.append(time.time())
            stop.wait(0.05)

    if original_policy is None:
        POLICY.write_bytes(policy)
        POLICY.chmod(0o755)
    policy_mode = POLICY.stat().st_mode
    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        first = commit('initial scoped qualification')
        assert tick()['applied'] == first
        active()
        # Unchanged clean applies do not restart the live process.
        first_pid = pid()
        assert tick()['applied'] == first and pid() == first_pid
        evidence['clean_noop_pid'] = first_pid
        # Schema rejection occurs before any template publication or activation.
        valid_unit = UNIT.read_bytes()
        node['collectors'] = ['systemd', 'systemd']
        duplicate = commit('duplicate collector rejection')
        s = tick(expected=1)
        assert s['desired'] == duplicate and s['applied'] == first and s['attempt_success'] == 0
        assert UNIT.read_bytes() == valid_unit and pid() == first_pid
        evidence['duplicate_rejected_before_publication'] = True

        # A real Ansible shell worker calls setsid internally. Both runtime expiry
        # and an abnormal wrapper timeout must kill it before drift or lock release.
        worker_play = '''- hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.copy:
        dest: "{{ engine_dir }}/owned"
        content: "{{ collectors | join(',') }}"
        mode: '0644'
    - ansible.builtin.shell: "trap '' TERM; echo started > {{ engine_dir }}/started; sleep 23; echo escaped > {{ engine_dir }}/escaped"
      when: not ansible_check_mode and lookup('ansible.builtin.fileglob', engine_dir + '/hang') | length > 0
'''
        (engine / 'node-exporter.yml').write_text(worker_play)
        node['collectors'] = ['systemd']
        baseline = commit('harmless worker baseline')
        worker_config = {**config, 'state_dir': str(base / 'worker-state'), 'apply_timeout': 6}
        assert tick(selected=worker_config)['applied'] == baseline
        for kind in ('runtime', 'wrapper'):
            node['collectors'] = ['cpu'] if kind == 'runtime' else ['diskstats']
            failed = commit(kind + ' detached worker timeout')
            (engine / 'hang').touch()
            (engine / 'started').unlink(missing_ok=True)
            (engine / 'escaped').unlink(missing_ok=True)
            selected = {**worker_config, 'apply_timeout': 6 if kind == 'runtime' else 60}
            original_run = module.run
            if kind == 'wrapper':
                def shortened(args, timeout=60, env=None, cwd=None, on_interrupt=None):
                    if args[0] == 'systemd-run' and '--property=RuntimeMaxSec=60' in args:
                        timeout = 6  # Unit runtime is deliberately much longer.
                    return original_run(args, timeout, env, cwd, on_interrupt)
                module.run = shortened
            before = time.monotonic()
            try:
                s = tick(expected=1, selected=selected)
            finally:
                module.run = original_run
            elapsed = time.monotonic() - before
            assert (engine / 'started').exists() and elapsed < 22
            assert s['applied'] == baseline and s['dirty'] == 1 and s['attempt_success'] == 0
            assert s['drift'] == 1 and 'play_unit' not in s and not (engine / 'escaped').exists()
            with (Path(selected['state_dir']) / 'lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Beyond the shell's 23s deadline: a released lock cannot hide a live worker.
            time.sleep(max(0, 25 - (time.time() - (engine / 'started').stat().st_mtime)))
            assert not (engine / 'escaped').exists()
            evidence[kind + '_worker_terminated_before_failed_tick_return'] = True
            evidence[kind + '_failed_tick_seconds'] = round(elapsed, 3)
            (engine / 'hang').unlink()
            baseline = failed
            assert tick(selected=worker_config)['applied'] == baseline

        # Exercise main()'s real signal handler with an isolated root-owned config.
        # SIGKILL bypasses cleanup; the next lock holder must settle the recorded unit.
        child_code = '''import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('hostcfg', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original_path = module.Path
config = sys.argv[2]
module.Path = lambda path: original_path(config if str(path) == '/etc/pve-hostcfg/config.json' else path)
sys.argv = ['controller.py', 'sync']
module.main()
'''
        for kind, signum in (('sigterm', signal.SIGTERM), ('sigkill', signal.SIGKILL)):
            node['collectors'] = ['loadavg'] if kind == 'sigterm' else ['meminfo']
            interrupted_worker = commit(kind + ' manual controller interruption')
            (engine / 'hang').touch()
            (engine / 'started').unlink(missing_ok=True)
            (engine / 'escaped').unlink(missing_ok=True)
            selected = {**worker_config, 'apply_timeout': 60}
            child_config = base / 'child-config.json'
            child_config.write_text(json.dumps(selected))
            child_config.chmod(0o600)
            with (base / 'child.log').open('w') as log:
                child = subprocess.Popen(['/usr/bin/python3', '-c', child_code, str(engine / 'controller.py'), str(child_config)], stdout=log, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 20
                    while not (engine / 'started').exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    assert (engine / 'started').exists(), (base / 'child.log').read_text()
                    with (Path(selected['state_dir']) / 'lock').open('a') as lock:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            pass
                        else:
                            raise AssertionError('manual controller released its lock while worker was running')
                    child.send_signal(signum)
                    assert child.wait(timeout=15) == (143 if kind == 'sigterm' else -signal.SIGKILL)
                    recorded = json.loads((Path(selected['state_dir']) / 'status.json').read_text())
                    assert ('play_unit' in recorded) == (kind == 'sigkill')
                    s = tick('check', selected=selected)
                    assert 'play_unit' not in s and s['dirty'] == 1 and s['applied'] == baseline
                    time.sleep(max(0, 25 - (time.time() - (engine / 'started').stat().st_mtime)))
                    assert not (engine / 'escaped').exists()
                    evidence[kind + '_manual_controller_worker_settled'] = True
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
                    # Also settle a recorded play if an assertion interrupted the test.
                    cleanup = module.Controller(selected)
                    if cleanup.s.get('play_unit'):
                        cleanup.tick('check')
            (engine / 'hang').unlink()
            baseline = interrupted_worker
            assert tick(selected=worker_config)['applied'] == baseline

        # Publish a validated template, then kill the play before activation.
        # Same-SHA retry must reload and restart even when template.changed is false.
        (engine / 'node-exporter.yml').write_text(native_play.replace(
            '    - name: Activate the validated loopback exporter',
            '''    - name: Inject interruption after validated publication
      ansible.builtin.shell: "echo started > {{ engine_dir }}/published; sleep 23; echo escaped > {{ engine_dir }}/publication-escaped"
      when: not ansible_check_mode and lookup('ansible.builtin.fileglob', engine_dir + '/interrupt') | length > 0
    - name: Activate the validated loopback exporter'''))
        node['collectors'] = ['cpu']
        interrupted = commit('interrupted unit publication')
        (engine / 'interrupt').touch()
        old_pid = pid()
        s = tick(expected=1, selected={**config, 'apply_timeout': 8})
        assert (engine / 'published').exists() and b'--collector.cpu' in UNIT.read_bytes()
        assert pid() == old_pid and b'--collector.cpu' not in Path(f'/proc/{old_pid}/cmdline').read_bytes()
        assert s['applied'] == first and s['dirty'] == 1 and s['attempt_success'] == 0
        published = UNIT.read_bytes()
        (engine / 'interrupt').unlink()
        s = tick()
        new_pid = pid()
        assert s['applied'] == interrupted and s['dirty'] == 0 and s['attempt_success'] == 1
        assert UNIT.read_bytes() == published and new_pid != old_pid
        assert b'--collector.cpu' in Path(f'/proc/{new_pid}/cmdline').read_bytes()
        assert not (engine / 'publication-escaped').exists()
        active()
        evidence['same_sha_retry_activated_published_unit'] = dict(old_pid=old_pid, new_pid=new_pid, sha=interrupted)

        # Mask alone contains a first install with an existing exit-0 policy.
        # Explicit removal retains dependency packages and all other owner files.
        (engine / 'node-exporter.yml').write_text(native_play)
        node['node_exporter'] = 'absent'
        removed = commit('remove before masked first install')
        assert tick()['applied'] == removed and not UNIT.exists()
        validator = engine / 'validate-unit'
        original_validator = validator.read_bytes()
        backup = engine / 'validate-unit.original'
        backup.write_bytes(original_validator)
        backup.chmod(0o755)
        validator.write_text('#!/bin/sh\n' + shlex.quote(str(backup)) + ' "$@" || exit $?\nexit 1\n')
        validator.chmod(0o755)
        node['node_exporter'] = 'present'
        installed = commit('masked first installation')
        s = tick(expected=1)
        assert s['applied'] == removed and s['dirty'] == 1 and s['attempt_success'] == 0
        assert run(['systemctl', 'show', 'prometheus-node-exporter.service', '-p', 'LoadState', '--value']).stdout.strip() == 'masked'
        assert run(['systemctl', 'is-active', '--quiet', 'prometheus-node-exporter.service'], False).returncode != 0
        assert POLICY.read_bytes() == policy
        validator.write_bytes(original_validator)
        validator.chmod(0o755)
        assert tick()['applied'] == installed
        active()
        assert tick('check')['drift'] == 0
        evidence['mask_alone_contained_install_with_unchanged_operator_policy'] = True
        evidence['no_nonloopback_listeners'] = not bad_listeners
        evidence['operator_policy_unchanged_through_apply_check_and_interruption'] = not policy_changes
        assert not bad_listeners and not policy_changes, (bad_listeners, policy_changes)
    finally:
        stop.set()
        watcher.join()
        # No live task can outlast teardown: every controller path settled its unit.
        UNIT.write_bytes(original_unit)
        UNIT.chmod(0o644)
        if subprocess.run(['dpkg-query', '-W', '-f=${Status}', 'prometheus-node-exporter'], capture_output=True).stdout != b'install ok installed':
            run(['apt-get', 'install', '-y', '--no-install-recommends', '--no-remove', 'prometheus-node-exporter=1.9.0-1+b4'])
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'restart', 'prometheus-node-exporter.service'])
        active()
        if original_policy is None:
            POLICY.unlink()
        else:
            assert POLICY.read_bytes() == original_policy and POLICY.stat().st_mode == original_policy_mode
        assert not run(['systemctl', 'list-units', '--all', '--no-legend', 'pve-hostcfg-play-*']).stdout.strip()
        run(['systemctl', 'enable', '--now', 'pve-hostcfg.timer'])

assert (REAL_BASE / 'paused').exists()
Path('/tmp/pve-channel-recovery-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
print(json.dumps(evidence, indent=2))
print('PASS: real scoped Ansible runtime/wrapper and manual-controller interruption, dirty same-SHA live activation, duplicate rejection, mask-only install, retained operator policy, clean no-op; original exporter restored, paused')
