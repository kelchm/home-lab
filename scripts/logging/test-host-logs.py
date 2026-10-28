#!/usr/bin/env python3
"""Run a host's production Fluent Bit config against this machine's journal and check what VictoriaLogs stores.

Writes a syslog line at two levels, a container's stdout and stderr through Docker's journald
driver, a kernel message, and native journal entries that repeat MESSAGE or hold a NUL byte, then
sends the journal through proxmox/monitoring/logs with only the endpoint swapped: a local
VictoriaLogs, reached with the query arguments pve-sbx-1's ingest route adds, and a client header
that tries to rename the host. Fluent Bit runs under the memory limit compose.yaml gives it. Also
checks that every host's log route stamps the identity its metrics route does and pins the tenant
headers. Needs Docker on a machine whose Docker daemon logs to systemd-journald, such as a GitHub
runner or Colima's VM; the images are pinned.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / 'proxmox/monitoring/logs'
VMUSERS = ROOT / 'kubernetes/apps/observability/metrics-ingest/app/vmusers.yaml'
ALPINE_IMAGE = 'alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f'
LOG_PATH = '/insert/opentelemetry/v1/logs'
# journald's native protocol: KEY=value lines, or KEY, a little-endian length and raw bytes.
NATIVE = r'''
import socket, struct, sys
run = sys.argv[1]
def entry(*fields):
    out = b''
    for key, value in fields:
        value = value.encode()
        out += key + (b'\n' + struct.pack('<Q', len(value)) + value if b'\0' in value else b'=' + value) + b'\n'
    socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM).sendto(out, '/run/systemd/journal/socket')
entry((b'SYSLOG_IDENTIFIER', 'host-logs-test'), (b'PRIORITY', '6'), (b'MESSAGE', run + ' first'), (b'MESSAGE', run + ' second'))
entry((b'SYSLOG_IDENTIFIER', 'host-logs-test'), (b'PRIORITY', '6'), (b'MESSAGE', run + ' before\0after'))
'''


def pinned(path, pattern):
    return re.search(pattern, path.read_text()).group(1)


def log_routes():
    """Each host's log query arguments, checked against its metrics identity."""
    users = json.loads(subprocess.run(['yq', '-o=json', '-I=0', 'ea', '[.]', str(VMUSERS)],
                                      check=True, capture_output=True, text=True).stdout)
    routes, failures = {}, []
    for user in users:
        name = user['spec']['name']
        refs = user['spec']['targetRefs']
        labels = dict(v.split('=', 1) for r in refs for q in r.get('query_args', []) if q['name'] == 'extra_label' for v in q['values'])
        logs = [r for r in refs if LOG_PATH in r.get('paths', [])]
        if not logs:
            continue
        args = {q['name']: q['values'] for q in logs[0]['query_args']}
        want = {'extra_fields': [f"host.name={labels['instance']},service.namespace={labels['platform']}"],
                '_stream_fields': ['host.name,service.name']}
        if args != want:
            failures.append(f'{name}: log route query args {args} should be {want}')
        if logs[0].get('headers') != ['AccountID: 0', 'ProjectID: 0']:
            failures.append(f"{name}: log route should pin the tenant headers to 0, has {logs[0].get('headers')}")
        routes[name] = args
    return routes, failures


def test_config(query):
    config = (LOGS / 'fluent-bit.yaml').read_text()
    for old, new in (('host: metrics-ingest.home.kelch.io', 'host: vl'), ('port: 443', 'port: 9428'),
                     ('tls: on', 'tls: off'), ('tls.verify: on', 'tls.verify: off'),
                     (f'logs_uri: {LOG_PATH}', f'logs_uri: {LOG_PATH}?{query}'),
                     ('        - Authorization Bearer ${INGEST_TOKEN}\n',
                      '        - Authorization Bearer ${INGEST_TOKEN}\n        - VL-Extra-Fields host.name=spoofed,service.namespace=spoofed\n')):
        assert config.count(old) == 1, old
        config = config.replace(old, new)
    return config


def query(port, logsql):
    data = urllib.parse.urlencode({'query': logsql}).encode()
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/select/logsql/query', data, timeout=10) as r:
        return [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, capture_output=True, text=True, **kwargs)


def write_fixtures(run, python_image):
    docker('run', '--rm', '-v', '/dev/log:/dev/log', ALPINE_IMAGE, 'sh', '-c',
           f'logger -t host-logs-test -p daemon.warning "{run} syslog warning"; logger -t host-logs-test -p user.err "{run} syslog error"')
    docker('run', '--rm', '--log-driver', 'journald', '--name', f'host-logs-{run}', ALPINE_IMAGE, 'sh', '-c',
           f'echo "{run} container stdout"; echo "{run} container stderr" >&2')
    docker('run', '--rm', '--privileged', ALPINE_IMAGE, 'sh', '-c', f'echo "<4>host-logs-test: {run} kernel warning" > /dev/kmsg')
    docker('run', '--rm', '-v', '/run/systemd/journal/socket:/run/systemd/journal/socket', python_image, 'python', '-c', NATIVE, run)


