#!/usr/bin/env python3
"""Validate the Fluent Bit release: pinned image, hardening, mounts and pipeline guarantees."""
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[2]
app = root / 'kubernetes/apps/observability/fluent-bit/app'
values = json.loads(subprocess.check_output(['yq', '-o=json', '.spec.values', str(app / 'helmrelease.yaml')]))
assert values['image']['repository'] == 'fluent/fluent-bit'
assert re.fullmatch(r'[0-9.]+@sha256:[a-f0-9]{64}', values['image']['tag'])
assert values['existingConfigMap'] == 'fluent-bit-config'
security = values['securityContext']
assert security['readOnlyRootFilesystem'] and not security['allowPrivilegeEscalation']
assert security['capabilities']['drop'] == ['ALL']
assert security['seccompProfile']['type'] == 'RuntimeDefault'
assert not values.get('hostNetwork') and not values.get('hostPID')
assert sorted(v['hostPath']['path'] for v in values['daemonSetVolumes']) == ['/var/lib/fluent-bit', '/var/log/containers', '/var/log/pods']
assert all(m.get('readOnly') for m in values['daemonSetVolumeMounts'] if m['name'] != 'state')
assert values['updateStrategy']['rollingUpdate'] == {'maxUnavailable': 1, 'maxSurge': 0}

conf = (app / 'config/fluent-bit.conf').read_text()
setting = lambda key: re.search(rf'^\s*{re.escape(key)}\s+(\S+)', conf, re.M | re.I).group(1)
# Rotated files must not match the tail path: Fluent Bit would replay them.
assert setting('Path') == '/var/log/containers/*.log'
assert setting('multiline.parser') == 'cri'
assert setting('Read_from_Head').lower() == 'on'
assert setting('storage.type') == 'filesystem' and setting('storage.sync') == 'full'
assert setting('DB').startswith('/var/lib/fluent-bit/') and setting('storage.path').startswith('/var/lib/fluent-bit/')
assert setting('Retry_Limit') == 'no_limits'
assert 'VL-Ignore-Fields scope.name,scope.version' in conf
# Off stops tailing the whole file at the first line over Buffer_Max_Size.
assert setting('Skip_Long_Lines').lower() == 'on'
assert setting('Logs_URI') == '/insert/opentelemetry/v1/logs'
print('Fluent Bit image, hardening, mounts and pipeline guarantees validated.')
