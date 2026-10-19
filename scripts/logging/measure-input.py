#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["prometheus-client==0.24.1"]
# ///
"""Measure collector input and output volume per node, without exporting log contents."""
import argparse
import concurrent.futures
import json
import subprocess
import time
from prometheus_client.parser import text_string_to_metric_families

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--kubeconfig', required=True)
p.add_argument('--namespace', default='observability')
p.add_argument('--selector', default='app.kubernetes.io/name=fluent-bit')
p.add_argument('--seconds', type=int, default=300)
a = p.parse_args()
base = ['kubectl', '--kubeconfig', a.kubeconfig, '--request-timeout=15s', '-n', a.namespace]
pods = json.loads(subprocess.check_output(base + ['get', 'pods', '-l', a.selector, '-o', 'json'], timeout=20))['items']
if not pods:
    raise SystemExit(f'No collector pods match {a.selector!r} in namespace {a.namespace!r}')


def sample(pod):
    name = pod['metadata']['name']
    raw = subprocess.check_output(base + ['get', '--raw', f'/api/v1/namespaces/{a.namespace}/pods/{name}:2020/proxy/api/v2/metrics/prometheus'], text=True, timeout=20)
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
    assert start[node]['fluentbit_uptime'] <= end[node]['fluentbit_uptime'], 'Collector restarted; discard the interval'
    read = end[node]['fluentbit_input_bytes_total'] - start[node]['fluentbit_input_bytes_total']
    sent = end[node]['fluentbit_output_proc_bytes_total'] - start[node]['fluentbit_output_proc_bytes_total']
    result[node] = {'input_bytes': read, 'output_bytes': sent, 'output_bytes_per_second': round(sent / elapsed, 2)}
print(json.dumps({'seconds': round(elapsed, 2), 'nodes': result}, indent=2))
