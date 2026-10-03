#!/usr/bin/env python3
"""Check exposure/identity invariants and exercise the actual config publisher."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BINARY = os.environ['VMAGENT_BIN']


def yaml_document(path):
    return json.loads(subprocess.check_output(['yq', '-o=json', '.', str(path)]))


for host in ('spark-1', 'spark-2'):
    project = ROOT / host
    compose = json.loads(subprocess.check_output([
        'docker', 'compose', '-f', str(project / 'compose.yaml'), 'config', '--format', 'json']))
    assert compose['name'] == host + '-monitoring'
    services = compose['services']
    assert set(services) == {'publish', 'node-exporter', 'gpu-exporter', 'vmagent'}
    for name, service in services.items():
        assert re.search(r'@sha256:[a-f0-9]{64}$', service['image'])
        assert service['user'] == '65534:65534' and service['read_only']
        assert service['cap_drop'] == ['ALL'] and 'no-new-privileges:true' in service['security_opt']
        assert not service.get('ports') and not service.get('privileged')
        assert int(service['mem_limit']) > 0 and float(service['cpus']) > 0
        assert service['network_mode'] == ('none' if name == 'publish' else 'host')
        if name != 'publish':
            assert any('ListenAddr=127.0.0.1:' in arg or 'listen-address=127.0.0.1:' in arg for arg in service['command'])
    assert services['gpu-exporter']['devices'][0]['source'] == 'nvidia.com/gpu=all'
    assert '-remoteWrite.maxDiskUsagePerURL=1GiB' in services['vmagent']['command']
    mounts = services['vmagent']['volumes']
    assert any(v['target'] == '/queue' and v['source'] == '/var/lib/spark-monitoring/queue' for v in mounts)
    assert any(v['target'] == '/credentials' and v['read_only'] and v['source'] == '/etc/spark-monitoring/credentials' for v in mounts)
    config = yaml_document(project / 'scrape.yaml')
    jobs = {job['job_name']: job for job in config['scrape_configs']}
    assert ('spark-inference' in jobs) == (host == 'spark-1')
    for job in jobs.values():
        target = job['static_configs'][0]
        assert all(t.startswith('127.0.0.1:') for t in target['targets'])
        assert target['labels']['host'] == host and target['labels']['instance'] == host
        assert 'cluster' not in target['labels']
    if host == 'spark-1':
        assert jobs['spark-inference']['scrape_interval'] == '1s'
        assert jobs['spark-inference']['metric_relabel_configs'][0]['regex'] == '(tensorfold|tensorfold_health):.*'
    target = yaml_document(project / '.doco-cd.yml')
    assert target['working_dir'] == 'sparks/host-monitoring/' + host
    assert target['reconciliation'] is False and target['prune_images'] is False
    deployer = yaml_document(ROOT / 'platform' / host / 'compose.yaml')['services']['doco-cd']
    assert deployer['environment']['DEPLOY_CONFIG_BASE_DIR'] == target['working_dir']
    assert deployer['ports'] == ['127.0.0.1:9120:9120']
    subprocess.run([BINARY, '-promscrape.config=' + str(project / 'scrape.yaml'), '-promscrape.config.dryRun'], check=True, capture_output=True)

    # Run the real rendered publisher script with scratch mounts and native VMagent.
    with tempfile.TemporaryDirectory(prefix='spark-publish-') as directory:
        scratch = Path(directory)
        for name in ('source', 'runtime', 'textfile'):
            (scratch / name).mkdir()
        script = services['publish']['command'][0].replace('$$', '$').replace('/vmagent-prod', shlex.quote(BINARY))
        for name in ('source', 'runtime', 'textfile'):
            script = script.replace('/' + name + '/', str(scratch / name) + '/')
        source = scratch / 'source/scrape.yaml'
        applied = scratch / 'runtime/scrape.yaml'
        metric = scratch / 'textfile/config.prom'
        original = (project / 'scrape.yaml').read_text()
        def publish():
            result = subprocess.run(['/bin/sh', '-ec', script], capture_output=True, text=True)
            return result
        source.write_text(original + '\nunsupported_field: true\n')
        assert publish().returncode != 0 and not applied.exists(), 'invalid first install must fail'
        source.write_text(original)
        assert publish().returncode == 0 and applied.read_text() == original
        assert 'spark_monitoring_config_success 1\n' in metric.read_text()
        source.write_text(original + '\nunsupported_field: true\n')
        assert publish().returncode == 0 and applied.read_text() == original, 'invalid update must preserve applied'
        failed = metric.read_text()
        assert 'spark_monitoring_config_success 0\n' in failed
        desired_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        applied_sha = hashlib.sha256(applied.read_bytes()).hexdigest()
        assert desired_sha != applied_sha and desired_sha in failed and applied_sha in failed
        source.write_text(original.replace('scrape_interval: 15s', 'scrape_interval: 20s', 1))
        assert publish().returncode == 0 and applied.read_text() == source.read_text()
        source.write_text(original)
        assert publish().returncode == 0 and applied.read_text() == original
print('PASS: pinned Compose, loopback exposure, host identity, deployment scope, native config validation and publisher failure/revert behavior')
