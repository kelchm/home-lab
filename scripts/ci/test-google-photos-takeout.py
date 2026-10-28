#!/usr/bin/env python3
"""Run the actual inline collector in its pinned image, offline, with tiny ZIPs.

Local use: python3 scripts/ci/test-google-photos-takeout.py --context colima-tf-build
CI uses the default Docker context. No images are built; no credentials are read.
"""

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / 'synology/google-photos-takeout/compose.yaml'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--context')
args = parser.parse_args()
DOCKER = ['docker'] + (['--context', args.context] if args.context else [])
config = json.loads(subprocess.check_output(DOCKER + [
    'compose', '-f', str(COMPOSE), '--profile', '*', 'config',
    '--no-interpolate', '--format', 'json',
]))['services']['collector']
SCRIPT = config['command'][0].replace('$$', '$')
IMAGE = config['image']
NOW = int(dt.datetime(2026, 10, 28, 12, tzinfo=dt.timezone.utc).timestamp())
PHOTO = 'Takeout/Google Photos/Album é /猫.jpg'
TIMELINE = 'Takeout/Timeline/Settings.json'
NAME = 'takeout-20261006T022413Z-1-001.zip'


def archive_name(epoch, suffix='1-001'):
    stamp = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return f'takeout-{stamp}-{suffix}.zip'


