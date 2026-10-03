#!/usr/bin/python3
"""Destructive qualification ONLY on the pre-created disposable Debian VM 990.
Run before-reboot, reboot it externally, then run after-reboot. Leaves paused.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

assert os.geteuid() == 0
assert subprocess.check_output(['hostname', '-s'], text=True).strip() == 'monitoring-eval-990'
assert not Path('/etc/pve').exists()
CTL = '/usr/local/lib/pve-hostcfg/controller.py'
BASE = Path('/var/lib/pve-hostcfg')
ROOT = Path('/tmp/pve-eval')
UNIT = Path('/etc/systemd/system/prometheus-node-exporter.service')
ENGINE = Path('/usr/local/lib/pve-hostcfg')
EVIDENCE = ROOT / 'guest-evidence.json'


def run(args, check=True):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout, flush=True)
    if check:
        assert result.returncode == 0, (args, result.returncode)
    return result


def status():
    return json.loads((BASE / 'status.json').read_text())


def tick(command='sync', expected=0):
    result = run([CTL, command], False)
    assert result.returncode == expected, (command, result.returncode, expected)
    return status()


def active():
    assert run(['systemctl', 'is-active', 'prometheus-node-exporter.service']).stdout.strip() == 'active'
    metrics = subprocess.check_output(['curl', '-fsS', 'http://127.0.0.1:9100/metrics'], text=True)
    assert 'node_uname_info' in metrics and 'pve_hostcfg_revision_info' in metrics
    listeners = run(['ss', '-H', '-lnt', 'sport', '=', ':9100']).stdout
    assert listeners and all('127.0.0.1:9100' in line for line in listeners.splitlines())


if sys.argv[1] == 'after-reboot':
    evidence = json.loads(EVIDENCE.read_text())
    assert run(['cat', '/proc/sys/kernel/random/boot_id']).stdout.strip() != evidence['boot_id']
    assert (BASE / 'paused').exists()
    before = status()['applied']
    assert before == evidence['applied_before_reboot']
    run(['systemctl', 'start', 'pve-hostcfg.service'])
    s = status()
    assert s['paused'] == 1 and s['desired'] == evidence['pending'] and s['applied'] == before
    assert run(['systemctl', 'is-enabled', 'pve-hostcfg.timer']).stdout.strip() == 'enabled'
    active()
    evidence['reboot_pause_survival'] = True
    evidence['final_status'] = s
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + '\n')
    print('PASS: real reboot, retained service, persistent pause, pending revision never applied')
    raise SystemExit(0)

assert sys.argv[1] == 'before-reboot'
assert (BASE / 'paused').exists()
run(['systemctl', 'stop', 'pve-hostcfg.timer'])
work = ROOT / 'work'
work.mkdir()
remote = ROOT / 'fixture.git'
run(['git', 'init', '--bare', str(remote)])
run(['git', '-C', str(work), 'init', '-b', 'main'])
run(['git', '-C', str(work), 'config', 'user.name', 'qualification'])
run(['git', '-C', str(work), 'config', 'user.email', 'qualification@invalid'])
run(['git', '-C', str(work), 'remote', 'add', 'origin', str(remote)])
payload = work / 'proxmox/host-config/payload'
payload.mkdir(parents=True)
node = dict(enabled=True, node_exporter='present', collectors=['systemd'])


def commit(message, write=True):
    if write:
        (payload / 'nodes.json').write_text(json.dumps({'pve-sbx-1': node}))
    run(['git', '-C', str(work), 'add', '.'])
    run(['git', '-C', str(work), 'commit', '-m', message])
    run(['git', '-C', str(work), 'push', 'origin', 'main'])
    return run(['git', '-C', str(work), 'rev-parse', 'HEAD']).stdout.strip()


first = commit('initial qualification payload')
s = tick()
assert s['desired'] == first and not s.get('applied') and s['paused'] == 1
# Fault injection after a real package installation, before unit activation.
validator = ENGINE / 'validate-unit'
original = validator.read_bytes()
policy = Path('/usr/sbin/policy-rc.d')
assert not policy.exists(), 'fresh guest expected; do not replace an unknown policy'
sentinel = b'#!/bin/sh\n# existing qualification policy, must survive apt\nexit 0\n'
policy.write_bytes(sentinel)
policy.chmod(0o755)
backup = ENGINE / 'validate-unit.original'
backup.write_bytes(original)
backup.chmod(0o755)
validator.write_text('#!/bin/sh\n' + str(backup) + ' "$@" || exit $?\n[ ! -e /tmp/pve-eval/inject-failure ]\n')
validator.chmod(0o755)
(ROOT / 'inject-failure').touch()
stop = threading.Event()
bad_listeners = []


def watch():
    while not stop.is_set():
        result = subprocess.run(['ss', '-H', '-lnt', 'sport', '=', ':9100'], text=True, capture_output=True)
        bad_listeners.extend(line for line in result.stdout.splitlines() if '127.0.0.1:9100' not in line)
        stop.wait(0.05)


thread = threading.Thread(target=watch)
thread.start()
try:
    tick('resume')
    s = tick(expected=1)
    assert s['dirty'] == 1 and not s.get('applied') and s['attempt_success'] == 0
    assert 'LoadState=masked' in run(['systemctl', 'show', 'prometheus-node-exporter.service', '-p', 'LoadState']).stdout
    assert policy.read_bytes() == sentinel
    assert run(['systemctl', 'is-active', 'prometheus-node-exporter.service'], False).returncode != 0
    (ROOT / 'inject-failure').unlink()
    s = tick()
    assert s['applied'] == first and s['dirty'] == 0 and s['drift'] == 0
    assert policy.read_bytes() == sentinel
    active()
finally:
    stop.set()
    thread.join()
    validator.write_bytes(original)
    validator.chmod(0o755)
    backup.unlink(missing_ok=True)
    policy.unlink(missing_ok=True)
assert not bad_listeners, bad_listeners
print('PASS: blocked maintainer start, retained existing policy, masked interrupted install, same-SHA recovery, loopback activation')
# No-op and unrelated engine/docs commits cannot update the installed engine or restart the exporter.
attempt = status()['attempt_timestamp_seconds']
engine_hash = hashlib.sha256((ENGINE / 'controller.py').read_bytes()).hexdigest()
start_timestamp = run(['systemctl', 'show', 'prometheus-node-exporter.service', '-p', 'ExecMainStartTimestampMonotonic']).stdout
(work / 'README.md').write_text('unrelated')
(work / 'proxmox/host-config/engine').mkdir()
(work / 'proxmox/host-config/engine/controller.py').write_text('not deployed')
unrelated = commit('unrelated docs and engine changes', False)
s = tick()
assert s['desired'] == unrelated and s['applied'] == first and s['attempt_timestamp_seconds'] == attempt
assert hashlib.sha256((ENGINE / 'controller.py').read_bytes()).hexdigest() == engine_hash
assert run(['systemctl', 'show', 'prometheus-node-exporter.service', '-p', 'ExecMainStartTimestampMonotonic']).stdout == start_timestamp
assert tick()['applied'] == first
valid_unit = UNIT.read_bytes()
node['collectors'] = ['not_a_collector']
invalid = commit('invalid flags')
for _ in range(2):
    s = tick(expected=1)
    assert s['desired'] == invalid and s['applied'] == first and s['attempt_success'] == 0
    assert UNIT.read_bytes() == valid_unit
active()
remote.rename(ROOT / 'offline.git')
before = time.monotonic()
s = tick(expected=1)
assert time.monotonic() - before < 60 and s['fetch_success'] == 0 and s['applied'] == first
active()
(ROOT / 'offline.git').rename(remote)
node['collectors'] = ['cpu']
changed = commit('valid changed collectors')
assert tick()['applied'] == changed
run(['git', '-C', str(work), 'revert', '--no-edit', changed])
# The actual Git revert restores the prior invalid commit; revert twice to restore valid data.
run(['git', '-C', str(work), 'revert', '--no-edit', invalid])
run(['git', '-C', str(work), 'push', 'origin', 'main'])
reverted = run(['git', '-C', str(work), 'rev-parse', 'HEAD']).stdout.strip()
assert tick()['applied'] == reverted
assert UNIT.read_bytes() == valid_unit
# Out-of-band drift must be reported, never repaired on check or unchanged sync.
UNIT.write_bytes(valid_unit.replace(b'Restart=on-failure', b'Restart=always'))
manual = UNIT.read_bytes()
assert tick('check')['drift'] == 1 and UNIT.read_bytes() == manual
assert tick()['drift'] == 1 and UNIT.read_bytes() == manual
node['collectors'] = ['systemd']
node['node_exporter'] = 'absent'
removal = commit('explicit exporter removal')
retained = BASE / 'textfile/operator-owned.prom'
retained.write_text('qualification_retained 1\n')
s = tick()
assert s['applied'] == removal and s['drift'] == 0 and not UNIT.exists()
assert retained.exists() and (BASE / 'textfile/pve-hostcfg.prom').exists()
assert run(['systemctl', 'is-active', 'prometheus-node-exporter.service'], False).returncode != 0
assert run(['dpkg-query', '-W', '-f=${Status}', 'prometheus-node-exporter'], False).returncode != 0
node['node_exporter'] = 'present'
rebuilt = commit('rebuild exporter')
assert tick()['applied'] == rebuilt
active()
tick('pause')
node['collectors'] = ['cpu']
pending = commit('pending while paused')
assert tick()['applied'] == rebuilt
run(['systemctl', 'enable', '--now', 'pve-hostcfg.timer'])
run(['systemctl', 'show', 'pve-hostcfg.timer', '-p', 'NextElapseUSecRealtime', '-p', 'LastTriggerUSec'])
evidence = dict(boot_id=run(['cat', '/proc/sys/kernel/random/boot_id']).stdout.strip(),
                applied_before_reboot=rebuilt, pending=pending,
                pins=run(['dpkg-query', '-W', 'git', 'ansible-core', 'prometheus-node-exporter']).stdout,
                listener=run(['ss', '-H', '-lnt', 'sport', '=', ':9100']).stdout,
                blocked_default_listener_observations=bad_listeners,
                initial_partial_sha=first, invalid_sha=invalid, removal_sha=removal,
                final_status=status())
EVIDENCE.write_text(json.dumps(evidence, indent=2) + '\n')
print('PASS: real package/systemd activation, unchanged engine/no-op, invalid preservation/retry, offline Git, Git revert, drift without repair, stop/removal/rebuild; ready for real reboot, paused')
