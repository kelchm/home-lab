#!/usr/bin/env python3
"""Keep provisioned logging queries identical to the tested compatibility pipe."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
pipe = (ROOT / 'scripts/logging/compat.logsql').read_text().strip()
expr = '(kubernetes.pod_namespace:~"$${namespace:raw}" OR namespace:~"$${namespace:raw}")\n' + pipe + '\n| filter namespace:~"$${namespace:raw}" service_name:~"$${service:raw}" stream:~"$${stream:raw}" level:~"$${level:raw}"'
dashboard = {
    'uid': 'kubernetes-logs', 'title': 'Kubernetes logs', 'schemaVersion': 39,
    'tags': ['logging'], 'timezone': 'browser', 'refresh': '30s',
    'time': {'from': 'now-15m', 'to': 'now'},
    'templating': {'list': [
        {'name': name, 'label': label, 'type': 'textbox', 'query': '.*',
         'current': {'text': '.*', 'value': '.*'}}
        for name, label in [('namespace', 'Namespace (regex)'), ('service', 'Service (regex)'),
                            ('stream', 'stdout / stderr (regex)'), ('level', 'Canonical severity (regex)')]]
    },
    'panels': [{
        'id': 1, 'type': 'logs', 'title': 'Container logs — Alloy history and vlagent',
        'description': 'Explicit severity only. Namespace selection precedes compatibility parsing. Use a narrow time range.',
        'datasource': {'type': 'victoriametrics-logs-datasource', 'uid': 'victorialogs'},
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
