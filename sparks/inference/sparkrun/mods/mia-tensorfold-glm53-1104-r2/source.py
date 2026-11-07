#!/usr/bin/env python3
"""Prepare the exact patched engine for off-host CPU tests without modifying a source checkout.

The supplied repository must already contain the locked engine commit. Only its Git archive is
used; its working tree and untracked files are ignored. No network, GPU or Spark is accessed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from inputs import stage

HERE = Path(__file__).resolve().parent
SOURCE_PARTS = ('zig', 'tools', 'build.zig', 'build.zig.zon', '.zig-version', 'README.md', 'CHANGELOG.md', 'RUNBOOK.md')


def source_files(root):
    paths = []
    for part in SOURCE_PARTS:
        path = root / part
        if path.is_symlink():
            raise ValueError(f'Linked native source: {path}')
        if path.is_dir():
            children = list(path.rglob('*'))
            if any(child.is_symlink() for child in children):
                raise ValueError(f'Linked native source under {path}')
            paths.extend(child for child in children if child.is_file())
        elif path.is_file():
            paths.append(path)
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def verify_source(root, lock):
    identity = json.loads((root / 'HOME-LAB-SOURCES.json').read_text())
    for key in ('engine_repository', 'engine_revision', 'kit_repository', 'kit_revision',
                'kit_patch_count', 'kit_patches_sha256', 'site_patch_count', 'site_patches_sha256'):
        if identity.get(key) != lock[key]:
            raise ValueError(f'Prepared source disagrees with locked {key}')
    if not identity.get('source_files') or identity['source_files'] != source_files(root):
        raise ValueError('Native source files differ from the prepared source receipt')
    return identity


def prepare(repository, kit, output):
    lock = json.loads((HERE / 'upstream.lock.json').read_text())
    subprocess.run(['git', '-C', str(repository), 'cat-file', '-e', lock['engine_revision'] + '^{commit}'], check=True)
    # Refuse an existing destination: no source, worktree or prior test artifacts are overwritten.
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='glm1104-source-') as work:
        context = Path(work) / 'kit'
        shutil.copytree(kit, context, symlinks=True)
        stage(context, HERE)
        with tempfile.TemporaryFile() as archive:
            subprocess.run(['git', '-C', str(repository), 'archive', lock['engine_revision']], stdout=archive, check=True)
            archive.seek(0)
            with tarfile.open(fileobj=archive) as source:
                source.extractall(output, filter='data')
        patches = sorted((context / 'patches/engine').glob('*.patch'))
        for patch in patches:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=output, check=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=output, check=True)
    identity = {key: lock[key] for key in ('engine_repository', 'engine_revision', 'kit_repository',
                'kit_revision', 'kit_patch_count', 'kit_patches_sha256', 'site_patch_count', 'site_patches_sha256')}
    identity['source_files'] = source_files(output)
    (output / 'HOME-LAB-SOURCES.json').write_text(json.dumps(identity, indent=2) + '\n')
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, type=Path)
    parser.add_argument('--kit', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    subprocess.run([sys.executable, str(HERE / 'prepare.py'), '--check'], check=True)
    prepare(args.repository.resolve(), args.kit.resolve(), args.output.resolve())
    print(f'Prepared locked native source in {args.output}')


if __name__ == '__main__':
    main()
