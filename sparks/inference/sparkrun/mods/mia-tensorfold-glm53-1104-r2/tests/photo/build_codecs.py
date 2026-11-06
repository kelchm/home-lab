#!/usr/bin/env python3
"""Build only pinned CPU codec shared libraries from hash-verified archives into a new scratch directory."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request

SOURCES = {
    'jpeg': dict(version='3.1.4.1', url='https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.1.4.1/libjpeg-turbo-3.1.4.1.tar.gz',
                 sha256='ecae8008e2cc9ade2f2c1bb9d5e6d4fb73e7c433866a056bd82980741571a022', directory='libjpeg-turbo-3.1.4.1'),
    'webp': dict(version='1.6.0', url='https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.6.0.tar.gz',
                 sha256='e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564', directory='libwebp-1.6.0'),
    'pillow_fixture': dict(version='13.0.0', url='https://files.pythonhosted.org/packages/ce/a4/9cc30690a394a8305e8be4c9036aa16d0dfddadd09079860b582f562496d/pillow-13.0.0.tar.gz',
                           sha256='cdcd1ba258178f7356d74170c9a81ba4eab6a5d55748bc402a5a2a6fe6be3510', directory='pillow-13.0.0'),
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True, type=Path, help='new scratch directory (must not exist)')
    p.add_argument('--jobs', type=int, default=min(8, os.cpu_count() or 1))
    p.add_argument('--fixture-source', action='store_true', help='also fetch pinned Pillow source for independent YCCK input')
    args = p.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    commands = []
    artifacts = {}

    def run(command: list[str]) -> None:
        commands.append(command)
        print('+ ' + ' '.join(command), flush=True)
        subprocess.run(command, check=True)

    for name in ('jpeg', 'webp') + (('pillow_fixture',) if args.fixture_source else ()):
        spec = SOURCES[name]
        archive = out / (name + '.tar.gz')
        urllib.request.urlretrieve(spec['url'], archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != spec['sha256']:
            raise RuntimeError(f'{name} source hash mismatch: {digest}')
        with tarfile.open(archive) as tar:
            tar.extractall(out / 'source' / name, filter='data')
        source = out / 'source' / name / spec['directory']
        if name == 'pillow_fixture':
            continue
        build = out / (name + '-build')
        flags = (['-DENABLE_SHARED=ON', '-DENABLE_STATIC=OFF', '-DWITH_TURBOJPEG=ON', '-DWITH_JAVA=OFF', '-DWITH_TOOLS=OFF', '-DWITH_TESTS=OFF']
                 if name == 'jpeg' else ['-DBUILD_SHARED_LIBS=ON'] + [f'-DWEBP_BUILD_{v}=OFF' for v in
                    ('ANIM_UTILS', 'CWEBP', 'DWEBP', 'GIF2WEBP', 'IMG2WEBP', 'VWEBP', 'WEBPINFO', 'WEBPMUX', 'EXTRAS', 'LIBWEBPMUX')])
        run(['cmake', '-S', str(source), '-B', str(build), '-DCMAKE_BUILD_TYPE=Release', *flags])
        run(['cmake', '--build', str(build), '-j', str(args.jobs), '--target', 'turbojpeg' if name == 'jpeg' else 'webpdecoder'])
        for path in sorted(build.glob('**/*')):
            if path.is_file() and not path.is_symlink() and (path.name.startswith('libturbojpeg.') or path.name.startswith('libwebpdecoder.')) and path.suffix in ('.dylib', '.so', '.0', '.3'):
                artifacts[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / 'build-receipt.json').write_text(json.dumps(dict(sources=SOURCES, commands=commands, artifacts=artifacts), indent=2) + '\n')
    print(f'CPU codecs built; receipt={out / "build-receipt.json"}')


if __name__ == '__main__':
    main()
