#!/usr/bin/env python3
"""Move this quiesced cache to DSM-native ownership; preserve metadata rollback."""
import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import time

SHARE = 'vonk-forge-models-pve-sbx'
ROOT = Path('/volume1')/SHARE
ACL = '/usr/syno/bin/synoacltool'
CHILDREN = {'objects', 'partials', 'locks', 'manifests', 'quarantine'}
OLD_RULE = {'client': '10.32.21.101', 'privilege': 'rw', 'root_squash': 'guest',
            'async': False, 'insecure': False, 'crossmnt': False,
            'security_flavor': {'sys': True, 'kerberos': False,
                                'kerberos_integrity': False, 'kerberos_privacy': False}}
NEW_RULE = {**OLD_RULE, 'root_squash': 'all_admin'}


def api(method, **kwargs):
    args = ['/usr/syno/bin/synowebapi', '--exec',
            'api=SYNO.Core.FileServ.NFS.SharePrivilege', 'version=1', 'method='+method]
    args += [k+'='+json.dumps(v) for k, v in kwargs.items()]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    if not value.get('success'):
        raise RuntimeError('DSM NFS API refused the operation')
    return value.get('data', {})


def paths():
    if ROOT.is_symlink() or not ROOT.is_dir():
        raise RuntimeError('Unexpected share root')
    if {p.name for p in ROOT.iterdir()} - CHILDREN - {'@eaDir'}:
        raise RuntimeError('Unexpected share contents')
    result = [ROOT]
    for name in sorted(CHILDREN):
        child = ROOT/name
        if child.is_symlink() or not child.is_dir():
            raise RuntimeError('Missing or unsafe cache directory')
        result.append(child)
        for parent, dirs, files in os.walk(child, followlinks=False):
            dirs[:] = [name for name in dirs if name != '@eaDir']
            result.extend(Path(parent)/name for name in dirs+files if name != '@eaDir')
    return result


def acl_entries(path):
    result = subprocess.run([ACL, '-get', str(path)], capture_output=True, text=True)
    if "It's Linux mode" in result.stdout+result.stderr:
        return {'entries': [], 'inherit': False}
    if result.returncode != 0:
        raise RuntimeError('Unable to determine ACL state: '+str(path))
    matches = re.findall(r'^\s*\[\d+\] (.+) \(level:(\d+)\)$', result.stdout, re.M)
    inherited = [('owner::allow:rwxpdDaARWcCo:fdi-', '1'),
                 ('user::allow:rwxpdDaARWcCo:----', '1'),
                 ('group:administrators:allow:r-x---a-R-c--:fd--', '1')]
    inherited_file = [('user::allow:rwxpdDaARWcCo:----', '2'),
                      ('group:administrators:allow:r-x---a-R-c--:----', '2')]
    known_inherited = (path.is_dir() and matches == inherited) or (path.is_file() and matches == inherited_file)
    if known_inherited:
        parent = acl_entries(path.parent)
        if path.is_dir() and (parent['inherit'] or not parent['entries']):
            raise RuntimeError('Inherited directory parent is not the known direct policy')
        if path.is_file() and not parent['inherit']:
            raise RuntimeError('Inherited file parent is not the known inherited policy')
        numeric = subprocess.run([ACL, '-getace', str(path)], capture_output=True, text=True, check=True)
        if 'user:10001:allow:' not in numeric.stdout or 'group:101:allow:' not in numeric.stdout:
            raise RuntimeError('Inherited ACL identities differ from the initial policy')
        return {'entries': [], 'inherit': True}
    entries = []
    for entry, level in matches:
        if level != '0':
            raise RuntimeError('Inherited ACL requires separate reviewed migration')
        entries.append(entry.replace('owner::', 'owner:*:'))
    inheritance = 'fd--' if path.is_dir() else '----'
    expected = ['owner:*:allow:rwxpdDaARWcCo:'+inheritance,
                'group:administrators:allow:r-x---a-R-c--:'+inheritance]
    if entries != expected:
        raise RuntimeError('ACL differs from the known initial policy: '+str(path))
    return {'entries': entries, 'inherit': False}


def cli(*args):
    subprocess.run(args, capture_output=True, text=True, check=True)


def other_exports():
    return [line for line in Path('/etc/exports').read_text().splitlines()
            if line.strip() and not line.startswith(str(ROOT)+'\t')
            and not line.startswith(str(ROOT)+' ')]