def expectations(run):
    stream = lambda service: f'{{host.name="pve-sbx-1",service.name="{service}"}}'
    identity = {'host.name': 'pve-sbx-1', 'service.namespace': 'pve'}
    return {
        f'{run} syslog warning': {**identity, '_stream': stream('host-logs-test'), 'service.name': 'host-logs-test',
                                  'severity_number': '13', 'severity_text': 'warning', 'syslog.facility': 'daemon'},
        f'{run} syslog error': {**identity, 'service.name': 'host-logs-test', 'severity_number': '17',
                                'severity_text': 'err', 'syslog.facility': 'user'},
        f'{run} container stdout': {**identity, '_stream': stream(f'host-logs-{run}'), 'service.name': f'host-logs-{run}',
                                    'container.name': f'host-logs-{run}', 'container.image.name': ALPINE_IMAGE,
                                    'log.iostream': 'stdout', 'severity_number': '0'},
        f'{run} container stderr': {**identity, 'service.name': f'host-logs-{run}', 'log.iostream': 'stderr',
                                    'severity_number': '0'},
        # journald takes the kernel line's `host-logs-test:` prefix as its identifier.
        f'{run} kernel warning': {**identity, '_stream': stream('kernel'), 'service.name': 'kernel',
                                                  'severity_number': '13', 'severity_text': 'warning'},
        f'{run} first\n{run} second': {**identity, 'service.name': 'host-logs-test', 'severity_number': '9'},
        f'{run} before\\0after': {**identity, 'service.name': 'host-logs-test', 'severity_number': '9'},
    }


def main():
    routes, failures = log_routes()
    if 'pve-sbx-1' not in routes:
        failures.append('pve-sbx-1 has no log route')
    if failures:
        sys.exit('\n'.join(failures))
    fb_image = pinned(ROOT / 'proxmox/monitoring/compose.yaml', r'image: (\S*fluent/fluent-bit:\S+)')
    vl_image = pinned(ROOT / 'scripts/logging/test-logsql-rules.py', r"VL_IMAGE = '([^']+)'")
    python_image = pinned(ROOT / 'scripts/logging/test-fluent-bit.py', r"PYTHON_IMAGE = '([^']+)'")
    memory = pinned(ROOT / 'proxmox/monitoring/compose.yaml', r'fluent-bit\.yaml"\]\n(?:.*\n)*?    mem_limit: (\S+)')
    run = uuid.uuid4().hex[:8]
    net = f'hostlogs-{run}'
    base = Path(os.environ.get('FLUENT_BIT_TEST_TMP', Path.home() / '.cache'))
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        work = Path(tmp)
        (work / 'token').mkdir()
        (work / 'token/token').write_text('test-token\n')
        (work / 'fluent-bit.yaml').write_text(test_config(urllib.parse.urlencode(routes['pve-sbx-1'], doseq=True)))
        docker('network', 'create', net)
        try:
            docker('run', '-d', '--name', f'{net}-vl', '--network', net, '--network-alias', 'vl', '-p', '127.0.0.1::9428', vl_image)
            port = docker('port', f'{net}-vl', '9428/tcp').stdout.split(':')[-1].strip()
            docker('run', '-d', '--name', f'{net}-fb', '--network', net, '--memory', memory, '--user', '0:0', '--read-only', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges:true',
                   '-v', '/var/log/journal:/var/log/journal:ro', '-v', '/run/log/journal:/run/log/journal:ro',
                   '-v', '/etc/machine-id:/etc/machine-id:ro', '-v', f'{work}/token:/etc/monitoring:ro',
                   '-v', f'{work}/fluent-bit.yaml:/fluent-bit/etc/fluent-bit.yaml:ro',
                   '-v', f'{LOGS}/host.lua:/fluent-bit/etc/host.lua:ro', '-v', f'{net}-state:/state',
                   fb_image, '-c', '/fluent-bit/etc/fluent-bit.yaml')
            # The input starts at the end of the journal, so wait until a line written now arrives.
            deadline = time.time() + 90
            while time.time() < deadline:
                docker('run', '--rm', '-v', '/dev/log:/dev/log', ALPINE_IMAGE, 'logger', '-t', 'host-logs-test', f'{run} ready')
                time.sleep(3)
                try:
                    if query(port, f'"{run} ready"'):
                        break
                except OSError:
                    pass
            else:
                sys.exit('Fluent Bit delivered nothing:\n' + docker('logs', f'{net}-fb').stderr[-3000:])
            write_fixtures(run, python_image)
            want = expectations(run)
            rows = []
            deadline = time.time() + 60
            while time.time() < deadline:
                rows = query(port, f'"{run}" -"{run} ready"')
                if want.keys() <= {r['_msg'] for r in rows}:
                    break
                time.sleep(2)
            for row in query(port, f'"{run}"'):
                for key in ('pipeline.normalize_error', 'pipeline.severity_unmapped'):
                    if key in row:
                        failures.append(f'{row["_msg"]}: {key}={row[key]}')
                if row.get('host.name') != 'pve-sbx-1' or row.get('service.namespace') != 'pve':
                    failures.append(f'{row["_msg"]}: identity {row.get("host.name")}/{row.get("service.namespace")}')
            by_msg = {r['_msg']: r for r in rows}
            for msg, fields in want.items():
                row = by_msg.get(msg)
                if row is None:
                    failures.append(f'missing: {msg}')
                    continue
                for key, value in fields.items():
                    if row.get(key) != value:
                        failures.append(f'{msg}: {key} should be {value!r}, got {row.get(key)!r}')
                if not msg.startswith(f'{run} container'):
                    for key in ('container.name', 'log.iostream'):
                        if key in row:
                            failures.append(f'{msg}: {key} should be absent')
            if failures:
                print(docker('logs', f'{net}-fb').stderr[-3000:], file=sys.stderr)
                print(json.dumps(rows, indent=1), file=sys.stderr)
                sys.exit('\n'.join(failures))
        finally:
            subprocess.run(['docker', 'rm', '-f', f'{net}-fb', f'{net}-vl'], capture_output=True)
            subprocess.run(['docker', 'volume', 'rm', f'{net}-state'], capture_output=True)
            subprocess.run(['docker', 'network', 'rm', net], capture_output=True)
    print(f'Host logs: {len(routes)} log routes and {len(want)} journal records passed.')


if __name__ == '__main__':
    main()
