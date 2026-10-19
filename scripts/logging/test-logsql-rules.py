#!/usr/bin/env python3
"""Evaluate every LogsQL alert rule against fixture records in VictoriaLogs.

Each VMRule labelled alerting.home.kelch.io/datasource=victorialogs must have
cases in logsql-rules-fixtures.json. The fixtures are stored-schema records;
each case's records sit a day apart from every other case's, and the rule's
expression is run through the stats query API that vmalert calls, at the
case's evaluation time less vmalert's evaluation delay. vmalert parses the rule files in dry-run mode first.
`for` durations are not exercised. Requires Docker. Keep the images at the
VictoriaLogs and vmalert versions the cluster runs.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VL_IMAGE = 'victoriametrics/victoria-logs:v1.53.0@sha256:251121fa882af99b95ba0c230a4a2f412ea602d2698c64a96c58dc9842bb755d'
VMALERT_IMAGE = 'victoriametrics/vmalert:v1.154.0@sha256:f11d77f9ff68dfd63c871d0e59ae7b479f9c3fd0e8a685fef690fb88b794d938'
STREAM_FIELDS = 'service.name,k8s.namespace.name,k8s.pod.name,k8s.container.name,k8s.node.name,cef.device_product,cef.device_event_class_id'
FIXTURES = json.loads((ROOT / 'scripts/logging/logsql-rules-fixtures.json').read_text())
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
# vmalert's -rule.evalDelay default, which vmalert-victoria-logs keeps: it queries 30 s in the past.
EVAL_DELAY = timedelta(seconds=30)


def vlogs_rule_files():
    files = []
    for path in sorted((ROOT / 'kubernetes').rglob('*.yaml')):
        if 'kind: VMRule' not in path.read_text():
            continue
        out = subprocess.run(['yq', '-o=json', '-I=0', 'select(.kind == "VMRule")', str(path)], check=True, capture_output=True, text=True).stdout
        for doc in map(json.loads, out.splitlines()):
            if doc['metadata'].get('labels', {}).get('alerting.home.kelch.io/datasource') == 'victorialogs':
                files.append((path, doc))
    return files


def records(cases):
    out = []
    for i, case in enumerate(cases):
        at = BASE + timedelta(days=i)
        case['_time'] = at
        for rec in case['records']:
            for n in range(rec.get('repeat', 1)):
                # "{n}" in a string field becomes the repeat index, e.g. a request ID.
                row = {k: v.replace('{n}', str(n)) if isinstance(v, str) else v for k, v in rec['fields'].items()}
                row['_time'] = (at + timedelta(seconds=rec['offset'] + n * rec.get('every', 0))).isoformat().replace('+00:00', 'Z')
                out.append(row)
    return out


def http(url, data=None):
    # A form content type would make VictoriaLogs read the body as parameters.
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/stream+json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()


def main():
    rule_files = vlogs_rule_files()
    rules = {r['alert']: r for _, doc in rule_files for g in doc['spec']['groups'] for r in g['rules'] if 'alert' in r}
    tested = {c['rule'] for c in FIXTURES['cases']}
    failures = [f'{name}: no fixture cases' for name in sorted(set(rules) - tested)]
    failures += [f'{name}: fixture names an unknown rule' for name in sorted(tested - set(rules))]

    name = f'logsql-{uuid.uuid4().hex[:8]}'
    docker = ['docker']
    groups = {'groups': [g for _, doc in rule_files for g in doc['spec']['groups']]}
    rules_path = Path(os.environ.get('LOGSQL_TEST_TMP', Path.home() / '.cache')) / f'{name}-rules.json'
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(json.dumps(groups))
    try:
        dry = subprocess.run(docker + ['run', '--rm', '-v', f'{rules_path}:/rules.json:ro', VMALERT_IMAGE, '-rule=/rules.json', '-dryRun',
                                       '-datasource.url=http://unused:9428'], capture_output=True, text=True)
        if dry.returncode:
            failures.append(f'vmalert -dryRun rejected the rules:\n{dry.stdout}{dry.stderr}')
        subprocess.run(docker + ['run', '-d', '--rm', '--name', name, '-p', '127.0.0.1::9428', VL_IMAGE, '-retentionPeriod=100y'],
                       check=True, capture_output=True)
        port = subprocess.run(docker + ['port', name, '9428/tcp'], check=True, capture_output=True, text=True).stdout.split(':')[-1].strip()
        url = f'http://127.0.0.1:{port}'
        deadline = time.time() + 60
        while True:
            try:
                http(f'{url}/health')
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(1)
        body = '\n'.join(json.dumps(r) for r in records(FIXTURES['cases'])).encode()
        http(f'{url}/insert/jsonline?_stream_fields={STREAM_FIELDS}&_time_field=_time&_msg_field=_msg', body)
        http(f'{url}/internal/force_flush', b'')
        for case in FIXTURES['cases']:
            rule = rules.get(case['rule'])
            if rule is None:
                continue
            q = urllib.parse.urlencode({'query': rule['expr'], 'time': (case['_time'] - EVAL_DELAY).isoformat().replace('+00:00', 'Z')})
            try:
                result = json.loads(http(f'{url}/select/logsql/stats_query?{q}'))['data']['result']
            except urllib.error.HTTPError as e:
                failures.append(f"{case['rule']} / {case['name']}: query rejected: {e.read().decode().strip()}")
                continue
            got = sorted(json.dumps([{k: v for k, v in r['metric'].items() if k != '__name__'}, r['value'][1]], sort_keys=True) for r in result)
            want = sorted(json.dumps([e.get('labels', {}), str(e['value'])], sort_keys=True) for e in case['expect'])
            if got != want:
                failures.append(f"{case['rule']} / {case['name']}: expected {want}, got {got}")
            used = set(re.findall(r'\$labels\.(\w+)', json.dumps(rule.get('annotations', {}))))
            for labels, _ in map(json.loads, got):
                if used - set(labels):
                    failures.append(f"{case['rule']} / {case['name']}: annotations use missing labels {sorted(used - set(labels))}")
    finally:
        subprocess.run(docker + ['rm', '-f', name], capture_output=True)
        rules_path.unlink(missing_ok=True)
    if failures:
        print('\n'.join(failures), file=sys.stderr)
        sys.exit(1)
    print(f'LogsQL rules: {len(FIXTURES["cases"])} cases for {len(rules)} rules passed.')


if __name__ == '__main__':
    main()