def restore(checkpoint):
    if (checkpoint.is_symlink() or checkpoint.parent != Path('/var/tmp')
            or not checkpoint.name.startswith('vonk-cache-identity-')
            or checkpoint.stat().st_uid != 0):
        raise RuntimeError('Invalid rollback checkpoint')
    state = json.loads((checkpoint/'before.json').read_text())
    if state.get('root') != str(ROOT):
        raise RuntimeError('Rollback checkpoint belongs to another cache root')
    records = state['paths']
    expected = {r['path'] for r in records}
    if {str(p.relative_to(ROOT)) for p in paths()} != expected:
        raise RuntimeError('Cache tree changed; inspect before metadata rollback')
    other_before = other_exports()
    drift = other_before != state['other_exports']
    if drift:
        (checkpoint/'export-drift.json').write_text(json.dumps({'original': state['other_exports'], 'before_rollback': other_before}, indent=2))
    for index, record in enumerate(records):
        path = ROOT/record['path']
        if path.resolve() != path or (path != ROOT and ROOT not in path.parents):
            raise RuntimeError('Unsafe rollback path')
        current = subprocess.run([ACL, '-get', str(path)], capture_output=True)
        if current.returncode == 0:
            cli(ACL, '-del', str(path))
        os.chown(path, record['uid'], record['gid'])
        os.chmod(path, record['mode'])
        for entry in record['acl']['entries']:
            cli(ACL, '-add', str(path), entry)
        if record['acl']['inherit']:
            cli(ACL, '-enforce-inherit', str(path))
        if acl_entries(path) != record['acl']:
            raise RuntimeError('ACL rollback verification failed: '+str(path))
    api('save', share_name=SHARE, rule=state['rules'])
    if api('load', share_name=SHARE).get('rule') != state['rules']:
        raise RuntimeError('NFS rollback verification failed')
    if other_exports() != other_before:
        raise RuntimeError('Owned metadata and rule restored, but DSM changed unrelated exports during rollback')
    (checkpoint/'rolled-back.json').write_text(json.dumps({'complete': True})+'\n')
    print(json.dumps({'rolled_back': str(checkpoint), 'unrelated_export_drift_observed': drift}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    if os.getuid() != 0:
        raise SystemExit('Run as NAS root')
    os.umask(0o077)
    if args.rollback:
        if args.apply:
            raise SystemExit('Choose apply or rollback')
        restore(args.rollback)
        return
    admin = pwd.getpwnam('admin')
    administrators = grp.getgrnam('administrators')
    if admin.pw_uid == 0 or administrators.gr_gid == 0:
        raise RuntimeError('Unexpected DSM native identity')
    rules = api('load', share_name=SHARE).get('rule')
    if rules != [OLD_RULE]:
        raise RuntimeError('NFS rule differs from the expected pre-migration policy')
    all_paths = paths()
    records = []
    for path in all_paths:
        info = path.lstat()
        if ((info.st_uid, info.st_gid) != (10001, 10001)
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
            raise RuntimeError('Unexpected owner or file type: '+str(path))
        records.append({'path': str(path.relative_to(ROOT)), 'uid': info.st_uid,
                        'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
                        'directory': stat.S_ISDIR(info.st_mode), 'acl': acl_entries(path)})
    print(json.dumps({'apply': args.apply, 'paths': len(records),
                      'owner': 'admin', 'uid': admin.pw_uid,
                      'group': 'administrators', 'gid': administrators.gr_gid,
                      'directory_mode': '02750', 'file_mode': '0640',
                      'rule': NEW_RULE}), flush=True)
    if not args.apply:
        return
    checkpoint = Path('/var/tmp')/('vonk-cache-identity-'+str(time.time_ns()))
    checkpoint.mkdir(mode=0o700)
    state = {'root': str(ROOT), 'paths': records, 'rules': rules, 'other_exports': other_exports()}
    (checkpoint/'before.json').write_text(json.dumps(state, indent=2)+'\n')
    try:
        for record in records:
            path = ROOT/record['path']
            if record['acl']['entries'] or record['acl']['inherit']:
                cli(ACL, '-del', str(path))
            os.chown(path, admin.pw_uid, administrators.gr_gid)
            os.chmod(path, 0o2750 if record['directory'] else 0o640)
        for record in records:
            path = ROOT/record['path']
            info = path.stat()
            expected_mode = 0o2750 if record['directory'] else 0o640
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (admin.pw_uid, administrators.gr_gid, expected_mode):
                raise RuntimeError('Native ownership/mode verification failed: '+str(path))
        api('save', share_name=SHARE, rule=[NEW_RULE])
        if api('load', share_name=SHARE).get('rule') != [NEW_RULE]:
            raise RuntimeError('New NFS policy did not persist')
        if other_exports() != state['other_exports']:
            raise RuntimeError('Unrelated exports changed')
    except BaseException:
        restore(checkpoint)
        raise
    (checkpoint/'applied.json').write_text(json.dumps({'complete': True})+'\n')
    print(json.dumps({'complete': True, 'checkpoint': str(checkpoint),
                      'owner': admin.pw_uid, 'group': administrators.gr_gid,
                      'unrelated_exports_unchanged': True}), flush=True)


if __name__ == '__main__':
    main()
