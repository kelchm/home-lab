#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["prometheus-client==0.24.1"]
# ///
"""Measure compressed collector input per node, without exporting log contents."""
import argparse
import concurrent.futures
import json
import subprocess
import time
from prometheus_client.parser import text_string_to_metric_families

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--kubeconfig', required=True)
p.add_argument('--namespace', default='log-drill')
p.add_argument('--selector', default='app=log-drill-collector,arm=vlagent')
p.add_argument('--seconds', type=int, default=300)
a = p.parse_args()
base = ['kubectl', '--kubeconfig', a.kubeconfig, '--request-timeout=15s', '-n', a.namespace]
pods = json.loads(subprocess.check_output(base + ['get', 'pods', '-l', a.selector, '-o', 'json'], timeout=20))['items']
if not pods:
    raise SystemExit(f'No collector pods match {a.selector!r} in namespace {a.namespace!r}')


def sample(pod):
    name = pod['metadata']['name']
    raw = subprocess.check_output(base + ['get', '--raw', f'/api/v1/namespaces/{a.namespace}/pods/{name}:9429/proxy/metrics'], text=True, timeout=20)
    values = {}
    for family in text_string_to_metric_families(raw):
        for metric in family.samples:
            values[metric.name] = values.get(metric.name, 0) + metric.value
    return pod['spec']['nodeName'], values


def collect():
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return dict(pool.map(sample, pods))


start = collect()
begin = time.monotonic()
time.sleep(a.seconds)
end = collect()
elapsed = time.monotonic() - begin
result = {}
for node in start:
    assert start[node]['process_start_time_seconds'] == end[node]['process_start_time_seconds'], 'Collector restarted; discard the interval'
    metric = 'vlagent_remotewrite_block_size_bytes_sum'
    delta = end[node][metric] - start[node][metric]
    assert delta >= 0, 'Collector restarted; discard the interval'
    result[node] = {'compressed_bytes': delta, 'bytes_per_second': round(delta / elapsed, 2),
                    'estimated_json_bytes': end[node]['vl_bytes_ingested_total'] - start[node]['vl_bytes_ingested_total']}
print(json.dumps({'seconds': round(elapsed, 2), 'nodes': result}, indent=2))
