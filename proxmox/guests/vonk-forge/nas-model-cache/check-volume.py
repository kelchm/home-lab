#!/usr/bin/env python3
"""Assert that an existing Docker cache volume is the exact intended NFS mount."""
import json
import subprocess
NAME = 'vonk-forge-models-pve-sbx'
EXPECTED = {'type': 'nfs', 'device': ':/volume1/'+NAME,
            'o': 'addr=10.32.25.5,nfsvers=4.1,proto=tcp,hard,timeo=600,retrans=2,nosuid,nodev,noexec'}

def validate(value):
    if value.get('Name') != NAME or value.get('Driver') != 'local' or value.get('Options') != EXPECTED:
        raise RuntimeError('Refusing volume with wrong name, driver or NFS options')

if __name__ == '__main__':
    result = subprocess.run(['docker', 'volume', 'inspect', NAME], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('Expected volume is absent or Docker is unavailable; create only from reviewed overlay')
    data = json.loads(result.stdout)
    if len(data) != 1:
        raise SystemExit('Unexpected volume inventory')
    validate(data[0])
    print(json.dumps({'volume': NAME, 'exact_nfs_options': True}))
