#!/usr/bin/env python3
"""Assert that an existing Docker cache volume is the exact intended NFS mount."""
import argparse
import json
import subprocess
SHARE = 'vonk-forge-models-pve-sbx'
NAME = SHARE+'-nconnect4'
EXPECTED = {'type': 'nfs', 'device': ':/volume1/'+SHARE,
            'o': 'addr=10.32.25.5,nfsvers=4.1,proto=tcp,hard,timeo=600,retrans=2,nosuid,nodev,noexec,nconnect=4'}

def validate(value, *, single_connection_rollback=False):
    name = SHARE if single_connection_rollback else NAME
    expected = dict(EXPECTED)
    if single_connection_rollback:
        expected['o'] = expected['o'].removesuffix(',nconnect=4')
    if value.get('Name') != name or value.get('Driver') != 'local' or value.get('Options') != expected:
        raise RuntimeError('Refusing volume with wrong name, driver or NFS options')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single-connection-rollback', action='store_true')
    args = parser.parse_args()
    name = SHARE if args.single_connection_rollback else NAME
    result = subprocess.run(['docker', 'volume', 'inspect', name], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('Expected volume is absent or Docker is unavailable; create only from reviewed overlay')
    data = json.loads(result.stdout)
    if len(data) != 1:
        raise SystemExit('Unexpected volume inventory')
    validate(data[0], single_connection_rollback=args.single_connection_rollback)
    print(json.dumps({'volume': name, 'exact_nfs_options': True}))
