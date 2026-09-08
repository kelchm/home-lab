#!/usr/bin/env python3
"""Restore administrator browsing on the existing Vonk cache; run as NAS root."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import time

ROOT = Path('/volume1/vonk-forge-models-pve-sbx')
ACL = '/usr/syno/bin/synoacltool'
CHILDREN = {'objects', 'partials', 'locks', 'manifests', 'quarantine'}


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.getuid() != 0:
        raise SystemExit('Run as NAS root')
    if {p.name for p in ROOT.iterdir()} - CHILDREN - {'@eaDir'}:
        raise RuntimeError('Unexpected share contents')
    paths = [ROOT]
    for name in sorted(CHILDREN):
        child = ROOT/name
        if not child.is_dir() or child.is_symlink():
            raise RuntimeError('Missing or unsafe cache directory')
        paths.append(child)
        for parent, dirs, files in os.walk(child, followlinks=False):
            dirs[:] = [d for d in dirs if d != '@eaDir']
            paths.extend(Path(parent)/name for name in dirs+files if name != '@eaDir')
    records = []
    for path in paths:
        info = path.lstat()
        if (info.st_uid, info.st_gid) != (10001, 10001) or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        ):
            raise RuntimeError('Unexpected ownership or file type: '+str(path))
        # Refuse overwriting an existing policy. The correction is deliberately one-shot.
        acl = subprocess.run([ACL, '-get', str(path)], capture_output=True, text=True)
        if acl.returncode == 0 or "It's Linux mode" not in acl.stderr+acl.stdout:
            raise RuntimeError('Existing or unknown ACL policy: '+str(path))
        records.append({'path': str(path), 'mode': stat.S_IMODE(info.st_mode),
                        'directory': stat.S_ISDIR(info.st_mode)})
    print(json.dumps({'paths': len(records), 'applied': args.apply,
                      'owner': 'unchanged 10001:10001',
                      'administrators': 'read and traverse; no write'}), flush=True)
    if not args.apply:
        return
    checkpoint = Path('/var/tmp')/('vonk-dsm-visibility-'+str(time.time_ns()))
    checkpoint.mkdir(mode=0o700)
    (checkpoint/'before.json').write_text(json.dumps(records, indent=2)+'\n')
    exports = Path('/etc/exports').read_bytes()
    (checkpoint/'exports.before').write_bytes(exports)
    # Children first: parents must not make a partly converted tree browseable.
    changed = []
    for record in reversed(records):
        path = record['path']
        inheritance = 'fd--' if record['directory'] else '----'
        run(ACL, '-add', path, 'owner:*:allow:rwxpdDaARWcCo:'+inheritance)
        changed.append(path)
        (checkpoint/'changed.json').write_text(json.dumps(changed)+'\n')
        run(ACL, '-add', path, 'group:administrators:allow:r-x---a-R-c--:'+inheritance)
        assert Path(path).stat().st_uid == 10001
    assert Path('/etc/exports').read_bytes() == exports
    print(json.dumps({'complete': True, 'checkpoint': str(checkpoint),
                      'paths': len(changed), 'exports_unchanged': True}), flush=True)


if __name__ == '__main__':
    main()
