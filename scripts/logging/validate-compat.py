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


def add(message, level='', *, payload=None, namespace='fixture', service='fixture', stream='stderr', legacy=False):
    index = str(len(rows))
    row = {'_time': dt.datetime.now(dt.timezone.utc).isoformat(), '_msg': message,
           'fixture_run': run, 'fixture_id': index}
    if legacy:
        row.update(namespace=namespace, service_name=service, pod='fixture', container='app', node='fixture-node', stream=stream)
    else:
        row.update({'kubernetes.pod_namespace': namespace, 'kubernetes.pod_name': 'fixture',
                    'kubernetes.container_name': 'app', 'kubernetes.pod_node_name': 'fixture-node',
                    'kubernetes.pod_labels.app.kubernetes.io/name': service,
                    'kubernetes.pod_labels.app': 'wrong-legacy', 'output_stream': stream})
    row.update(payload or {})
    rows.append(row)
    expected[index] = {'level': level, 'namespace': namespace, 'service_name': service, 'stream': stream,
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
add('retained-only service', legacy=True, namespace='retained-only', service='archived.v1')
add('native-only service', namespace='native-only', service='checkout-api')
add('level=info msg="stdout event"', 'info', stream='stdout')


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

# Exercise the generated dashboard's queries against both schemas. Grafana's
# interpolation and dropdown interaction are checked separately in the browser.
dashboard_path = Path(__file__).resolve().parents[2] / 'kubernetes/apps/observability/grafana/app/kubernetes-logs-dashboard.json'
dashboard = json.loads(dashboard_path.read_text())
variables = {variable['name']: variable for variable in dashboard['templating']['list']}
panel_query = dashboard['panels'][0]['targets'][0]['expr']


def interpolate(query, selection):
    for name in variables:
        values = selection.get(name)
        literal = '*' if values is None else ','.join(json.dumps(value) for value in values)
        query = query.replace('${' + name + '}', literal)
    assert '${' not in query, query
    return f'_time:10m fixture_run:="{run}" AND ' + query


def field_values(name, selection):
    variable_query = variables[name]['query']
    response = request('/select/logsql/field_values', urllib.parse.urlencode({
        'query': interpolate(variable_query['query'], selection),
        'field': variable_query['field'], 'limit': variable_query['limit'],
    }).encode(), 'application/x-www-form-urlencoded')
    return {item['value'] for item in json.loads(response)['values']}


assert field_values('namespace', {}) == {row['namespace'] for row in expected.values()}
suggestion_cases = (None, ['fixture'], ['retained-only'], ['native-only'], ['printing', 'ai'], ['application-namespace'])
for namespaces in suggestion_cases:
    wanted = {row['service_name'] for row in expected.values()
              if namespaces is None or row['namespace'] in namespaces}
    assert field_values('service', {'namespace': namespaces}) == wanted, namespaces

selection_cases = [
    {},
    {'namespace': ['retained-only'], 'service': ['archived.v1']},
    {'namespace': ['native-only'], 'service': ['checkout-api']},
    {'namespace': ['printing', 'ai'], 'level': ['info', 'error']},
    {'namespace': ['fixture'], 'level': ['trace']},
    {'namespace': ['fixture'], 'level': ['unknown']},
    {'stream': ['stdout']},
    {'stream': ['stdout', 'stderr'], 'level': ['info', 'warning']},
    {'service': ['app', 'wrong-legacy']},
    {'namespace': ['application-namespace']},
    {'service': ['application-service']},
]
fields = {'namespace': 'namespace', 'service': 'service_name', 'stream': 'stream', 'level': 'level'}
for selection in selection_cases:
    query = interpolate(panel_query, selection) + '\n| fields fixture_id'
    result = [json.loads(line) for line in request('/select/logsql/query',
        urllib.parse.urlencode({'query': query, 'limit': 1000}).encode(),
        'application/x-www-form-urlencoded').splitlines()]
    wanted = {index for index, row in expected.items()
              if all((row[fields[name]] or ('unknown' if name == 'level' else '')) in values
                     for name, values in selection.items())}
    assert {row['fixture_id'] for row in result} == wanted, selection
    assert len(result) == len(wanted), ('duplicate', selection)

print(json.dumps({'fixtures':len(rows), 'passed':len(seen), 'tenant':'583:0',
                  'dashboard_suggestions': 1 + len(suggestion_cases),
                  'dashboard_selections': len(selection_cases)}))
