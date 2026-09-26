#!/usr/bin/env python3
"""Validate the upstream image, node-local state and staged cutover."""
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[2]
manifest = root / 'kubernetes/apps/observability/vlagent/app/daemonset.yaml'
d = json.loads(subprocess.check_output(['yq', '-o=json', '.', str(manifest)]))
pod = d['spec']['template']['spec']
c = pod['containers'][0]
assert re.fullmatch(r'victoriametrics/vlagent:[^@]+@sha256:[a-f0-9]{64}', c['image'])
assert '-remoteWrite.maxDiskUsagePerURL=4294967296' in c['args']
assert not any(a.startswith(('-kubernetesCollector.fieldsPrefix=', '-kubernetesCollector.useCRITimestamp=')) for a in c['args'])
security = c['securityContext']
assert security['readOnlyRootFilesystem'] and not security['allowPrivilegeEscalation']
assert security['capabilities']['drop'] == ['ALL']
assert security['seccompProfile']['type'] == 'RuntimeDefault'
assert pod['nodeSelector'] == {'logging.home.kelch.io/collector':'vlagent'}
assert sorted(v['hostPath']['path'] for v in pod['volumes']) == ['/var/lib/vlagent','/var/log/containers','/var/log/pods']
assert all(v.get('readOnly') for v in c['volumeMounts'] if v['name'] != 'data')
assert not pod.get('hostPID') and not pod.get('hostNetwork')
assert d['spec']['updateStrategy']['rollingUpdate'] == {'maxUnavailable':1, 'maxSurge':0}
print('Upstream image, mounts, hardening and staged selector validated.')
