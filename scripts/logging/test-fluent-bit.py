#!/usr/bin/env python3
"""Run the production Fluent Bit config and Lua mapping against fixture logs.

The test changes only what cannot run outside the cluster: Kubernetes metadata
comes from fixtures instead of the API, UniFi syslog and Talos json_lines
arrive from a local sender, output goes to a local OTLP capture instead of VictoriaLogs, and state
lives in a temporary directory. Requires Docker; the Fluent Bit image is the
one pinned in the HelmRelease.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
K8S_STREAM_FIELDS = 'service.name,k8s.namespace.name,k8s.pod.name,k8s.container.name,k8s.node.name'
UNIFI_STREAM_FIELDS = 'service.name,cef.device_product,cef.device_event_class_id'
TALOS_STREAM_FIELDS = 'service.name,k8s.node.name'
# Talos sends one JSON object per line over a single TCP connection.
SEND = """
import json, socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for host, datagram in json.load(open(sys.argv[1])):
    s.sendto(datagram.encode(), (host, 5140))
    time.sleep(0.05)
t = socket.create_connection(('fb', 5170))
for record in json.load(open(sys.argv[2])):
    t.sendall(json.dumps(record).encode() + b'\\n')
    time.sleep(0.05)
# Exiting with unsent data would discard it with the container's network.
t.shutdown(socket.SHUT_WR)
t.recv(1)
"""
APP = ROOT / 'kubernetes/apps/observability/fluent-bit/app'
FIXTURES = json.loads((ROOT / 'scripts/logging/fluent-bit-fixtures.json').read_text())
PYTHON_IMAGE = 'python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a'


def fluent_bit_image():
    values = (APP / 'helmrelease.yaml').read_text()
    repo = re.search(r'repository:\s*(\S+)', values).group(1)
    tag = re.search(r'tag:\s*(\S+)', values).group(1)
    return f'{repo}:{tag}'


def lua_literal(v):
    if isinstance(v, dict):
        return '{' + ','.join(f'[{lua_literal(k)}]={lua_literal(x)}' for k, x in v.items()) + '}'
    if isinstance(v, list):
        return '{' + ','.join(lua_literal(x) for x in v) + '}'
    return json.dumps(v)


def test_config(production):
    """Swap the Kubernetes filter, output target and state paths only."""
    sections = re.split(r'\n(?=\[)', production)
    out = []
    for s in sections:
        if s.startswith('[FILTER]') and re.search(r'^\s*Name\s+kubernetes\s*$', s, re.M):
            s = '[FILTER]\n    Name lua\n    Match kube.*\n    Script /test/inject.lua\n    Call inject\n'
        s = re.sub(r'(storage\.path\s+)\S+', r'\1/state/storage', s)
        s = re.sub(r'(^\s*DB\s+)\S+', r'\1/state/tail.db', s, flags=re.M)
        s = re.sub(r'(^\s*Host\s+)\S+', r'\1capture', s, flags=re.M)
        s = re.sub(r'(^\s*Port\s+)9428', r'\g<1>4318', s, flags=re.M)
        out.append(s)
    return '\n'.join(out)


def write_inputs(work):
    logs = work / 'logs'
    logs.mkdir()
    metadata, ts = {}, 1792353600
    for i, case in enumerate(FIXTURES['cases']):
        pod = dict(FIXTURES['pods'][case['pod']])
        pod_id = str(uuid.UUID(int=i + 1))
        pod.setdefault('host', 'k8s-prod-1')
        pod['pod_id'] = pod_id
        name = f"{pod['pod_name']}_{pod['namespace_name']}_{pod['container_name']}-{i:064x}.log"
        stamp = f'2026-10-18T19:00:{i:02d}.000000000Z'
        stream = case.get('stream', 'stdout')
        if 'partial' in case:
            parts = case['partial']
            lines = [f'{stamp} {stream} P {p}' for p in parts[:-1]] + [f'{stamp} {stream} F {parts[-1]}']
        else:
            lines = [f"{stamp} {stream} F {case['line']}{'a' * case.get('pad', 0)}"]
        (logs / name).write_text('\n'.join(lines) + '\n')
        metadata['kube.var.log.containers.' + name] = pod
        case['_uid'] = pod_id
    # A second network gives the sender a second source address.
    (work / 'syslog.json').write_text(json.dumps([[case.get('via', 'fb'), case['datagram']] for case in FIXTURES['syslog']]))
    (work / 'talos.json').write_text(json.dumps([case['record'] for case in FIXTURES['talos']]))
    (work / 'inject.lua').write_text(
        'local pods=' + lua_literal(metadata) + '\n'
        'function inject(tag, ts, record)\n'
        '  record.kubernetes = pods[tag]\n'
        '  return 1, ts, record\n'
        'end\n')


def run(work):
    net = f'fbtest-{uuid.uuid4().hex[:8]}'
    docker = ['docker']
    # Run as the invoking user so the temporary directory stays removable.
    user = ['--user', f'{os.getuid()}:{os.getgid()}']
    for n in (net, f'{net}-2'):
        subprocess.run(docker + ['network', 'create', n], check=True, capture_output=True)
    try:
        subprocess.run(docker + ['run', '-d', '--rm', *user, '--name', f'{net}-capture', '--network', net, '--network-alias', 'capture',
                                 '-v', f'{ROOT}/scripts/logging/otlp-capture.py:/capture.py:ro', '-v', f'{work}:/work',
                                 PYTHON_IMAGE, 'python', '/capture.py', '/work/wire.jsonl'], check=True, capture_output=True)
        subprocess.run(docker + ['run', '-d', '--rm', *user, '--name', f'{net}-fb', '--network', net, '--network-alias', 'fb', '-e', 'TZ=UTC', '-e', 'NODE_NAME=k8s-prod-1',
                                 '-v', f'{APP}/config:/fluent-bit/etc/conf:ro', '-v', f'{work}/test.conf:/test/fluent-bit.conf:ro',
                                 '-v', f'{work}/inject.lua:/test/inject.lua:ro', '-v', f'{work}/logs:/var/log/containers:ro',
                                 '-v', f'{work}/state:/state', fluent_bit_image(), '-c', '/test/fluent-bit.conf'],
                       check=True, capture_output=True)
        subprocess.run(docker + ['network', 'connect', '--alias', 'fb2', f'{net}-2', f'{net}-fb'], check=True, capture_output=True)
        wire = work / 'wire.jsonl'
        # Container records arriving show the syslog listener is bound too.
        deadline = time.time() + 60
        while time.time() < deadline and not (wire.exists() and wire.stat().st_size):
            time.sleep(1)
        subprocess.run(docker + ['create', *user, '--name', f'{net}-send', '--network', net, '-v', f'{work}/syslog.json:/syslog.json:ro',
                                 '-v', f'{work}/talos.json:/talos.json:ro', PYTHON_IMAGE, 'python', '-c', SEND, '/syslog.json', '/talos.json'], check=True, capture_output=True)
        subprocess.run(docker + ['network', 'connect', f'{net}-2', f'{net}-send'], check=True, capture_output=True)
        subprocess.run(docker + ['start', '-a', f'{net}-send'], check=True)
        deadline = time.time() + 90
        while time.time() < deadline:
            if wire.exists() and sum(1 for _ in wire.open()) >= expected_records():
                break
            time.sleep(1)
        time.sleep(3)
        logs = subprocess.run(docker + ['logs', f'{net}-fb'], capture_output=True, text=True)
        return [json.loads(line) for line in wire.open()] if wire.exists() else [], logs.stdout + logs.stderr
    finally:
        for name in ('fb', 'capture', 'send'):
            subprocess.run(docker + ['rm', '-f', f'{net}-{name}'], capture_output=True)
        for n in (net, f'{net}-2'):
            subprocess.run(docker + ['network', 'rm', n], capture_output=True)


def expected_records():
    return sum(1 for case in FIXTURES['cases'] + FIXTURES['talos'] if not case.get('dropped')) + len(FIXTURES['syslog'])


def check(rows):
    by_uid = {r['resource'].get('k8s.pod.uid'): r for r in rows if 'k8s.pod.uid' in r['resource']}
    # UniFi fixtures are told apart by their expected body, so each must be unique.
    by_body = {r.get('body'): r for r in rows if r['resource'].get('service.namespace') == 'unifi'}
    talos_rows = {r.get('body'): r for r in rows if r['resource'].get('service.namespace') == 'talos'}
    failures = []
    if len(rows) != expected_records():
        failures.append(f'expected {expected_records()} records, received {len(rows)}')
    for case in FIXTURES['cases'] + FIXTURES['syslog'] + FIXTURES['talos']:
        unifi, talos = 'datagram' in case, 'record' in case
        name = case['name']
        if talos:
            msg = case['record'].get('msg', '')
            row = talos_rows.get(case['expect']['body']) if 'expect' in case else next(
                (r for r in talos_rows.values() if r.get('body') and r['body'] in msg), None)
        else:
            row = by_body.get(case['expect']['body']) if unifi else by_uid.get(case['_uid'])
        if case.get('dropped'):
            if row is not None:
                failures.append(f'{name}: should be dropped, got {row.get("body")!r}')
            continue
        exp = case['expect']
        if row is None:
            failures.append(f'{name}: no record')
            continue

        def compare(label, want, got):
            if want != got:
                failures.append(f'{name}: {label} expected {want!r}, got {got!r}')

        if row.get('stream_fields') != (UNIFI_STREAM_FIELDS if unifi else TALOS_STREAM_FIELDS if talos else K8S_STREAM_FIELDS):
            failures.append(f'{name}: VL-Stream-Fields header {row.get("stream_fields")!r}')
        if row.get('ignore_fields') != 'scope.name,scope.version':
            failures.append(f'{name}: VL-Ignore-Fields header {row.get("ignore_fields")!r}')
        for key in ('severity_number', 'severity_text', 'body', 'timestamp'):
            if key in exp:
                compare(key, exp[key], row.get(key, 0 if key == 'severity_number' else None))
        if exp.get('no_severity_text'):
            compare('severity_text', None, row.get('severity_text'))
        for key, want in exp.get('resource', {}).items():
            compare(f'resource {key}', want, row['resource'].get(key))
        if exp.get('service_name_is_peer'):
            compare('resource service.name', row['attributes'].get('network.peer.address'), row['resource'].get('service.name'))
        for key, want in exp.get('attributes', {}).items():
            compare(f'attribute {key}', want, row['attributes'].get(key))
        for key in exp.get('present', []):
            if key not in row['attributes']:
                failures.append(f'{name}: attribute {key} should be present')
        for key in exp.get('absent', []):
            if key in row['attributes']:
                failures.append(f'{name}: attribute {key} should be absent, got {row["attributes"][key]!r}')
        if 'otlp' in row['attributes'] or 'ingress' in row['attributes']:
            failures.append(f'{name}: internal metadata leaked into attributes')
    return failures


def main():
    base = Path(os.environ.get('FLUENT_BIT_TEST_TMP', Path.home() / '.cache'))
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        work = Path(tmp)
        (work / 'state').mkdir()
        write_inputs(work)
        (work / 'test.conf').write_text(test_config((APP / 'config/fluent-bit.conf').read_text()))
        rows, logs = run(work)
        failures = check(rows)
        if failures:
            print(logs[-4000:], file=sys.stderr)
            print('\n'.join(failures), file=sys.stderr)
            if os.environ.get('FLUENT_BIT_TEST_DUMP'):
                print(json.dumps(rows, indent=1), file=sys.stderr)
            sys.exit(1)
    print(f'Fluent Bit mapping: {len(FIXTURES["cases"]) + len(FIXTURES["syslog"]) + len(FIXTURES["talos"])} fixtures passed.')


if __name__ == '__main__':
    main()
