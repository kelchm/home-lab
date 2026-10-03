#!/usr/bin/python3
"""Guarded destructive push qualification on VM 990 only; leaves channel paused.
Requires explicit bootstrap of this engine first. All auth is fixture-only.
"""
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

assert os.geteuid() == 0
assert subprocess.check_output(['hostname', '-s'], text=True).strip() == 'monitoring-eval-990'
assert not Path('/etc/pve').exists()
ROOT = Path('/tmp/pve-push-eval')
CTL = '/usr/local/lib/pve-hostcfg/controller.py'
ENGINE = Path('/usr/local/lib/pve-hostcfg')
BASE = Path('/var/lib/pve-hostcfg')
UNIT = 'pve-host-vmagent.service'
TOKEN = Path('/etc/pve-host-vmagent/token')
SCRAPE = Path('/etc/pve-hostcfg/vmagent-scrape.yml')
QUEUE = Path('/var/lib/pve-host-vmagent')
SAMPLE = BASE / 'textfile/pve-push-eval.prom'


def run(args, check=True):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout, flush=True)
    if check:
        assert result.returncode == 0, (args, result.returncode)
    return result


def get(url):
    with urllib.request.urlopen(url, timeout=4) as response:
        return response.read().decode()


def waitfor(test, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if test():
                return
        except OSError:
            pass  # systemd active precedes the HTTP listener becoming ready.
        time.sleep(1)
    raise AssertionError('condition timeout')


if sys.argv[1:] == ['receiver']:
    counts = {'accepted': 0, 'unauthorized': 0, 'unavailable': 0}
    lock = threading.Lock()

    class Receiver(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            state = json.loads((ROOT / 'receiver-state.json').read_text())
            if not state['accept']:
                code, kind = 503, 'unavailable'
            elif self.headers.get('Authorization') != 'Bearer ' + state['token']:
                code, kind = 401, 'unauthorized'
            else:
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'content-length', 'authorization')}
                request = urllib.request.Request('http://127.0.0.1:18428/api/v1/write', data=body, headers=headers)
                with urllib.request.urlopen(request, timeout=5) as response:
                    code = response.status
                kind = 'accepted'
            with lock:
                counts[kind] += 1
                (ROOT / 'receiver-counts.json').write_text(json.dumps(counts))
            self.send_response(code)
            self.end_headers()

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 443), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(ROOT / 'tls.crt', ROOT / 'tls.key')
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
    raise SystemExit(0)

assert not sys.argv[1:]
assert (BASE / 'paused').exists()
assert not TOKEN.exists(), 'fresh fixture-only credential path required'
assert not SAMPLE.exists(), 'fixture must not overwrite an existing textfile'
ROOT.mkdir(mode=0o755)
run([CTL, 'pause'])
run(['systemctl', 'stop', 'pve-hostcfg.timer', 'pve-hostcfg.service'])
config_path = Path('/etc/pve-hostcfg/config.json')
original_config = config_path.read_bytes()
hosts = Path('/etc/hosts')
original_hosts = hosts.read_bytes()
trust = Path('/usr/local/share/ca-certificates/pve-push-eval.crt')
trust_pem = Path('/etc/ssl/certs/pve-push-eval.pem')
assert not os.path.lexists(trust) and not os.path.lexists(trust_pem)
original_template = (ENGINE / 'vmagent-scrape.yml.j2').read_bytes()
exporter_unit = Path('/etc/systemd/system/prometheus-node-exporter.service')
assert exporter_unit.is_file() and not exporter_unit.is_symlink(), 'qualification expects an owned exporter fixture'
original_exporter_unit = exporter_unit.read_bytes()
original_exporter_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'prometheus-node-exporter.service']).returncode == 0
original_exporter_enabled = subprocess.run(['systemctl', 'is-enabled', '--quiet', 'prometheus-node-exporter.service']).returncode == 0
EVIDENCE = {}
fixtures = ('pve-push-eval-store.service', 'pve-push-eval-receiver.service')
assert all(not Path('/etc/systemd/system', unit).exists() for unit in fixtures), 'fixture unit names must be free'


def atomic_json(path, value):
    temp = path.with_suffix('.new')
    temp.write_text(json.dumps(value))
    temp.replace(path)


def state(accept=True, token='a' * 64):
    atomic_json(ROOT / 'receiver-state.json', dict(accept=accept, token=token))


