"""Exercise the real controller with a harmless engine-owned file/service fixture."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
sys.dont_write_bytecode = True
import time

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hostcfg', SOURCE / 'engine/controller.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(directory, *args):
    return subprocess.check_output(['git', '-C', str(directory), *args], text=True).strip()


with tempfile.TemporaryDirectory(prefix='pve-hostcfg-test-') as temp:
    base = Path(temp)
    work = base / 'work'
    work.mkdir()
    git(work, 'init', '-b', 'main')
    git(work, 'config', 'user.name', 'fixture')
    git(work, 'config', 'user.email', 'fixture@invalid')
    payload = work / module.PAYLOAD
    payload.mkdir(parents=True)
    remote = base / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, stdout=subprocess.DEVNULL)
    git(work, 'remote', 'add', 'origin', str(remote))
    node = dict(enabled=True, node_exporter='present', collectors=['systemd'])

    def commit(message, write=True):
        if write:
            (payload / 'nodes.json').write_text(json.dumps({'pve-sbx-1': node}))
        git(work, 'add', '.')
        git(work, 'commit', '-m', message)
        git(work, 'push', 'origin', 'main')
        return git(work, 'rev-parse', 'HEAD')

    engine = base / 'engine'
    engine.mkdir()
    # Restrict inventory/config just as production does; use the local interpreter.
    cfg = (SOURCE / 'engine/ansible.cfg').read_text()
    cfg = cfg.replace('/usr/bin/python3', shutil.which('python3'))
    (engine / 'ansible.cfg').write_text(cfg)
    (engine / 'node-exporter.yml').write_text('''- hosts: localhost
  gather_facts: false
  tasks:
    - name: Simulated owned config (real atomic Ansible copy)
      ansible.builtin.copy:
        dest: "{{ engine_dir }}/owned"
        content: "{{ collectors | join(',') }}"
        mode: '0644'
      when: exporter_state == 'present'
    - name: Controlled failure after the first mutation
      ansible.builtin.command: /bin/false
      changed_when: false
      when: exporter_state == 'present' and lookup('ansible.builtin.fileglob', engine_dir + '/fail') | length > 0
    - name: Explicit owned removal
      ansible.builtin.file:
        path: "{{ engine_dir }}/owned"
        state: absent
      when: exporter_state == 'absent'
''')
    config = dict(state_dir=str(base / 'state'), engine_dir=str(engine),
                  textfile_dir=str(base / 'textfile'), repository=str(remote), branch='main',
                  host_id='pve-sbx-1', ansible=os.environ['ANSIBLE_PLAYBOOK'], fetch_timeout=2)

    def tick(command='sync', expected=0):
        rc = module.Controller(config).tick(command)
        assert rc == expected, (command, rc, expected)
        return json.loads((base / 'state/status.json').read_text())

    first = commit('initial')
    s = tick()
    assert s['applied'] == first and s['drift'] == 0 and s['attempt_success'] == 1
    stamp = s['attempt_timestamp_seconds']
    modified = (engine / 'owned').stat().st_mtime_ns
    s = tick()
    assert s['attempt_timestamp_seconds'] == stamp
    assert (engine / 'owned').stat().st_mtime_ns == modified
    (work / 'unrelated').write_text('unrelated')
    unrelated = commit('unrelated', False)
    s = tick()
    assert s['desired'] == unrelated and s['applied'] == first and s['attempt_timestamp_seconds'] == stamp
    # A dirty worktree is never a source; only the fetched immutable commit is read.
    (payload / 'nodes.json').write_text('not committed')
    assert tick()['applied'] == first
    git(work, 'checkout', '--', '.')
    # Real missing bare remote: retained exporter/config and applied revision.
    remote.rename(base / 'offline.git')
    start = time.monotonic()
    s = tick(expected=1)
    assert time.monotonic() - start < 10 and s['fetch_success'] == 0 and s['applied'] == first
    (base / 'offline.git').rename(remote)
    # Real Git transport hang: external remote helper lives only in this fixture.
    helper = base / 'git-remote-hang'
    helper.write_text('#!/bin/sh\nsleep 30\n')
    helper.chmod(0o755)
    controller = module.Controller({**config, 'repository': 'hang::offline'})
    controller.env['PATH'] = str(base) + ':' + controller.env['PATH']
    start = time.monotonic()
    assert controller.tick('sync') == 1
    assert time.monotonic() - start < 10
    # Invalid payload retries without advancing applied or overwriting good config.
    node['collectors'] = ['BAD FLAG']
    invalid = commit('invalid')
    for _ in range(2):
        s = tick(expected=1)
        assert s['desired'] == invalid and s['attempt'] == invalid and s['applied'] == first
        assert s['attempt_success'] == 0 and (engine / 'owned').read_text() == 'systemd'
    # Partial apply: failed revision remains dirty; same SHA succeeds after fault clears.
    node['collectors'] = ['cpu']
    partial = commit('partial')
    (engine / 'fail').touch()
    s = tick(expected=1)
    assert s['applied'] == first and s['dirty'] == 1 and s['drift'] == 1
    assert (engine / 'owned').read_text() == 'cpu'
    (engine / 'fail').unlink()
    s = tick()
    assert s['applied'] == partial and s['dirty'] == 0 and s['attempt_success'] == 1
    # Failed apply followed by revert to APPLIED content must also recover.
    node['collectors'] = ['diskstats']
    commit('second partial')
    (engine / 'fail').touch()
    tick(expected=1)
    (engine / 'fail').unlink()
    node['collectors'] = ['cpu']
    recovered = commit('revert failed config')
    assert tick()['applied'] == recovered
    assert (engine / 'owned').read_text() == 'cpu'
    # Nonblocking lock never fetches or applies or corrupts status.
    with (base / 'state/lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = (base / 'state/status.json').read_text()
        assert module.Controller(config).tick('sync') == 0
        assert (base / 'state/status.json').read_text() == before
        assert module.Controller(config).tick('pause') == 0
        assert (base / 'state/paused').exists()
    tick('pause')
    node['collectors'] = ['systemd']
    reverted = commit('revert')
    s = tick()
    assert s['paused'] == 1 and s['desired'] == reverted and s['applied'] == recovered
    # A fresh Controller represents a new process after reboot: pause is a durable file.
    assert tick()['paused'] == 1 and (engine / 'owned').read_text() == 'cpu'
    tick('resume')
    assert tick()['applied'] == reverted
    # Drift check returns changed=1 with rc=0; report it without repairing.
    (engine / 'owned').write_text('manual drift')
    before = (engine / 'owned').stat().st_mtime_ns
    s = tick('check')
    assert s['drift'] == 1 and (engine / 'owned').read_text() == 'manual drift'
    assert (engine / 'owned').stat().st_mtime_ns == before
    assert tick()['drift'] == 1  # no automatic repair on an unchanged desired tree
    node['node_exporter'] = 'absent'
    removal = commit('explicit removal')
    s = tick()
    assert s['applied'] == removal and s['drift'] == 0 and not (engine / 'owned').exists()
    metrics = (base / 'textfile/pve-hostcfg.prom').read_text()
    assert f'revision="{removal}"' in metrics and 'pve_hostcfg_attempt_success 1' in metrics
    # Enrollment is independently gated.
    node['node_exporter'] = 'present'
    node['enabled'] = False
    commit('disabled')
    assert tick()['applied'] == removal and not (engine / 'owned').exists()
    # Applied history stays available even when the source force-pushes a new root.
    git(work, 'checkout', '--orphan', 'replacement')
    git(work, 'rm', '-rf', '.')
    payload.mkdir(parents=True, exist_ok=True)
    node['enabled'] = True
    node['node_exporter'] = 'absent'
    (payload / 'nodes.json').write_text(json.dumps({'pve-sbx-1': node}))
    git(work, 'add', '.')
    git(work, 'commit', '-m', 'replacement history, same applied payload')
    git(work, 'branch', '-f', 'main', 'HEAD')
    git(work, 'push', '--force', 'origin', 'main')
    s = tick()
    assert s['applied'] == removal
    cache = base / 'state/repo.git'
    assert git(cache, 'rev-parse', 'refs/heads/applied') == removal
    git(cache, 'gc', '--prune=now')
    assert tick('check')['drift'] == 0
    print('PASS: real Git/Ansible no-op, relevance, immutable source, outage/timeout, retries, partial/revert recovery, lock, pause, drift/no repair, removal, status, enrollment')
