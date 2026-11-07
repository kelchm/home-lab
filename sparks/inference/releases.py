#!/usr/bin/env python3
"""Pin or materialize immutable spark-inference release inputs; never operate a host."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request

REPOSITORY = 'kelchm/spark-inference'
MARKER = '.spark-inference-release.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def relative(value):
    if not isinstance(value, str):
        raise ValueError('Unsafe release path type')
    p = PurePosixPath(value)
    if not value or not p.parts or p.is_absolute() or '..' in p.parts or '.' in p.parts or '\\' in value or value != p.as_posix():
        raise ValueError('Unsafe release path: ' + str(value))
    return p


def validate(lock):
    if lock.get('schema') != 'spark-inference-deployment/v1' or lock.get('repository') != REPOSITORY:
        raise ValueError('Unsupported release source')
    if not re.fullmatch(r'[0-9a-f]{40}', lock.get('source_commit', '')):
        raise ValueError('Release source must be an immutable full commit')
    if not re.fullmatch(r'ghcr\.io/kelchm/spark-inference/[\w.-]+@sha256:[0-9a-f]{64}', lock.get('image', '')):
        raise ValueError('Image must be pinned by digest')
    if not isinstance(lock.get('files'), dict) or not lock['files']:
        raise ValueError('Missing release input inventory')
    for name, sha in lock['files'].items():
        relative(name)
        if name == MARKER or not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise ValueError('Invalid release input identity')
    for key in ('release_path', 'profile_path'):
        if lock[key] not in lock['files']:
            raise ValueError('Required release/profile file not pinned')
    if not set(lock.get('executables', [])) <= set(lock['files']):
        raise ValueError('Unpinned executable')
    return lock


def source_file(root, name):
    p = Path(root)
    for part in relative(name).parts:
        p = p / part
        if p.is_symlink():
            raise ValueError('Linked release input: ' + name)
    return p


def check_release(lock, read):
    release = json.loads(read(lock['release_path']))
    if release['release'] != lock['release'] or release['image'] != lock['image']:
        raise ValueError('Pinned release and deployment image differ')
    if release['engine']['commit'] != lock['engine_commit']:
        raise ValueError('Pinned engine differs')
    profile = read(lock['profile_path']).decode()
    images = re.findall(r'^container:\s*(.+)$', profile, re.M)
    if len(images) != 1:
        raise ValueError('Expected one profile container image')
    image = json.loads(images[0]) if images[0].startswith('"') else images[0].strip("'")
    if image != lock['image']:
        raise ValueError('Profile image differs from its pinned release')
    return release


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)


def pin(source, release_name):
    if not re.fullmatch(r'glm53-mia-\d{4}\.\d{2}\.\d{2}(?:\.\d+)?', release_name):
        raise ValueError('Invalid release name')
    commit = git(source, 'rev-parse', 'HEAD').decode().strip()
    release_path = f'recipes/glm53-mia/releases/{release_name}.json'
    release = json.loads(git(source, 'show', f'{commit}:{release_path}'))
    profile = f'recipes/glm53-mia/sparkrun/profiles/{release_name}-agent-1m.yaml'
    paths = [release_path, 'recipes/glm53-mia/sparkrun', 'plugins/tensorfold', 'qualification', 'AGENTS.md', 'LICENSE', 'NOTICE']
    entries = git(source, 'ls-tree', '-r', '-z', commit, '--', *paths).split(b'\0')
    files, executables = {}, []
    for entry in entries:
        if not entry:
            continue
        meta, name = entry.split(b'\t', 1)
        mode, kind, blob = meta.decode().split()
        name = name.decode()
        relative(name)
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError('Unsupported release input mode: ' + name)
        files[name] = digest(git(source, 'cat-file', 'blob', blob))
        if mode == '100755':
            executables.append(name)
    lock = {'schema': 'spark-inference-deployment/v1', 'repository': REPOSITORY,
            'source_commit': commit, 'release': release_name, 'release_path': release_path,
            'profile_path': profile, 'image': release['image'], 'engine_commit': release['engine']['commit'],
            'files': dict(sorted(files.items())), 'executables': sorted(executables),
            'operating_status': 'qualification-only; requires an approved window'}
    validate(lock)
    check_release(lock, lambda name: git(source, 'show', f'{commit}:{name}'))
    return lock


def check(lock, root):
    validate(lock)
    for name, sha in lock['files'].items():
        path = source_file(root, name)
        if digest(path.read_bytes()) != sha:
            raise ValueError('Release input differs: ' + name)
        executable = bool(path.stat().st_mode & 0o111)
        if executable != (name in lock.get('executables', [])):
            raise ValueError('Release executable mode differs: ' + name)
    check_release(lock, lambda name: source_file(root, name).read_bytes())
    return Path(root) / lock['profile_path']


def materialize(lock, output, source=None):
    validate(lock)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('Output must be a new directory')
    # Validate every file before publishing any part of the output. Sources are immutable GitHub raw URLs,
    # or a supplied local tree for offline preparation/tests. No SSH, Docker or endpoint operation exists here.
    with tempfile.TemporaryDirectory(prefix='spark-inference-release-') as tmp:
        staged = Path(tmp)
        for name, sha in lock['files'].items():
            if source is not None:
                data = source_file(source, name).read_bytes()
            else:
                url = f'https://raw.githubusercontent.com/{REPOSITORY}/{lock["source_commit"]}/' + urllib.parse.quote(name, safe='/')
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = response.read(16 * 1024 * 1024 + 1)
                if len(data) > 16 * 1024 * 1024:
                    raise ValueError('Release input exceeds the download bound')
            if digest(data) != sha:
                raise ValueError('Release input hash mismatch: ' + name)
            dest = staged / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(0o755 if name in lock.get('executables', []) else 0o644)
        check(lock, staged)
        output.mkdir(parents=True, exist_ok=False)
        for name in lock['files']:
            dest = output / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('xb') as handle:
                handle.write((staged / name).read_bytes())
            dest.chmod(0o755 if name in lock.get('executables', []) else 0o644)
        with (output / MARKER).open('xb') as handle:
            handle.write(encoded(lock))
    return check(lock, output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('pin')
    q.add_argument('--source', type=Path, required=True)
    q.add_argument('--release', required=True)
    q.add_argument('--output', type=Path, required=True)
    for name in ('materialize', 'check'):
        q = sub.add_parser(name)
        q.add_argument('--lock', type=Path, required=True)
        q.add_argument('--directory', type=Path, required=True)
        if name == 'materialize':
            q.add_argument('--source-tree', type=Path, help='Use local inputs without network access')
    args = p.parse_args()
    if args.command == 'pin':
        data = pin(args.source, args.release)
        with args.output.open('xb') as handle:
            handle.write(encoded(data))
        print(args.output)
    else:
        lock = json.loads(args.lock.read_text())
        print(materialize(lock, args.directory, args.source_tree) if args.command == 'materialize' else check(lock, args.directory))


if __name__ == '__main__':
    main()