class Fixture:
    def __init__(self, parent, label):
        self.base = parent / label
        self.base.mkdir(mode=0o777)
        self.source = self.base / 'source'
        self.work = self.base / 'work'
        self.exports = self.base / 'exports'
        for path in (self.source, self.work, self.exports):
            path.mkdir(mode=0o777)
            path.chmod(0o777)

    def zip(self, name=NAME, members=None, corrupt=False):
        path = self.source / name
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as z:
            for member, data in (members if members is not None else [(PHOTO, b'PHOTO-BYTES')]):
                z.writestr(member, data)
        if corrupt:
            data = path.read_bytes()
            assert b'PHOTO-BYTES' in data
            path.write_bytes(data.replace(b'PHOTO-BYTES', b'BROKEN-DATA'))
        path.chmod(0o644)
        return path

    def run(self, expected=0, message='', fault='', now=NOW, mode='fixture', env=None):
        cmd = DOCKER + [
            'run', '--rm', '--network', 'none', '--read-only',
            '--user', config['user'], '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true',
            '--tmpfs', '/tmp', '--tmpfs', '/config/rclone:mode=0700,uid=1026,gid=100',
            '--mount', f'type=bind,src={self.source},dst=/source,readonly',
            '--mount', f'type=bind,src={self.work},dst=/work',
            '--mount', f'type=bind,src={self.exports},dst=/exports',
            '-e', 'SOURCE=/source', '-e', f'COLLECTOR_MODE={mode}',
            '-e', f'FIXTURE_NOW={now}', '-e', f'FIXTURE_FAULT={fault}',
        ]
        if fault == 'state-write':
            cmd += ['--mount', f'type=bind,src={self.work / "state/decisions"},dst=/work/state/decisions,readonly']
        for key, value in (env or {}).items():
            cmd += ['-e', f'{key}={value}']
        # Only inject clock and I/O faults. Classification/extraction/state and
        # freshness code are unchanged, and real rclone still reads each ZIP.
        setup = r'''
# Linux CI's host user differs from UID 1026. Open only these synthetic fixture
# artifacts after exit so the host can inspect and remove them.
trap 'find /work /exports -mindepth 1 -exec chmod a+rwX {} + 2>/dev/null || true' EXIT
date() {
  if [ "$*" = '-u +%s' ]; then echo "$FIXTURE_NOW"; else /bin/date "$@"; fi
}
rclone() {
  if [ "$FIXTURE_FAULT" = copy ] && [ "$1" = copyto ]; then return 71; fi
  if [ "$FIXTURE_FAULT" = classify ] && [ "$1 $2" = 'archive list' ]; then return 73; fi
  if [ "$FIXTURE_FAULT" = inventory ] && [ "$1" = lsf ]; then return 74; fi
  /usr/local/bin/rclone "$@"
}
mv() {
  case "$FIXTURE_FAULT:$*" in
    state:*decisions/*) echo 'fixture: state rename denied' >&2; return 72 ;;
  esac
  /bin/mv "$@"
}
'''
        result = subprocess.run(cmd + [
            '--entrypoint', '/bin/sh', IMAGE, '-c', setup + SCRIPT,
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        assert result.returncode == expected, (self.base.name, result.returncode, result.stdout)
        assert message in result.stdout, (self.base.name, message, result.stdout)
        print(f'PASS {self.base.name}: exit {expected}' + (f' ({fault})' if fault else ''))
        return result.stdout

    def decisions(self, kind=None):
        records = list((self.work / 'state/decisions').glob('*'))
        return [p for p in records if not p.name.startswith('.')
                and (kind is None or f' {kind} ' in p.read_text())]

    def output_files(self):
        return [p for p in self.exports.rglob('*') if p.is_file()]

    def part_file(self, name, member):
        source = self.source / name
        identity = f'{name}.{hashlib.md5(source.read_bytes()).hexdigest()}.{source.stat().st_size}'
        return self.exports / name.split('-')[1] / identity / member


def tests(parent):
    # Shellcheck the production logic, not a separately maintained script.
    script = parent / 'collector.sh'
    script.write_text(SCRIPT)
    subprocess.run(['shellcheck', '-s', 'sh', str(script)], check=True)
    assert config['profiles'] == ['inactive']
    assert config['environment']['COLLECTOR_ENABLED'] == 'false'
    assert all(not v['source'].startswith(str(ROOT)) for v in config['volumes'])

    f = Fixture(parent, 'photos-multipart-unicode-trailing-space')
    f.zip(members=[('Takeout/', b''), ('Takeout/Google Photos/', b''), (PHOTO, b'PHOTO-BYTES'),
                   ('Takeout/Google Photos/Album é /sidecar.json', b'{}')])
    f.zip(NAME.replace('001.zip', '002.zip'), [('Takeout/Google Photos/Year/a.jpg ', b'second')])
    (f.source / 'ignore.txt').write_text('not a ZIP')
    f.run(message='accepted Photos part:')
    assert len(f.decisions('accepted')) == 2 and len(f.output_files()) == 3
    assert f.part_file(NAME, PHOTO).read_bytes() == b'PHOTO-BYTES'
    assert f.part_file(NAME.replace('001.zip', '002.zip'), 'Takeout/Google Photos/Year/a.jpg ').read_bytes() == b'second'
    f.run(message='already committed')
    assert not list((f.work / 'archives').iterdir())

    # Crash between committing acceptance and removing scratch: never re-extract.
    (f.work / 'archives' / NAME).write_bytes(b'not even a ZIP')
    f.run(message='already committed')
    assert len(f.decisions('accepted')) == 2

    # Large bodies exercise rclone's streamed CRC-failure cleanup. Each part and
    # same-name replacement must leave previously accepted output intact.
    f = Fixture(parent, 'changed-identity-never-destroys-accepted-output')
    member = 'Takeout/Google Photos/Album/big.jpg'
    original = b'PHOTO-BYTES' + b'x' * (256 * 1024)
    source = f.zip(members=[(member, original)])
    original_zip = source.read_bytes()
    accepted = f.part_file(NAME, member)
    f.run()
    f.zip(members=[(member, original)], corrupt=True)
    f.run(1, message='checksum error')
    assert accepted.read_bytes() == original and len(f.decisions('accepted')) == 1
    source.write_bytes(original_zip)
    f.run(message='already committed')
    assert accepted.read_bytes() == original
    f.zip(members=[(member, b'changed valid content')])
    changed = f.part_file(NAME, member)
    f.run()
    assert changed != accepted and changed.read_bytes() == b'changed valid content'
    assert accepted.read_bytes() == original and len(f.decisions('accepted')) == 2

    f = Fixture(parent, 'colliding-parts-never-overwrite-accepted-output')
    f.zip(members=[(member, original)])
    accepted = f.part_file(NAME, member)
    f.run()
    second = NAME.replace('001.zip', '002.zip')
    f.zip(second, [(member, original)], corrupt=True)
    f.run(1, message='checksum error')
    assert accepted.read_bytes() == original and len(f.decisions('accepted')) == 1
    (f.source / second).unlink()
    f.run(message='already committed')
    assert accepted.read_bytes() == original
    f.zip(second, [(member, b'second part content')])
    f.run()
    assert f.part_file(second, member).read_bytes() == b'second part content'
    assert accepted.read_bytes() == original and len(f.decisions('accepted')) == 2

    for label, members, decision in [
        ('timeline', [(TIMELINE, b'{}')], 'ignored'),
        ('mixed', [(PHOTO, b'PHOTO-BYTES'), (TIMELINE, b'{}')], 'quarantined'),
        ('unknown', [('Takeout/Mail/mail.mbox', b'mail')], 'quarantined'),
        ('index-only', [('Takeout/archive_browser.html', b'Google Photos')], 'quarantined'),
        ('index-with-payload-unconfirmed', [(PHOTO, b'PHOTO-BYTES'), ('Takeout/archive_browser.html', b'unknown schema')], 'quarantined'),
        ('root-lookalike', [('Takeout/Google Photos Other/a.jpg', b'photo')], 'quarantined'),
        ('nested-lookalike', [('Other/Takeout/Google Photos/a.jpg', b'photo')], 'quarantined'),
        ('directory-only', [('Takeout/Google Photos/', b'')], 'quarantined'),
        ('empty-zip', [], 'quarantined'),
        ('filename-lookalike', [('Takeout/Google Photos', b'photo')], 'quarantined'),
        ('traversal', [(PHOTO, b'PHOTO-BYTES'), ('Takeout/Google Photos/../../Timeline/x', b'x')], 'quarantined'),
        ('hidden-unsafe', [(PHOTO, b'PHOTO-BYTES'), ('../../Timeline/x', b'x')], 'quarantined'),
        ('absolute', [('/Takeout/Google Photos/x', b'x')], 'quarantined'),
        ('backslash', [('Takeout/Google Photos/a\\b.jpg', b'x')], 'quarantined'),
        ('dot-component', [('Takeout/Google Photos/./x.jpg', b'x')], 'quarantined'),
        ('double-slash', [('Takeout/Google Photos//x.jpg', b'x')], 'quarantined'),
        ('control-name', [('Takeout/Google Photos/a\tb.jpg', b'x')], 'quarantined'),
        ('newline-plain-spoof', [('Takeout/Google Photos/a\nTakeout/Google Photos/b', b'x')], 'quarantined'),
        ('newline-sized-spoof', [('Takeout/Google Photos/a\n        1 Takeout/Google Photos/b', b'x')], 'quarantined'),
        ('newline-other-product', [('Takeout/Google Photos/a\nTakeout/Timeline/x', b'x')], 'quarantined'),
        ('duplicate-member', [(PHOTO, b'x'), (PHOTO, b'y')], 'quarantined'),
        ('nonempty-directory', [('Takeout/Google Photos/', b'not-a-directory-body'), (PHOTO, b'x')], 'quarantined'),
    ]:
        f = Fixture(parent, label)
        f.zip(members=members)
        f.run(1)
        assert len(f.decisions(decision)) == 1
        assert not f.output_files() and (f.work / 'archives' / NAME).exists()

    # A fresh previous Photos acceptance must not hide any new run failure.
    for fault in ('copy', 'state', 'state-write', 'classify', 'inventory'):
        f = Fixture(parent, f'{fault}-failure-after-fresh')
        f.zip()
        f.run()
        f.zip(NAME.replace('001.zip', '002.zip'))
        f.run(1, fault=fault)
        assert len(f.decisions('accepted')) == 1
        f.run()
        assert len(f.decisions('accepted')) == 2

    f = Fixture(parent, 'crc-retry-changed-same-name')
    f.zip()
    f.run()
    second = NAME.replace('001.zip', '002.zip')
    f.zip(second, corrupt=True)
    f.run(1, message='checksum error')
    assert len(f.decisions('accepted')) == 1 and len(f.decisions('quarantined')) == 1
    assert (f.work / 'archives' / second).exists()
    f.run(1, message='checksum error')
    f.zip(second)
    f.run()
    assert len(f.decisions('accepted')) == 2
    f.zip(second, [(PHOTO, b'valid media'), ('Takeout/Google Photos/sidecar.json', b'PHOTO-BYTES')], corrupt=True)
    f.run(1, message='checksum error')
    assert len(f.decisions('accepted')) == 2
    # Changing an already accepted name to Timeline cannot inherit acceptance.
    f.zip(second)
    f.zip(NAME, [(TIMELINE, b'{}')])
    f.run(message='ignored Timeline:')
    assert len(f.decisions('ignored')) == 1 and len(f.decisions('accepted')) == 2
    # A changed mixed archive still fails despite previously fresh acceptance.
    f.zip(NAME, [(PHOTO, b'PHOTO-BYTES'), (TIMELINE, b'{}')])
    f.run(1, message='quarantined unsafe, mixed or unknown')

    for label, age, expected in [('old-first-ingested-today', 100 * 86400, 1),
                                  ('boundary-minus-second', 40 * 86400 - 1, 0),
                                  ('boundary-exact', 40 * 86400, 1),
                                  ('boundary-plus-second', 40 * 86400 + 1, 1)]:
        f = Fixture(parent, label)
        f.zip(archive_name(NOW - age))
        f.run(expected, message=f'epoch={NOW - age};')
        assert len(f.decisions('accepted')) == 1

    f = Fixture(parent, 'old-after-new-and-source-removal')
    newer = NOW - 10 * 86400
    f.zip(archive_name(newer))
    f.run()
    f.zip(archive_name(NOW - 100 * 86400))
    f.run(message=f'epoch={newer};')
    for p in f.source.iterdir():
        p.unlink()
    f.run(message=f'epoch={newer};')
    f.run(1, now=newer + 40 * 86400, message='40 days')

    for label, name in [('invalid-calendar', 'takeout-20260230T000000Z-001.zip'),
                        ('invalid-time', 'takeout-20261006T250000Z-001.zip'),
                        ('malformed-name', 'takeout-2026106T022413Z-001.zip'),
                        ('future', archive_name(NOW + 1)),
                        ('ambiguous-source-newline', 'takeout-20261006T022413Z-001.zip\ntakeout-20261006T022413Z-002.zip')]:
        f = Fixture(parent, label)
        f.zip(name)
        f.run(1)
        assert not f.decisions('accepted') and not f.output_files()

    for label, target in [('legacy-last-arrival', 'state/last-arrival'),
                          ('legacy-extracted', 'state/extracted.txt'),
                          ('legacy-cached', 'archives/old.zip')]:
        f = Fixture(parent, label)
        p = f.work / target
        p.parent.mkdir(exist_ok=True)
        p.write_text('preserve me')
        f.zip()
        f.run(1, message='unversioned/nonempty')
        assert p.read_text() == 'preserve me' and not f.output_files()

    f = Fixture(parent, 'lost-state-nonempty-output')
    f.zip()
    f.run()
    shutil.rmtree(f.work / 'state')
    f.run(1, message='unversioned/nonempty')
    assert f.output_files()

    f = Fixture(parent, 'interrupted-version-write')
    (f.work / 'state').mkdir()
    # CI's host UID differs from the collector's; only synthetic fixture paths
    # are writable by both, as in Fixture.__init__ above.
    (f.work / 'state').chmod(0o777)
    (f.work / 'state/.version.tmp').write_text('partial marker')
    f.zip()
    f.run()
    assert len(f.decisions('accepted')) == 1
    assert not (f.work / 'state/.version.tmp').exists()

    f = Fixture(parent, 'stray-ledger-file-diagnostic')
    f.zip()
    f.run()
    (f.work / 'state/decisions/notes.txt').touch()
    f.run(1, message='ERROR: invalid state identity notes.txt')

    f = Fixture(parent, 'prior-shared-output-version-refused')
    (f.work / 'state').mkdir()
    (f.work / 'state/version').write_text('photos-v1\n')
    f.zip()
    f.run(1, message='unsupported state version')
    assert not f.decisions('accepted') and not f.output_files()

    f = Fixture(parent, 'timeline-never-refreshes-photos')
    photos_time = NOW - 39 * 86400
    f.zip(archive_name(photos_time))
    f.run()
    f.zip(archive_name(NOW), [(TIMELINE, b'{}')])
    f.run(message=f'epoch={photos_time};')
    f.run(1, now=photos_time + 40 * 86400, message='40 days')
    assert len(f.decisions('accepted')) == 1 and len(f.decisions('ignored')) == 1

    for label, change in [('corrupt-ledger', 'decision'), ('unknown-version', 'version')]:
        f = Fixture(parent, label)
        f.zip()
        f.run()
        target = f.decisions()[0] if change == 'decision' else f.work / 'state/version'
        target.write_text('invalid state')
        f.run(1, message='state')

    for label, env in [('inactive-default', {}),
                       ('missing-creds', {'COLLECTOR_ENABLED': 'true'}),
                       ('placeholder-creds', {'COLLECTOR_ENABLED': 'true', 'GDRIVE_CLIENT_ID': 'PLACEHOLDER'}),
                       ('encrypted-creds', {'COLLECTOR_ENABLED': 'true', 'GDRIVE_CLIENT_ID': 'ENC[AES256_GCM,dummy]'}),
                       ('empty-refresh', {'COLLECTOR_ENABLED': 'true', 'GDRIVE_CLIENT_ID': 'dummy.apps.googleusercontent.com',
                                          'GDRIVE_CLIENT_SECRET': 'dummy', 'GDRIVE_TOKEN': '{"refresh_token":""}',
                                          'HC_PING_URL': 'https://hc-ping.com/dummy'})]:
        f = Fixture(parent, label)
        f.run(1, mode='normal', env=env)
        assert not list(f.work.iterdir())


# Keep fixtures on the shared workspace filesystem for Colima bind mounts.
with tempfile.TemporaryDirectory(prefix='.photos-fixtures-', dir=ROOT) as temp:
    parent = Path(temp)
    parent.chmod(0o755)
    tests(parent)
print('All offline collector fixtures passed')
