#!/usr/bin/env python3
"""Keep provisioned logging queries identical to the tested compatibility pipe."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
pipe = (ROOT / 'scripts/logging/compat.logsql').read_text().strip()
# Suggestions need the same metadata aliases as the logs, without parsing severity.
metadata_pipe = pipe.split('\n| unpack_logfmt ', 1)[0]
namespace_scope = '(kubernetes.pod_namespace:in(${namespace}) OR namespace:in(${namespace}))'
namespace_query = '*\n' + metadata_pipe + '\n| filter namespace:*'
service_query = namespace_scope + '\n' + metadata_pipe + '\n| filter namespace:in(${namespace}) service_name:*'
expr = (namespace_scope + '\n' + pipe
        + '\n| format if (level:="") "unknown" as level'
        + '\n| filter namespace:in(${namespace}) service_name:in(${service}) stream:in(${stream}) level:in(${level})')
datasource = {'type': 'victoriametrics-logs-datasource', 'uid': 'victorialogs'}


def variable(name, label, *, query=None, field=None, values=()):
    result = {
        'name': name, 'label': label, 'type': 'query' if query else 'custom',
        'multi': True, 'includeAll': True, 'allValue': '*', 'allowCustomValue': False,
        'current': {}, 'options': [],
    }
    if query:
        result.update(
            datasource=datasource, refresh=2, sort=1,
            description='Search values present in the selected time range (up to 1,000 suggestions).',
            query={'type': 'fieldValue', 'field': field, 'query': query, 'limit': 1000,
                   'refId': 'VictoriaLogsVariableQueryEditor-VariableQuery'},
        )
    else:
        result.update(query=','.join(values),
                      options=[{'text': value, 'value': value, 'selected': False} for value in values])
    return result


variables = [
    variable('namespace', 'Namespace', query=namespace_query, field='namespace'),
    variable('service', 'Service', query=service_query, field='service_name'),
    variable('stream', 'Stream', values=('stdout', 'stderr')),
    variable('level', 'Severity', values=('trace', 'debug', 'info', 'warning', 'error', 'critical', 'unknown')),
]
# Start with a bounded part of the cluster; users can choose any namespace or All.
variables[0]['current'] = {'text': ['observability'], 'value': ['observability']}
dashboard = {
    'uid': 'kubernetes-logs', 'title': 'Kubernetes logs', 'schemaVersion': 39,
    'tags': ['logging'], 'timezone': 'browser', 'refresh': '30s',
    'time': {'from': 'now-15m', 'to': 'now'},
    'templating': {'list': variables},
    'panels': [{
        'id': 1, 'type': 'logs', 'title': 'Container logs',
        'description': 'Choose a namespace, service and time range. Stream and severity start at All. Shows up to 1,000 matching lines; narrow the time range if needed. Open a row\'s log menu for details and surrounding logs.',
        'datasource': datasource,
        'gridPos': {'h': 22, 'w': 24, 'x': 0, 'y': 0},
        'options': {'showTime': True, 'showLabels': False, 'wrapLogMessage': True, 'sortOrder': 'Descending'},
        'targets': [{'refId': 'A', 'expr': expr, 'editorMode': 'code', 'queryType': 'instant',
                     'maxLines': 1000, 'adHocFiltersMode': 'off'}],
    }],
}
output = ROOT / 'kubernetes/apps/observability/grafana/app/kubernetes-logs-dashboard.json'
rendered = json.dumps(dashboard, indent=2) + '\n'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
if args.check:
    assert output.read_text() == rendered, 'Regenerate the Kubernetes logs dashboard'
else:
    output.write_text(rendered)