def rotate(token):
    temp = TOKEN.with_suffix('.new')
    temp.write_text(token + '\n')
    temp.chmod(0o640)
    os.chown(temp, 0, 65534)
    temp.replace(TOKEN)


def status():
    return json.loads((BASE / 'status.json').read_text())


def tick(command='sync', expected=0):
    result = run([CTL, command], False)
    assert result.returncode == expected, (command, result.returncode, expected)
    return status()


def prop(name):
    return subprocess.check_output(['systemctl', 'show', UNIT, '-p', name, '--value'], text=True).strip()


def active():
    def ready():
        try:
            return prop('ActiveState') == 'active' and get('http://127.0.0.1:8429/health').strip() == 'OK'
        except OSError:
            return False
    waitfor(ready, 20)


def pending():
    metrics = get('http://127.0.0.1:8429/metrics')
    return sum(float(line.split()[-1]) for line in metrics.splitlines() if line.startswith('vm_persistentqueue_bytes_pending{'))


def counts():
    return json.loads((ROOT / 'receiver-counts.json').read_text()) if (ROOT / 'receiver-counts.json').exists() else dict(accepted=0, unauthorized=0, unavailable=0)


def historical(phase):
    url = 'http://127.0.0.1:18428/api/v1/export?match%5B%5D=pve_push_eval_sample'
    return [json.loads(line) for line in get(url).splitlines() if json.loads(line)['metric'].get('phase') == phase]


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
node = dict(enabled=True, node_exporter='present', collectors=['systemd'], vmagent='absent')


def commit(message, write=True):
    if write:
        (payload / 'nodes.json').write_text(json.dumps({'pve-sbx-1': node}))
    run(['git', '-C', str(work), 'add', '.'])
    run(['git', '-C', str(work), 'commit', '-m', message])
    run(['git', '-C', str(work), 'push', 'origin', 'main'])
    return subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'HEAD'], text=True).strip()


