#!/usr/bin/env python3
"""Run as root on Athena; creates only the reviewed, previously absent share."""
import argparse
import json
import os
from pathlib import Path
import subprocess

NAME = 'vonk-forge-models-pve-sbx'
ROOT = Path('/volume1') / NAME
RULE = {'client': '10.32.21.101', 'privilege': 'rw', 'root_squash': 'guest',
        'async': False, 'insecure': False, 'crossmnt': False,
        'security_flavor': {'sys': True, 'kerberos': False,
                            'kerberos_integrity': False, 'kerberos_privacy': False}}

def api(method, **kwargs):
    args = ['/usr/syno/bin/synowebapi', '--exec',
            'api=SYNO.Core.FileServ.NFS.SharePrivilege', 'version=1', 'method='+method]
    args += [k+'='+json.dumps(v) for k,v in kwargs.items()]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    document = json.loads(result.stdout)
    if not document.get('success'):
        raise RuntimeError(document)
    return document.get('data', {})

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--resume-created', action='store_true', help='Resume only this script’s newly created, unpopulated share')
    args = parser.parse_args()
    if not args.resume_created and (ROOT.exists() or ROOT.is_symlink()):
        raise SystemExit('Share path already exists; inspect instead of overwriting it')
    existing = subprocess.run(['/usr/syno/sbin/synoshare', '--get', NAME],
                              capture_output=True, text=True)
    if existing.returncode == 0 and not args.resume_created:
        raise SystemExit('DSM share already exists; inspect instead of overwriting it')
    exports = Path('/etc/exports').read_text()
    if str(ROOT) in exports:
        raise SystemExit('An export already references this share')
    print(json.dumps({'name': NAME, 'path': str(ROOT), 'nfs_rule': RULE,
                      'numeric_owner': '10001:10001', 'mode': '0750',
                      'access': 'POSIX permissions; root squashed to guest; reserved ports',
                      'applied': args.apply}), flush=True)
    if not args.apply:
        return
    backup = Path('/var/tmp') / (NAME+'-creation')
    if args.resume_created:
        if existing.returncode != 0 or not (backup/'exports.before').is_file():
            raise RuntimeError('No creation checkpoint to resume')
        if (backup/'exports.before').read_text() != exports:
            raise RuntimeError('Exports changed since creation checkpoint')
    else:
        backup.mkdir(mode=0o700, exist_ok=False)
        (backup/'exports.before').write_text(exports)
        subprocess.run(['/usr/syno/sbin/synoshare', '--add', NAME,
                        'Vonk Forge model cache for the pve-sbx Controller',
                        str(ROOT), '', '', '', '0', '0'], check=True)
    # DSM creates its own @eaDir metadata on an otherwise empty shared folder.
    if not ROOT.is_dir() or ROOT.is_symlink() or {p.name for p in ROOT.iterdir()} - {'@eaDir'}:
        raise RuntimeError('Unexpected new share contents; inspect before permissions change')
    share = subprocess.check_output(['/usr/syno/sbin/synoshare', '--get', NAME], text=True)
    (backup/'share.after.txt').write_text(share)
    for required in ('['+NAME+']', '['+str(ROOT)+']', 'fBrowseable [no]', 'RecycleBin....[no]'):
        if required not in share:
            raise RuntimeError('Unexpected DSM share properties; inspect saved receipt')
    if (ROOT/'@eaDir').is_symlink():
        raise RuntimeError('Unexpected symlink in DSM share metadata')
    # Only the newly created empty share. Do not change other shares or global NFS policy.
    subprocess.run(['/usr/syno/bin/synoacltool', '-del', str(ROOT)], check=True)
    os.chown(ROOT, 10001, 10001)
    os.chmod(ROOT, 0o750)
    for name in ('objects', 'partials', 'locks', 'manifests', 'quarantine'):
        child = ROOT/name
        child.mkdir(mode=0o750)
        os.chown(child, 10001, 10001)
        os.chmod(child, 0o750)
    # DSM administrator browsing without granting cache writes or changing NFS identity.
    for path in [ROOT] + [ROOT/name for name in ('objects', 'partials', 'locks', 'manifests', 'quarantine')]:
        for entry in ('owner:*:allow:rwxpdDaARWcCo:fd--',
                      'group:administrators:allow:r-x---a-R-c--:fd--'):
            subprocess.run(['/usr/syno/bin/synoacltool', '-add', str(path), entry], check=True)
    api('save', share_name=NAME, rule=[RULE])
    actual = api('load', share_name=NAME)
    (backup/'receipt.json').write_text(json.dumps({'share': share, 'nfs': actual}, indent=2)+'\n')
    if actual.get('rule') != [RULE]:
        raise RuntimeError('DSM did not retain the exact export rule')
    before = [x for x in exports.splitlines() if x.strip()]
    after = [x for x in Path('/etc/exports').read_text().splitlines()
             if x.strip() and not x.startswith(str(ROOT)+'\t') and not x.startswith(str(ROOT)+' ')]
    if before != after:
        raise RuntimeError('Unrelated export lines changed; inspect before continuing')
    print(json.dumps({'created': NAME, 'rule_verified': True, 'unrelated_exports_unchanged': True}))

if __name__ == '__main__':
    main()
