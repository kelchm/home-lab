#!/usr/bin/env python3
"""Verify the locked upstream inputs, then append the reviewed site patches to a build context."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def patch_digest(paths):
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def stage(kit, mod):
    for directory in (kit / 'patches', kit / 'patches/engine', kit / 'templates', mod / 'patches'):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f'Expected an ordinary input directory: {directory}')
    lock = json.loads((mod / 'upstream.lock.json').read_text())
    upstream = sorted((kit / 'patches/engine').glob('*.patch'))
    site = sorted((mod / 'patches').glob('*.patch'))
    for label, paths, count, digest in (
        ('kit', upstream, lock['kit_patch_count'], lock['kit_patches_sha256']),
        ('site', site, lock['site_patch_count'], lock['site_patches_sha256']),
    ):
        if any(p.is_symlink() or not p.is_file() for p in paths):
            raise ValueError(f'{label}: linked or non-file patch')
        if len(paths) != count or patch_digest(paths) != digest:
            raise ValueError(f'{label}: patch count or content differs from the lock')
    if (kit / 'templates/glm-5.3-flash-vision.jinja').read_bytes() != (mod / 'chat_template.jinja').read_bytes():
        raise ValueError('Kit template differs from the reviewed template')
    for patch in site:
        target = kit / 'patches/engine' / patch.name
        if target.exists() or target.is_symlink():
            raise ValueError(f'Site patch collides with kit input: {patch.name}')
    for patch in site:
        shutil.copyfile(patch, kit / 'patches/engine' / patch.name)
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    parser.add_argument('mod', type=Path)
    args = parser.parse_args()
    try:
        lock = stage(args.kit, args.mod)
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit(str(error)) from error
    print(f"Verified {lock['kit_patch_count']} upstream and {lock['site_patch_count']} site patches")


if __name__ == '__main__':
    main()
