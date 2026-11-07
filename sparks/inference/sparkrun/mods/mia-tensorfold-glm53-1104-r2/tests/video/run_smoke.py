#!/usr/bin/env python3
"""Exercise an exported portable libtfvideo.so in an off-host ARM64 compiler container.

Only the decoder and a small C driver execute. Do not supply or execute the Spark
serving binary. Fixtures come from make_fixtures.py and are hash-checked first.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--toolchain-image', required=True, help='off-host ARM64 image containing gcc')
    parser.add_argument('--docker-context', default='colima-tf-build')
    args = parser.parse_args()
    manifest = json.loads((args.fixtures / 'manifest.json').read_text())
    expected = {'h264.mp4', 'hevc.mp4', 'vp8.webm', 'vp9.webm', 'mpeg4.mp4'}
    if manifest.get('schema') != 'glm-video-decoder-smoke/v1' or set(manifest['files']) != expected:
        parser.error('Expected the complete generated five-codec fixture set')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((args.fixtures / name).read_bytes()).hexdigest() != digest:
            parser.error(f'Fixture hash mismatch: {name}')
    docker = ['docker', '--context', args.docker_context]
    image = subprocess.check_output([*docker, 'image', 'inspect', '--format', '{{.Id}}', args.toolchain_image], text=True).strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        parser.error('Invalid compiler image ID')
    library = args.library.read_bytes()
    if not library or len(library) > 128 << 20:
        parser.error('Expected a bounded exported portable decoder library')
    print(json.dumps({'compiler_image': image, 'library_sha256': hashlib.sha256(library).hexdigest(),
                      'fixtures': manifest}, sort_keys=True), flush=True)
    script = ('mkdir /probe && tar -xf - -C /probe && '
              'gcc -O2 -o /probe/decoder_smoke /probe/decoder_smoke.c -L/probe -Wl,-rpath,/probe -ltfvideo && '
              '/probe/decoder_smoke ' + ' '.join('/probe/' + name for name in sorted(expected)))
    name = 'glm1104-video-smoke-' + uuid.uuid4().hex
    command = [*docker, 'run', '--rm', '--name', name, '-i', '--network', 'none', '--entrypoint', 'bash', image, '-c', script]
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w') as archive:
        entry = tarfile.TarInfo('libtfvideo.so')
        entry.size = len(library)
        entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(library))
        archive.add(Path(__file__).parent / 'decoder_smoke.c', arcname='decoder_smoke.c')
        for filename in sorted(expected):
            archive.add(args.fixtures / filename, arcname=filename)
    try:
        with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
            try:
                process.communicate(input=payload.getvalue(), timeout=120)
                code = process.returncode
            except BaseException:
                process.kill()
                process.wait()
                raise
            if code:
                raise SystemExit(code)
    finally:
        # This name belongs only to this invocation, including on timeout or a broken Docker client.
        subprocess.run([*docker, 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


if __name__ == '__main__':
    main()
