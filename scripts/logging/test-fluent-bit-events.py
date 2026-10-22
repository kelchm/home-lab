#!/usr/bin/env python3
"""Run the production Kubernetes events collector against a fixture API server.

The test changes only what cannot run outside the cluster: events come from
events-api.py, which serves lists and a watch stream as the API server does,
output goes to a local OTLP capture instead of VictoriaLogs, and state lives in
a temporary directory. The real kubernetes_events input lists, watches from the
list's resource version, loses its watch and lists again, so the fixtures also
cover retention, repeated events and series, ignored watch types, an event
created while the watch was down, and the absence of duplicates after the
second list. Requires Docker; the Fluent Bit image is the one pinned in the
HelmRelease.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'kubernetes/apps/observability/fluent-bit/app'
CONFIG = APP / 'config/events'
FIXTURES = json.loads((ROOT / 'scripts/logging/fluent-bit-events-fixtures.json').read_text())
PYTHON_IMAGE = 'python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a'
STREAM_FIELDS = 'service.name,k8s.namespace.name'
RESOURCE = {'service.name': 'kubernetes-events', 'k8s.cluster.name': 'k8s-prod'}
TOKEN = 'fixture-token'
MICRO = {'eventTime', 'series.lastObservedTime'}


def fluent_bit_image():
    values = (APP / 'helmrelease-events.yaml').read_text()
    repo = re.search(r'repository:\s*(\S+)', values).group(1)
    tag = re.search(r'tag:\s*(\S+)', values).group(1)
    return f'{repo}:{tag}'


def test_config(production):
    """Point the input at the fixture API and the output at the capture."""
    conf = re.sub(r'(storage\.path\s+)\S+', r'\1/state/storage', production)
    conf = re.sub(r'(^\s*Name\s+kubernetes_events\s*$)', r'\1\n    kube_url            http://api:8080', conf, flags=re.M)
    conf = re.sub(r'(^\s*Host\s+)\S+', r'\1capture', conf, flags=re.M)
    return re.sub(r'(^\s*Port\s+)9428', r'\g<1>4318', conf, flags=re.M)


def build_plan(now):
    """Fill each fixture's times, `times` seconds before now, and the second list,
    which reflects the watch and adds events created while it was down."""
    first, later, watch = {}, {}, []
    for case in FIXTURES['events']:
        event = json.loads(json.dumps(case['event']))
        event.setdefault('kind', 'Event')
        event.setdefault('apiVersion', 'v1')
        case['_times'] = {}
        for field, age in case['times'].items():
            micro = field in MICRO
            t = (now - datetime.timedelta(seconds=age)).replace(microsecond=123456 if micro else 0)
            value = t.strftime('%Y-%m-%dT%H:%M:%S.%fZ' if micro else '%Y-%m-%dT%H:%M:%SZ')
            target, key = event, field
            if field == 'creationTimestamp':
                target = event['metadata']
            elif field.startswith('series.'):
                target, key = event['series'], field.split('.', 1)[1]
            target[key] = value
            case['_times'][field] = (int(t.timestamp()) * 10**9 + t.microsecond * 1000, value)
        uid = event['metadata'].get('uid')
        if case['phase'] == 'list':
            first[uid] = later[uid] = event
        elif case['phase'] == 'relist':
            later[uid] = event
        else:
            watch.append({'type': case['watch_type'], 'object': event})
            if case['watch_type'] == 'DELETED':
                later.pop(uid, None)
            elif case['watch_type'] in ('ADDED', 'MODIFIED') and not case.get('dropped'):
                later[uid] = event
    lists = [{'kind': 'EventList', 'apiVersion': 'v1', 'metadata': {'resourceVersion': version}, 'items': list(items.values())}
             for version, items in zip(FIXTURES['list_resource_versions'], (first, later))]
    return {'lists': lists, 'watch': watch}


def expected():
    return [case for case in FIXTURES['events'] if not case.get('dropped')]


def run(work):
    net = f'fbevents-{uuid.uuid4().hex[:8]}'
    docker = ['docker']
    user = ['--user', f'{os.getuid()}:{os.getgid()}']
    subprocess.run(docker + ['network', 'create', net], check=True, stdout=subprocess.DEVNULL)
    try:
        subprocess.run(docker + ['run', '-d', '--rm', *user, '--name', f'{net}-capture', '--network', net, '--network-alias', 'capture',
                                 '-v', f'{ROOT}/scripts/logging/otlp-capture.py:/capture.py:ro', '-v', f'{work}:/work',
                                 PYTHON_IMAGE, 'python', '/capture.py', '/work/wire.jsonl'], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(docker + ['run', '-d', '--rm', *user, '--name', f'{net}-api', '--network', net, '--network-alias', 'api',
                                 '-v', f'{ROOT}/scripts/logging/events-api.py:/api.py:ro', '-v', f'{work}:/work',
                                 PYTHON_IMAGE, 'python', '/api.py', '/work/plan.json', '/work/requests.jsonl'], check=True, stdout=subprocess.DEVNULL)
        time.sleep(2)
        # No --rm: a collector that exits keeps its log for the failure report.
        subprocess.run(docker + ['run', '-d', *user, '--name', f'{net}-fb', '--network', net,
                                 '-v', f'{CONFIG}:/fluent-bit/etc/conf:ro', '-v', f'{work}/test.conf:/test/fluent-bit.conf:ro',
                                 '-v', f'{work}/token:/var/run/secrets/kubernetes.io/serviceaccount/token:ro',
                                 '-v', f'{work}/state:/state', fluent_bit_image(), '-c', '/test/fluent-bit.conf'],
                       check=True, stdout=subprocess.DEVNULL)
        wire, requests = work / 'wire.jsonl', work / 'requests.jsonl'
        deadline = time.time() + 60
        while time.time() < deadline:
            watches = sum('watch=1' in line for line in requests.open()) if requests.exists() else 0
            if watches >= 2 and wire.exists() and sum(1 for _ in wire.open()) >= len(expected()):
                break
            time.sleep(1)
        # Anything the second list re-sent would arrive within a few flushes.
        time.sleep(5)
        logs = subprocess.run(docker + ['logs', f'{net}-fb'], capture_output=True, text=True)
        rows = [json.loads(line) for line in wire.open()] if wire.exists() else []
        seen = [json.loads(line) for line in requests.open()] if requests.exists() else []
        return rows, seen, logs.stdout + logs.stderr
    finally:
        for name in ('fb', 'api', 'capture'):
            subprocess.run(docker + ['rm', '-f', f'{net}-{name}'], capture_output=True)
        subprocess.run(docker + ['network', 'rm', net], capture_output=True)


def check(rows, seen):
    failures = []
    # Two lists, each followed by a watch from its resource version; the second watch stays open.
    requests = [(urlsplit(r['path']).path, parse_qs(urlsplit(r['path']).query)) for r in seen]
    want = [r for v in FIXTURES['list_resource_versions']
            for r in (('/api/v1/events', {}), ('/api/v1/events', {'watch': ['1'], 'resourceVersion': [v], 'timeoutSeconds': ['600']}))]
    if requests != want:
        failures.append(f'expected requests {want}, saw {requests}')
    if any(r['authorization'] != f'Bearer {TOKEN}' for r in seen):
        failures.append('a request did not carry the service account token')
    if len(rows) != len(expected()):
        failures.append(f'expected {len(expected())} records, received {len(rows)}')
    by_key = {}
    for row in rows:
        key = (row['attributes'].get('k8s.event.uid'), row['attributes'].get('k8s.event.count'))
        if key in by_key:
            failures.append(f'duplicate record for event {key}')
        by_key[key] = row
    for case in FIXTURES['events']:
        name, event = case['name'], case['event']
        key = (event['metadata'].get('uid'), (event.get('series') or {}).get('count') or event.get('count') or None)
        row = by_key.get(key)
        if case.get('dropped'):
            if row is not None:
                failures.append(f'{name}: should be dropped, got {row.get("body")!r}')
            continue
        if row is None:
            failures.append(f'{name}: no record')
            continue
        exp = case['expect']

        def compare(label, want, got):
            # An integer field sent as a double decodes as a float.
            if want != got or type(want) is int and type(got) is float:
                failures.append(f'{name}: {label} expected {want!r}, got {got!r}')

        if row.get('stream_fields') != STREAM_FIELDS:
            failures.append(f'{name}: VL-Stream-Fields header {row.get("stream_fields")!r}')
        if row.get('ignore_fields') != 'scope.name,scope.version':
            failures.append(f'{name}: VL-Ignore-Fields header {row.get("ignore_fields")!r}')
        compare('resource', RESOURCE, row['resource'])
        compare('timestamp', case['_times'][exp['timestamp']][0], row.get('timestamp'))
        for key in ('severity_number', 'severity_text', 'body'):
            if key in exp:
                compare(key, exp[key], row.get(key, 0 if key == 'severity_number' else None))
        for key, want in exp.get('attributes', {}).items():
            if isinstance(want, str) and want.startswith('@'):
                want = case['_times'][want[1:]][1]
            compare(f'attribute {key}', want, row['attributes'].get(key))
        for key in exp.get('absent', []):
            if key in row['attributes']:
                failures.append(f'{name}: attribute {key} should be absent, got {row["attributes"][key]!r}')
        if 'otlp' in row['attributes']:
            failures.append(f'{name}: internal metadata leaked into attributes')
    return failures


def main():
    base = Path(os.environ.get('FLUENT_BIT_TEST_TMP', Path.home() / '.cache'))
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        work = Path(tmp)
        (work / 'state').mkdir()
        (work / 'token').write_text(TOKEN)
        (work / 'plan.json').write_text(json.dumps(build_plan(datetime.datetime.now(datetime.timezone.utc))))
        (work / 'test.conf').write_text(test_config((CONFIG / 'fluent-bit.conf').read_text()))
        rows, seen, logs = run(work)
        failures = check(rows, seen)
        if failures:
            print(logs[-4000:], file=sys.stderr)
            print('\n'.join(failures), file=sys.stderr)
            if os.environ.get('FLUENT_BIT_TEST_DUMP'):
                print(json.dumps(rows, indent=1), file=sys.stderr)
            sys.exit(1)
    print(f'Fluent Bit events mapping: {len(FIXTURES["events"])} fixtures passed.')


if __name__ == '__main__':
    main()