try:
    # Download an official checksum-verified native store for loopback qualification only.
    url = 'https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v1.152.0/'
    name = 'victoria-metrics-linux-amd64-v1.152.0'
    with urllib.request.urlopen(url + name + '_checksums.txt', timeout=20) as response:
        manifest = dict(line.split()[::-1] for line in response.read().decode().splitlines())
    assert manifest[name + '.tar.gz'] == '1be2fc4bbdbfa56ba0480e6b0aff736f0c17e5e5b8b888587b69c3a21b42114a'
    archive = ROOT / 'store.tar.gz'
    with urllib.request.urlopen(url + name + '.tar.gz', timeout=30) as response, archive.open('wb') as out:
        shutil.copyfileobj(response, out)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest[name + '.tar.gz']
    with tarfile.open(archive) as tar, (ROOT / 'victoria-metrics-prod').open('wb') as out:
        shutil.copyfileobj(tar.extractfile('victoria-metrics-prod'), out)
    assert hashlib.sha256((ROOT / 'victoria-metrics-prod').read_bytes()).hexdigest() == manifest['victoria-metrics-prod']
    (ROOT / 'victoria-metrics-prod').chmod(0o755)
    run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-keyout', str(ROOT / 'tls.key'), '-out', str(ROOT / 'tls.crt'), '-subj', '/CN=metrics-ingest.home.kelch.io', '-addext', 'subjectAltName=DNS:metrics-ingest.home.kelch.io'])
    hosts.write_bytes(original_hosts + b'\n127.0.0.1 metrics-ingest.home.kelch.io # pve-push-eval\n')
    shutil.copyfile(ROOT / 'tls.crt', trust)
    run(['update-ca-certificates'])
    TOKEN.parent.mkdir(mode=0o750)
    os.chown(TOKEN.parent, 0, 65534)
    rotate('a' * 64)
    state()
    units = [f'[Service]\nExecStart={ROOT}/victoria-metrics-prod -httpListenAddr=127.0.0.1:18428 -storageDataPath={ROOT}/store -search.latencyOffset=0s -loggerLevel=WARN\n', f'[Service]\nExecStart=/usr/bin/python3 {Path(__file__).resolve()} receiver\n']
    for unit, content in zip(fixtures, units):
        Path('/etc/systemd/system', unit).write_text(content)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'start', *fixtures])
    waitfor(lambda: subprocess.run(['curl', '-fsS', 'http://127.0.0.1:18428/health'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
    config = json.loads(original_config)
    config['repository'] = str(remote)
    atomic_json(config_path, config)
    baseline = commit('fixture absent baseline')
    tick('resume')
    assert tick()['applied'] == baseline
    node['vmagent'] = 'present'
    first = commit('fixture explicit present')
    s = tick()
    assert s['applied'] == first and s['drift'] == 0
    active()
    pid = prop('MainPID')
    ids = Path('/proc', pid, 'status').read_text()
    assert 'Uid:\t65534\t65534\t65534\t65534' in ids and 'Gid:\t65534\t65534\t65534\t65534' in ids
    command = Path('/proc', pid, 'cmdline').read_bytes().replace(b'\0', b' ').decode()
    assert '-remoteWrite.maxDiskUsagePerURL=1GiB' in command and '-remoteWrite.bearerTokenFile=/etc/pve-host-vmagent/token' in command
    assert 'https://metrics-ingest.home.kelch.io/api/v1/write' in command
    listener = run(['ss', '-H', '-lnt', 'sport', '=', ':8429']).stdout
    assert listener and all('127.0.0.1:8429' in line for line in listener.splitlines())
    waitfor(lambda: counts()['accepted'] > 0)
    EVIDENCE['activation'] = dict(uid=65534, gid=65534, command=command, listener=listener, memory_max=prop('MemoryMax'), cpu_quota=prop('CPUQuotaPerSecUSec'), protect_system=prop('ProtectSystem'), stop_timeout=prop('TimeoutStopUSec'))
    # Check mode sees inactive service drift without starting it.
    run(['systemctl', 'stop', UNIT])
    assert tick('check')['drift'] == 1 and prop('ActiveState') == 'inactive'
    run(['systemctl', 'start', UNIT])
    active()
    EVIDENCE['check_does_not_start'] = True
    # No-op and fetched code never replenish/modify the native binary or restart.
    pid = prop('MainPID')
    binary_sha = hashlib.sha256((ENGINE / 'vmagent-prod').read_bytes()).hexdigest()
    assert tick()['applied'] == first and prop('MainPID') == pid
    (work / 'proxmox/host-config/engine').mkdir()
    (work / 'proxmox/host-config/engine/vmagent-prod').write_text('not delivered')
    unrelated = commit('unrelated fetched binary', False)
    assert tick()['applied'] == first and prop('MainPID') == pid
    assert hashlib.sha256((ENGINE / 'vmagent-prod').read_bytes()).hexdigest() == binary_sha
    EVIDENCE['no_op_and_no_code_delivery'] = True
    # Native config failure and missing credential preserve the prior running service/files/marker.
    good_config = SCRAPE.read_bytes()
    node['collectors'] = ['systemd', 'cpu']
    change = commit('same-SHA recovery after native preflight failures')
    (ENGINE / 'vmagent-scrape.yml.j2').write_text('global:\n  unsupported_native_field: true\n')
    assert tick(expected=1)['applied'] == first and prop('MainPID') == pid and SCRAPE.read_bytes() == good_config
    (ENGINE / 'vmagent-scrape.yml.j2').write_bytes(original_template)
    rotate('not-a-valid-token')
    assert tick(expected=1)['applied'] == first and prop('MainPID') == pid and SCRAPE.read_bytes() == good_config
    rotate('a' * 64)
    saved_token = TOKEN.with_suffix('.saved')
    TOKEN.rename(saved_token)
    try:
        assert tick(expected=1)['applied'] == first and prop('MainPID') == pid and SCRAPE.read_bytes() == good_config
    finally:
        saved_token.rename(TOKEN)
    assert tick()['applied'] == change
    active()
    assert prop('MainPID') != pid, 'prior dirty apply must reactivate vmagent even with unchanged artifacts'
    EVIDENCE['bad_config_missing_token_preservation_same_sha'] = True
    # Drift-deleted unit still has a live process after reload; check preserves it, explicit absent stops it.
    drift_pid = prop('MainPID')
    Path('/etc/systemd/system', UNIT).unlink()
    run(['systemctl', 'daemon-reload'])
    assert prop('ActiveState') == 'active'
    assert tick('check')['drift'] == 1 and prop('MainPID') == drift_pid and prop('ActiveState') == 'active'
    # Real payload change then Git revert; exporter stays independent.
    node['vmagent'] = 'absent'
    removal = commit('remove fixture collector')
    assert tick()['applied'] == removal and not SCRAPE.exists() and not Path('/etc/systemd/system', UNIT).exists()
    assert QUEUE.exists() and TOKEN.exists() and prop('ActiveState') == 'inactive'
    assert not Path('/etc/systemd/system/multi-user.target.wants', UNIT).exists()
    assert not Path('/etc/systemd/system/multi-user.target.wants', UNIT).is_symlink()
    EVIDENCE['drift_deleted_unit_check_preserves_absent_stops'] = True
    assert run(['systemctl', 'is-active', 'prometheus-node-exporter.service']).stdout.strip() == 'active'
    run(['git', '-C', str(work), 'revert', '--no-edit', removal])
    run(['git', '-C', str(work), 'push', 'origin', 'main'])
    reverted = subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'HEAD'], text=True).strip()
    node['vmagent'] = 'present'
    assert tick()['applied'] == reverted
    active()
    EVIDENCE['explicit_remove_git_revert_rebuild'] = True
    # The actual production unit buffers through outage, clean systemd restart and historical replay.
    state(False)
    sample_value = int(time.time())  # Exact integer avoids native store decimal rounding in this assertion.
    SAMPLE.write_text(f'pve_push_eval_sample{{phase="outage"}} {sample_value}\n')
    begin = time.time()
    waitfor(lambda: counts()['unavailable'] > 0 and pending() > 0)
    time.sleep(35)
    pending_before = pending()
    run(['systemctl', 'stop', UNIT])
    stopped_at = time.time()
    files_bytes = sum(p.stat().st_size for p in QUEUE.rglob('*') if p.is_file())
    assert files_bytes > 0
    SAMPLE.write_text(f'pve_push_eval_sample{{phase="after"}} {time.time()}\n')
    state(True)
    run(['systemctl', 'start', UNIT])
    active()
    waitfor(lambda: pending() == 0)
    waitfor(lambda: bool(historical('outage')))
    old = historical('outage')
    timestamps = [x / 1000 for item in old for x in item['timestamps']]
    assert len(timestamps) >= 2 and all(begin - 1 <= x <= stopped_at for x in timestamps)
    assert all(value == sample_value for item in old for value in item['values']), 'replayed samples retain original values'
    queue_after_drain = {str(p.relative_to(QUEUE)): p.stat().st_size for p in QUEUE.rglob('*') if p.is_file()}
    for item in old:
        labels = item['metric']
        assert all(labels.get(k) == v for k, v in dict(host='pve-sbx-1', instance='pve-sbx-1', platform='proxmox', collector='pve-host-vmagent').items()) and 'cluster' not in labels
    EVIDENCE['outage_restart_replay'] = dict(pending_bytes_before_stop=pending_before, queue_files_bytes_after_stop=files_bytes, original_timestamps=timestamps, original_sample_value=sample_value, queue_files_after_drain=queue_after_drain, queued_data_bytes_after_drain=pending(), configured_queued_data_cap_bytes=1073741824, interval_seconds=15, historical_labels=old[0]['metric'])
    # Receiver rotates ahead: 401 causes queue/retry; atomic file rotation recovers without restart.
    state(True, 'b' * 64)
    waitfor(lambda: counts()['unauthorized'] > 0 and pending() > 0)
    queued = pending()
    pid = prop('MainPID')
    accepted_before = counts()['accepted']
    inode_before = TOKEN.stat().st_ino
    rotate('b' * 64)
    inode_after = TOKEN.stat().st_ino
    assert inode_after != inode_before, 'rotation must atomically replace the token inode'
    waitfor(lambda: counts()['accepted'] > accepted_before and pending() == 0)
    assert prop('MainPID') == pid
    EVIDENCE['atomic_token_rotation_401_retry'] = dict(queued_bytes=queued, same_pid=True, accepted_before_rotation=accepted_before, inode_before=inode_before, inode_after=inode_after, receiver_counts=counts())
    # Unit/config drift is reported without repairing bytes or restarting the collector.
    manual = good_config + b'# operator drift\n'
    SCRAPE.write_bytes(manual)
    assert tick('check')['drift'] == 1 and SCRAPE.read_bytes() == manual and prop('MainPID') == pid
    assert tick()['drift'] == 1 and SCRAPE.read_bytes() == manual
    node['vmagent'] = 'absent'
    final = commit('final fixture absent cleanup')
    assert tick()['applied'] == final and not SCRAPE.exists() and not Path('/etc/systemd/system', UNIT).exists()
    assert TOKEN.exists() and QUEUE.exists()
    EVIDENCE['drift_no_repair_final_removal'] = True
    EVIDENCE['final_status'] = tick('pause')
    EVIDENCE['result'] = 'PASS'
finally:
    (ENGINE / 'vmagent-scrape.yml.j2').write_bytes(original_template)
    run([CTL, 'pause'], False)
    run(['systemctl', 'stop', UNIT, *fixtures], False)
    run(['systemctl', 'disable', UNIT], False)
    Path('/etc/systemd/system', UNIT).unlink(missing_ok=True)
    Path('/etc/systemd/system/multi-user.target.wants', UNIT).unlink(missing_ok=True)
    SCRAPE.unlink(missing_ok=True)
    SAMPLE.unlink(missing_ok=True)
    for unit in fixtures:
        Path('/etc/systemd/system', unit).unlink(missing_ok=True)
    hosts.write_bytes(original_hosts)
    trust.unlink(missing_ok=True)
    # Ordinary update-ca-certificates can retain dangling generated links after
    # the source disappears. Remove only links to this named fixture anchor.
    for link in trust_pem.parent.iterdir():
        if link.is_symlink() and os.readlink(link) == trust_pem.name:
            link.unlink()
    if trust_pem.is_symlink() and os.readlink(trust_pem) == str(trust):
        trust_pem.unlink()
    run(['update-ca-certificates'], False)
    config_path.write_bytes(original_config)
    exporter_unit.unlink(missing_ok=True)
    exporter_unit.write_bytes(original_exporter_unit)
    exporter_unit.chmod(0o644)
    os.chown(exporter_unit, 0, 0)
    run(['systemctl', 'daemon-reload'], False)
    run(['systemctl', 'enable' if original_exporter_enabled else 'disable', 'prometheus-node-exporter.service'], False)
    run(['systemctl', 'restart' if original_exporter_active else 'stop', 'prometheus-node-exporter.service'], False)
    run(['systemctl', 'enable', '--now', 'pve-hostcfg.timer'], False)
    EVIDENCE['cleanup'] = dict(hosts_restored=hosts.read_bytes() == original_hosts, repository_config_restored=config_path.read_bytes() == original_config, temporary_trust_removed=not os.path.lexists(trust) and not os.path.lexists(trust_pem), fixture_units_removed=all(not Path('/etc/systemd/system', u).exists() for u in fixtures), fixture_services_inactive=all(subprocess.run(['systemctl', 'is-active', '--quiet', u]).returncode != 0 for u in fixtures), vmagent_owned_files_removed=not Path('/etc/systemd/system', UNIT).exists() and not Path('/etc/systemd/system/multi-user.target.wants', UNIT).is_symlink() and not SCRAPE.exists(), vmagent_inactive=subprocess.run(['systemctl', 'is-active', '--quiet', UNIT]).returncode != 0, exporter_unit_restored=exporter_unit.read_bytes() == original_exporter_unit, exporter_active_restored=(subprocess.run(['systemctl', 'is-active', '--quiet', 'prometheus-node-exporter.service']).returncode == 0) == original_exporter_active, exporter_enabled_restored=(subprocess.run(['systemctl', 'is-enabled', '--quiet', 'prometheus-node-exporter.service']).returncode == 0) == original_exporter_enabled, timer_enabled=subprocess.run(['systemctl', 'is-enabled', '--quiet', 'pve-hostcfg.timer']).returncode == 0, timer_active=subprocess.run(['systemctl', 'is-active', '--quiet', 'pve-hostcfg.timer']).returncode == 0, paused=(BASE / 'paused').exists())
    (ROOT / 'evidence.json').write_text(json.dumps(EVIDENCE, indent=2) + '\n')
assert all(EVIDENCE['cleanup'].values()), EVIDENCE['cleanup']
print('PASS: native systemd UID/flags/TLS, check without start, immutable binary/no-op, config/token preservation, same-SHA retry, Git revert, removal/rebuild, outage queue/restart/historical replay, token rotation/401 retry, drift and cleanup')
