#!/usr/bin/env python3
"""Store synthetic flows in VictoriaLogs and run the UniFi / Flows dashboard's queries on them.

Rows go through the collector's real mapping and sink, then every VictoriaLogs
query in the committed dashboard runs through the API Grafana's datasource uses,
with the dashboard's own default variables. Also checks the checkpoint query the
collector resumes from and the stream count. Requires Docker, at the VictoriaLogs
version the cluster runs, unless VICTORIALOGS_URL names a running instance.
"""
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('collector', ROOT / 'kubernetes/apps/observability/unifi-flows/app/collector.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
DASHBOARD = json.loads((ROOT / 'kubernetes/apps/observability/unifi-flows/app/dashboards/unifi-flows.json').read_text())
ROWS = json.loads((ROOT / 'scripts/logging/unifi-flows-rows.json').read_text())
VL_IMAGE = re.search(r"VL_IMAGE = '([^']+)'", (ROOT / 'scripts/logging/test-logsql-rules.py').read_text()).group(1)
START, END = '2026-08-01T00:00:00Z', '2026-08-01T01:00:00Z'
DEFAULTS = {v['name']: v['current']['value'] for v in DASHBOARD['templating']['list']}


def post(base, path, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(base + path, data=data), timeout=30) as response:
        return response.read().decode()


def expand(expr, **overrides):
    values = {**DEFAULTS, **overrides}
    return re.sub(r'\$(\w+)', lambda m: values[m.group(1)], expr)


def targets():
    for panel in DASHBOARD['panels']:
        for target in panel.get('targets', []):
            if target['datasource']['uid'] == 'victorialogs':
                yield panel['title'], target


def run(base, target, **overrides):
    """Rows from a logs query, or {labels: total} from a range query."""
    expr = expand(target['expr'], **overrides)
    if target['queryType'] == 'statsRange':
        body = json.loads(post(base, '/select/logsql/stats_query_range', query=expr, start=START, end=END, step='1h'))
        return {tuple(sorted((k, v) for k, v in s['metric'].items() if k != '__name__')): sum(float(p[1]) for p in s['values']) for s in body['data']['result']}
    text = post(base, '/select/logsql/query', query=expr, start=START, end=END, limit=target.get('maxLines', 200))
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def fixture_lines():
    # The first row twice, as an overlap after a restart stores it, and a row with no port.
    rows = [*ROWS, ROWS[0]]
    icmp = copy.deepcopy(ROWS[0])
    icmp.update(id='0000000000000000000000ff', protocol='ICMP', time=ROWS[0]['time'] + 30000)
    del icmp['destination']['port']
    return [json.dumps(collector.flow_record(row)) for row in [*rows, icmp]]


def check(base):
    config = collector.Config(logs_url=base)
    sink = collector.Sink(config)
    instance = collector.Collector(config, console=object(), sink=sink, clock=lambda: 1785542700.0)
    assert sink.last_watermark() is None, 'no checkpoint yet; a local instance must start empty'
    instance.watermark = 1785542640000
    instance.gap(1785542000000, 1785542060000, 'query_cap')
    sink.write(fixture_lines() + instance.pending)
    # Checkpoints carry the time they were written, inside the collector's look-back.
    now = time.time()
    instance.clock = lambda: now - 60
    sink.write([instance.checkpoint('ok')])
    instance.clock = lambda: now
    instance.watermark = 1785542700000
    sink.write([instance.checkpoint('ok')])
    deadline = time.time() + 20
    while sink.last_watermark() != 1785542700000:
        assert time.time() < deadline, f'newest checkpoint never returned: {sink.last_watermark()}'
        time.sleep(0.5)

    panels = {}
    columns = {p['title']: next(t for t in p['transformations'] if t['id'] == 'organize')['options']['includeByName'] for p in DASHBOARD['panels'] if p['type'] == 'table'}
    for title, target in targets():
        panels[title] = target
        # Every scope, so a column such as destination.domain has a row to appear in.
        rows = run(base, target, scope='.*')
        # Grafana's datasource returns each row's fields, plus _time as the frame's Time.
        for name in columns.get(title, ()):
            assert any(name in row for row in rows) or (name == 'Time' and '_time' in rows[0]), (title, name)
    assert set(panels) == {'Coverage gaps', 'Flows between networks', 'Network dependencies', 'Recent flows'}, set(panels)

    by_pair = run(base, panels['Flows between networks'])
    totals = {(dict(k)['source.network'], dict(k)['destination.network']): v for k, v in by_pair.items()}
    assert totals == {('Default', 'IoT'): 2, ('Workloads', 'K8s Prod'): 1, ('IoT', 'Infra Mgmt'): 1}, totals

    table = run(base, panels['Network dependencies'])
    default = {(r['source.network'], r['network.transport'], r.get('destination.port', '')): r for r in table}
    assert len(table) == 4, table
    assert default[('Default', 'tcp', '80')]['flows'] == '1', 'the repeated row counts once'
    assert default[('Default', 'icmp', '')]['flows'] == '1', 'a row with no port still matches the default filter'
    assert default[('IoT', 'tcp', '22')]['flow.action'] == 'blocked'
    assert default[('Workloads', 'tcp', '443')]['bytes'] == '5000'
    assert default[('Workloads', 'tcp', '443')]['last_seen'].startswith('2026-08-01T00:01:00'), default[('Workloads', 'tcp', '443')]

    for overrides, expected in (
        ({'port': '443'}, {'Workloads'}),
        ({'protocol': 'icmp'}, {'Default'}),
        ({'source_network': 'Work.*'}, {'Workloads'}),
        ({'destination_network': 'IoT'}, {'Default'}),
        ({'scope': 'internet'}, {'Main'}),
        ({'scope': '.*', 'port': '443'}, {'Workloads', 'Main'}),
    ):
        found = {r['source.network'] for r in run(base, panels['Network dependencies'], **overrides)}
        assert found == expected, (overrides, found)

    recent = run(base, panels['Recent flows'])
    assert [r['flow.id'] for r in recent][0] == '000000000000000000000004', 'newest first'
    assert recent[0]['source.address'] == '192.0.2.50' and recent[0]['_time'].startswith('2026-08-01T00:03:00')

    gaps = run(base, panels['Coverage gaps'])
    assert [g['gap.reason'] for g in gaps] == ['query_cap'] and float(gaps[0]['gap.seconds']) == 60, gaps

    streams = json.loads(post(base, '/select/logsql/streams', query='{service.name="unifi-flows"}', start=START))
    assert len(streams['values']) == 3, streams
    stored = run(base, {'expr': '{service.name="unifi-flows",record.type="flow"} | limit 1', 'queryType': 'instant'})[0]
    assert not {'source.mac', 'destination.mac', 'source.client_fingerprint'} & set(stored), stored
    print(f'UniFi flow mapping, ingestion, checkpoint and {len(panels)} dashboard queries validated.')


def main():
    base = os.environ.get('VICTORIALOGS_URL')
    if base:
        return check(base)
    name = f'unifi-flows-{uuid.uuid4().hex[:8]}'
    subprocess.run(['docker', 'run', '-d', '--rm', '--name', name, '-p', '127.0.0.1::9428', VL_IMAGE, '-retentionPeriod=100y'], check=True, capture_output=True)
    try:
        port = subprocess.run(['docker', 'port', name, '9428/tcp'], check=True, capture_output=True, text=True).stdout.split(':')[-1].strip()
        base = f'http://127.0.0.1:{port}'
        deadline = time.time() + 30
        while True:
            try:
                urllib.request.urlopen(base + '/health', timeout=2)
                break
            except OSError:
                assert time.time() < deadline, 'VictoriaLogs did not start'
                time.sleep(0.5)
        check(base)
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True)


if __name__ == '__main__':
    sys.exit(main())
