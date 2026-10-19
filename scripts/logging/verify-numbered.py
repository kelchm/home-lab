#!/usr/bin/env python3
"""Verify exact sequence delivery and collector semantics for numbered-fixture.py."""
import argparse
import datetime as dt
from collections import Counter
import json
import urllib.parse
import urllib.request

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--url', required=True)
p.add_argument('--run', required=True)
p.add_argument('--expected', type=int, required=True)
p.add_argument('--start', required=True, help='Inclusive RFC3339 time')
p.add_argument('--end', required=True, help='Exclusive RFC3339 time')
p.add_argument('--app', required=True, help='Fixture pod\'s application name (app.kubernetes.io/name, app label or workload); prefixes its fields')
a = p.parse_args()
start, end = [dt.datetime.fromisoformat(t.replace('Z', '+00:00')) for t in (a.start, a.end)]
if start.tzinfo is None or end.tzinfo is None or start >= end:
    p.error('start/end must be ordered, timezone-aware timestamps')
if a.expected <= 0:
    p.error('expected must be positive')
prefix = a.app + '.'
limit = a.expected * 2 + 100
query = f'_time:[{start.isoformat()},{end.isoformat()}) ' + json.dumps(prefix + 'fixture_run') + ':=' + json.dumps(a.run)
req = urllib.request.Request(a.url.rstrip('/') + '/select/logsql/query',
    data=urllib.parse.urlencode({'query':query,'limit':limit,'timeout':'5s'}).encode())
with urllib.request.urlopen(req, timeout=30) as response:
    rows = [json.loads(line) for line in response.read().decode().splitlines()]
if len(rows) >= limit:
    raise SystemExit('Query reached its row limit; counts would be incomplete')
counts = Counter()
for row in rows:
    n = int(row[prefix + 'n'])
    counts[n] += 1
    assert row['k8s.namespace.name'] == 'log-drill', row['k8s.namespace.name']
    assert row['log.iostream'] == ('stderr' if n % 2 else 'stdout'), n
    assert row['severity_number'] == ('9' if n % 10 else '13'), n
    timestamp = dt.datetime.fromisoformat(row['_time'].replace('Z', '+00:00'))
    emitted = dt.datetime.fromisoformat(row[prefix + 'emitted_at'])
    assert start <= timestamp < end, n
    assert abs((timestamp - emitted).total_seconds()) < 1, n
    expected = 'fixture ' + ('x' * 40000 if n % 100 == 0 else str(n)) + ' end'
    assert row['_msg'] == expected, (n, len(row['_msg']))
missing = sorted(set(range(a.expected)) - counts.keys())
unexpected = sorted(counts.keys() - set(range(a.expected)))
duplicates = sum(c - 1 for c in counts.values())
print(json.dumps({'expected':a.expected, 'rows':len(rows), 'unique':len(counts),
                  'missing':len(missing), 'missing_ids':missing[:30], 'unexpected':unexpected,
                  'duplicates':duplicates, 'semantic_checks':'pass'}, indent=2))
raise SystemExit(bool(missing or unexpected or duplicates))
