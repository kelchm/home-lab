#!/usr/bin/env python3
"""Render the Kubernetes / Logs dashboard from the stored OpenTelemetry fields."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The Severity choices are the OpenTelemetry severity_number ranges.
BANDS = (('trace', 1, 4), ('debug', 5, 8), ('info', 9, 12), ('warning', 13, 16), ('error', 17, 20), ('critical', 21, 24))
namespace_query = 'k8s.namespace.name:*'
service_query = 'k8s.namespace.name:in(${namespace})'
expr = ('k8s.namespace.name:in(${namespace}) service.name:in(${service}) log.iostream:in(${stream})\n'
        + '\n'.join(f'| format if (severity_number:range[{low}, {high}]) "{name}" as level' for name, low, high in BANDS)
        + '\n| format if (level:="") "unknown" as level'
        + '\n| filter level:in(${level})')
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
    variable('namespace', 'Namespace', query=namespace_query, field='k8s.namespace.name'),
    variable('service', 'Service', query=service_query, field='service.name'),
    variable('stream', 'Stream', values=('stdout', 'stderr')),
    variable('level', 'Severity', values=tuple(name for name, _, _ in BANDS) + ('unknown',)),
]
# Start with a bounded part of the cluster; users can choose any namespace or All.
variables[0]['current'] = {'text': ['observability'], 'value': ['observability']}
dashboard = {
    'uid': 'kubernetes-logs', 'title': 'Kubernetes / Logs', 'schemaVersion': 39,
    'description': 'Container logs from the cluster. Opens on the last 15 minutes because log queries over long ranges are slow.',
    'tags': ['kubernetes', 'logs'], 'timezone': 'browser', 'refresh': '30s',
    'editable': False, 'graphTooltip': 1,
    'time': {'from': 'now-15m', 'to': 'now'},
    'links': [{'title': 'Observability / Pipeline', 'url': '/d/observability-pipeline', 'type': 'link',
               'keepTime': True, 'includeVars': False, 'targetBlank': False}],
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
