#!/usr/bin/env python3
"""Exercise the shared LogsQL pipe in an isolated scratch tenant (583:0).

Only sends synthetic events. Never point --url at production.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', required=True, help='Scratch VictoriaLogs URL through a local port-forward')
args = parser.parse_args()
if urllib.parse.urlparse(args.url).hostname not in ('127.0.0.1', 'localhost'):
    parser.error('Use a localhost port-forward to the scratch backend')
pipe = (Path(__file__).parent / 'compat.logsql').read_text()
run = str(uuid.uuid4())
rows = []
expected = {}


def add(message, level='', *, payload=None, namespace='fixture', service='fixture', legacy=False):
    index = str(len(rows))
    row = {'_time': dt.datetime.now(dt.timezone.utc).isoformat(), '_msg': message,
           'fixture_run': run, 'fixture_id': index}
    if legacy:
        row.update(namespace=namespace, service_name=service, pod='fixture', container='app', node='fixture-node', stream='stderr')
    else:
        row.update({'kubernetes.pod_namespace': namespace, 'kubernetes.pod_name': 'fixture',
                    'kubernetes.container_name': 'app', 'kubernetes.pod_node_name': 'fixture-node',
                    'kubernetes.pod_labels.app.kubernetes.io/name': service,
                    'kubernetes.pod_labels.app': 'wrong-legacy', 'output_stream': 'stderr'})
    row.update(payload or {})
    rows.append(row)
    expected[index] = {'level': level, 'namespace': namespace, 'service_name': service, 'stream': 'stderr',
                       'app_instance': row.get('app_instance', '') if legacy else row.get('kubernetes.pod_labels.app.kubernetes.io/instance', '')}


aliases = {'TRC':'trace', 'verbose':'debug', 'Informational':'info', 'WaRn':'warning', 'ERR':'error', 'PANIC':'critical'}
for raw, normalized in aliases.items():
    for field in ('level', 'log.level', 'severity_text', 'SeverityText', 'severity', 'lvl'):
        add('structured', normalized, payload={field: raw})
    add(f'level="{raw}" msg="logfmt"', normalized)
    add('retained', normalized, payload={'msg.level': raw}, legacy=True)
add('the words error and warning are not a level')
add('level=error')  # no message/time companion
add('prefix level=error msg=untrusted')  # no logfmt envelope
add('plain', payload={'level': 'foreign'})
add('warning: renderer diagnostic', 'warning', namespace='iot', service='broadsheet')
add('warning: unrelated source')
add('2026-09-23 12:00:00,123 INFO [logger] diagnostic', 'info', namespace='printing', service='bambuddy')
add('[2026-09-23T12:00:00.123Z] [ERROR] [123] [server] failure', 'error', namespace='ai', service='mcphub')
add('  continuation with error text', namespace='ai', service='mcphub')
add('ordinary application fields', 'warning', payload={'namespace': 'application-namespace', 'service_name': 'application-service', 'level':'warn'})
add('old canonical wins', 'warning', legacy=True, payload={'level':'warning', 'msg.level':'error'})
# Label fallbacks, including container fallback, with no pre-existing alias.
add('legacy label')
rows[-1].pop('kubernetes.pod_labels.app.kubernetes.io/name')
expected[str(len(rows)-1)]['service_name'] = 'wrong-legacy'
add('container fallback')
rows[-1].pop('kubernetes.pod_labels.app.kubernetes.io/name')
rows[-1].pop('kubernetes.pod_labels.app')
rows[-1]['service_name'] = 'application-service'
expected[str(len(rows)-1)]['service_name'] = 'app'

add('instance label', payload={'kubernetes.pod_labels.app.kubernetes.io/instance': 'release', 'app_instance': 'application'})
add('instance absent', payload={'app_instance': 'application'})
add('retained instance', legacy=True, payload={'app_instance': 'release'})


def request(path, body, content_type):
    req = urllib.request.Request(args.url.rstrip('/') + path, data=body,
        headers={'AccountID':'583', 'ProjectID':'0', 'Content-Type': content_type})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode()) from exc


request('/insert/jsonline?_stream_fields=fixture_run&_time_field=_time',
        ''.join(json.dumps(row) + '\n' for row in rows).encode(), 'application/stream+json')
query = f'_time:10m fixture_run:="{run}"\n' + pipe + '\n| fields fixture_id,level,namespace,service_name,stream,app_instance'
# VL flushes ingested data asynchronously; force a bounded query retry.
import time
result = []
for attempt in range(15):
    result = [json.loads(line) for line in request('/select/logsql/query',
        urllib.parse.urlencode({'query':query, 'limit':1000}).encode(), 'application/x-www-form-urlencoded').splitlines()]
    if len(result) == len(rows):
        break
    time.sleep(1)
assert len(result) == len(rows), (len(result),len(rows))
seen = set()
for row in result:
    index = row['fixture_id']
    assert index not in seen, ('duplicate', index)
    seen.add(index)
    for field, value in expected[index].items():
        assert row.get(field, '') == value, (index, field, row, expected[index])
print(json.dumps({'fixtures':len(rows), 'passed':len(seen), 'tenant':'583:0'}))
