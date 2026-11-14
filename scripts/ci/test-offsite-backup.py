#!/usr/bin/env python3
"""Exercise the scheduled inline backup command offline in its pinned image.

Only synthetic sources are mounted. Kopia, curl, date and sleep are shell stubs;
no repository, credentials, external notification or real backup is accessed.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--context')
args = parser.parse_args()
DOCKER = ['docker'] + (['--context', args.context] if args.context else [])
config = json.loads(subprocess.check_output(DOCKER + [
    'compose', '-f', str(ROOT / 'synology/offsite-backup/compose.yaml'),
    'config', '--no-interpolate', '--format', 'json',
]))['services']['kopia']
SCRIPT = config['command'][0].replace('$$', '$')
MARKERS = {
    'longhorn': 'longhorn/backupstore/volumes/aa/bb/pvc-fixture/volume.cfg',
    'visionect-db': 'object-storage/data/k8s-prod-visionect-db/visionect-db-pg17/base/20261114T080000/backup.info',
    'bambuddy-db': 'object-storage/data/k8s-prod-bambuddy-db/bambuddy-db-pg18/base/20261114T081000/backup.info',
    'dawarich-db': 'object-storage/data/k8s-prod-dawarich-db/dawarich-db-pg17/base/20261114T082000/backup.info',
}
STUBS = r'''
# Merge before Docker multiplexes the streams so diagnostics cannot split stub lines.
exec 2>&1
kopia() { printf 'KOPIA'; printf ' %s' "$@"; printf '\n'; }
curl() {
  printf 'CURL'; printf ' %s' "$@"; printf '\n'
  return "$FIXTURE_CURL_STATUS"
}
date() {
  case "$*" in
    '-u +%s') if (( sleeps == 0 )); then echo 100; else echo 201; fi ;;
    '-u -d today 10:00 +%s') echo 200 ;;
    '-u -d tomorrow 10:00 +%s') echo 300 ;;
    *) echo "Unexpected date call: $*" >&2; exit 90 ;;
  esac
}
sleeps=0
sleep() {
  sleeps=$(( sleeps + 1 ))
  # Two complete runs prove failures, including notification failure, return to
  # scheduling. Exit at the third wait instead of sleeping or touching production.
  if (( sleeps == 3 )); then exit 0; fi
  if (( sleeps == 1 )); then
    [[ "$1" = 100 ]] || exit 91
  else
    [[ "$1" = 99 ]] || exit 92
  fi
}
'''


def run_case(parent, label, mutate=None, invalid=None, curl_status=0):
    source = parent / label
    source.mkdir()
    for relative in MARKERS.values():
        marker = source / relative
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('synthetic archive metadata\n')
    if mutate:
        mutate(source)
    cmd = DOCKER + [
        'run', '--rm', '--network', 'none', '--read-only',
        '--user', config['user'], '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true',
        '--mount', f'type=bind,src={source},dst=/sources,readonly',
        '-e', 'B2_BUCKET=fixture', '-e', 'B2_ENDPOINT=invalid.example',
        '-e', 'RUN_AT_UTC=10:00', '-e', 'HC_PING_URL=https://invalid.example/ping',
        '-e', f'FIXTURE_CURL_STATUS={curl_status}',
        '--entrypoint', '/bin/bash', config['image'], '-c', STUBS + SCRIPT,
    ]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=30)
    output = result.stdout
    assert result.returncode == 0, (label, result.returncode, output)
    lines = output.splitlines()
    calls = [line for line in lines if line.startswith('KOPIA ')]
    assert calls[0].startswith('KOPIA repository connect s3 '), (label, output)
    pings = [line for line in lines if line.startswith('CURL ')]
    expected_status = '1' if invalid else '0'
    assert [line.split()[-1] for line in pings] == [
        f'https://invalid.example/ping/{suffix}'
        for suffix in ('start', expected_status, 'start', expected_status)
    ], (label, output)
    assert all('--fail --silent --show-error --max-time 10 --retry 5' in ping
               for ping in pings), (label, output)
    if invalid:
        assert len(calls) == 1, (label, 'snapshot or maintenance ran', output)
        assert output.count(f'Refusing off-site backup: {invalid} has no nonempty archive marker') == 2, (label, output)
    else:
        assert calls[1:] == [
            'KOPIA snapshot create /sources/longhorn /sources/object-storage',
            'KOPIA maintenance run --full',
        ] * 2, (label, output)
    print(f'PASS: {label}')


# A checkout-local fixture is also bind-mountable by macOS Colima contexts.
with tempfile.TemporaryDirectory(prefix='.offsite-backup-test-', dir=ROOT) as temp:
    parent = Path(temp)
    run_case(parent, 'healthy')
    for share, invalid in [('longhorn', 'longhorn'), ('object-storage', 'visionect-db')]:
        run_case(parent, f'{share}-missing',
                 lambda source, share=share: shutil.rmtree(source / share), invalid)

        def empty_share(source, share=share):
            shutil.rmtree(source / share)
            (source / share).mkdir()

        run_case(parent, f'{share}-empty', empty_share, invalid)
    for archive, relative in MARKERS.items():
        run_case(parent, f'{archive}-marker-empty',
                 lambda source, relative=relative: (source / relative).write_text(''), archive)
        run_case(parent, f'{archive}-marker-missing',
                 lambda source, relative=relative: (source / relative).unlink(), archive)
    run_case(parent, 'failed-notification-keeps-loop',
             lambda source: shutil.rmtree(source / 'longhorn'), 'longhorn', curl_status=22)

print('Off-site backup guard regression tests passed.')
