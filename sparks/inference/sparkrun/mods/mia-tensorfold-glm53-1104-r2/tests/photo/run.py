#!/usr/bin/env python3
"""Copy integrated source to scratch, apply 065, recheck Pillow pixels, and require real pinned codecs.

No Git checkout, network, model host, GPU, branch, commit, or push. Missing codecs are failures.
An optional Docker run executes a generic Linux CPU test binary against packaged runtime codecs.
"""
from __future__ import annotations
import argparse
import base64
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from make_fixtures import reference, require_reference

HERE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv: list[str], cwd: Path, *, env=None, stdin=None, expect_failure=False) -> str:
    print('+ ' + ' '.join(argv), flush=True)
    proc = subprocess.run(argv, cwd=cwd, env=env, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = proc.stdout.decode(errors='replace')
    if expect_failure:
        print(f'expected failure: exit={proc.returncode}; full output retained in scratch', flush=True)
    else:
        print(text, end='', flush=True)
    if expect_failure:
        if proc.returncode == 0:
            raise RuntimeError('missing codec unexpectedly passed')
    elif proc.returncode:
        raise RuntimeError(f'command failed with status {proc.returncode}')
    return text


def require_pass(text: str) -> int:
    match = re.search(r'All (\d+) tests passed\.', text)
    # The dedicated test reports "zero skips"; any actual skipped test or old loader skip is a failure.
    if not match or re.search(r'\b[1-9]\d* skipped\b|pixel cases skipped', text):
        raise RuntimeError('suite did not pass completely')
    if '49 complete pixel arrays, 10 refusals, zero skips' not in text:
        raise RuntimeError('dedicated actual-pixel test did not run completely')
    return int(match[1])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine', required=True, type=Path, help='integrated source before 065; read-only')
    p.add_argument('--zig', default='zig')
    p.add_argument('--jpeg', required=True, type=Path, help='loadable libturbojpeg 3.1.4.1')
    p.add_argument('--webp', required=True, type=Path, help='loadable libwebpdecoder 1.6.0')
    p.add_argument('--receipt', type=Path, help='write PASS receipt only after all requested execution passes')
    p.add_argument('--docker-context')
    p.add_argument('--docker-image', help='optional existing Linux runtime image, preferably immutable ID/digest')
    p.add_argument('--linux-target', default='aarch64-linux-gnu')
    args = p.parse_args()
    require_reference()
    version = subprocess.check_output([args.zig, 'version'], text=True).strip()
    if version != '0.17.0':
        p.error(f'expected Zig 0.17.0, found {version}')
    engine = args.engine.resolve(strict=True)
    jpeg, webp = args.jpeg.resolve(strict=True), args.webp.resolve(strict=True)
    fixtures = json.loads((HERE / 'cases.json').read_text())
    for case in fixtures['cases']:
        data = base64.b64decode(case['encoded_b64'], validate=True)
        assert hashlib.sha256(data).hexdigest() == case['encoded_sha256'], case['name']
        actual = reference(data)
        expected = {k: v for k, v in case.items() if k in actual}
        assert actual == expected, f"Pillow reference changed: {case['name']}"
    print(f"Pillow revalidated {len(fixtures['cases'])} cases", flush=True)
    manifest = json.loads((HERE / 'source-manifest.json').read_text())
    patch = HERE.parent.parent / 'patches' / manifest['patch']
    assert sha(patch) == manifest['patch_sha256'], 'patch changed without updating manifest'
    assert sha(HERE / 'cases.json') == manifest['fixtures_sha256'], 'fixture manifest changed'
    for row in manifest['unchanged_decoder_files']:
        assert sha(engine / row['path']) == row['sha256'], f"decoder dependency changed: {row['path']}"
    for row in manifest['files']:
        assert sha(engine / row['path']) == row['before_sha256'], f"pre-065 source mismatch: {row['path']}"
    scratch = Path(tempfile.mkdtemp(prefix='glm065-photo-test-'))
    print(f'scratch={scratch}', flush=True)
    for name in ('zig', 'tools', 'build.zig', 'build.zig.zon', '.zig-version'):
        src = engine / name
        shutil.copytree(src, scratch / name) if src.is_dir() else shutil.copy2(src, scratch / name)
    run(['git', 'apply', '--check', str(patch)], scratch)
    run(['git', 'apply', str(patch)], scratch)
    for row in manifest['files']:
        assert sha(scratch / row['path']) == row['after_sha256'], row['path']
    shutil.copy2(HERE / 'native_photo_test.zig', scratch / 'zig/src/photo_parity_test.zig')
    shutil.copy2(HERE / 'cases.json', scratch / 'photo-cases.json')
    env = dict(os.environ, TENSORFOLD_LIBTURBOJPEG=str(jpeg), TENSORFOLD_LIBWEBP=str(webp))
    command = [args.zig, 'test', '-lc', '--dep', 'sys', '-Mroot=zig/src/photo_parity_test.zig', '-Msys=zig/src/platform/sys.zig']
    log = run(command + ['--test-no-exec', '-femit-bin=photo-tests'], scratch, env=env)
    text = run([str(scratch / 'photo-tests')], scratch, env=env)
    (scratch / 'native.log').write_text(log + text)
    tests = require_pass(text)
    missing = run([str(scratch / 'photo-tests')], scratch,
                  env=dict(env, TENSORFOLD_LIBTURBOJPEG=str(scratch / 'absent-codec')), expect_failure=True)
    (scratch / 'missing-codec.log').write_text(missing)
    if 'NoJpegLibrary' not in missing:
        raise RuntimeError('negative control did not fail for the expected missing codec')
    linux = None
    if args.docker_image:
        command = [args.zig, 'test', '-target', args.linux_target, '-mcpu', 'generic', '-lc', '--dep', 'sys',
                   '-Mroot=zig/src/photo_parity_test.zig', '-Msys=zig/src/platform/sys.zig',
                   '--test-no-exec', '-femit-bin=photo-linux-tests']
        run(command, scratch)
        # No shared host directories or GPU device: send only the CPU test and fixture files to the container.
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            for name in ('photo-linux-tests', 'photo-cases.json', 'zig/tests/fixtures'):
                tar.add(scratch / name, arcname=name)
        docker = ['docker'] + (['--context', args.docker_context] if args.docker_context else [])
        image_id = subprocess.check_output(docker + ['image', 'inspect', '--format={{.Id}}', args.docker_image], text=True).strip()
        shell = ('set -eu; mkdir /tmp/photo; tar -xf - -C /tmp/photo; cd /tmp/photo; '
                 'export TENSORFOLD_LIBTURBOJPEG=/opt/tensorfold/native/lib/libturbojpeg.so.0; '
                 'export TENSORFOLD_LIBWEBP=/opt/tensorfold/native/lib/libwebpdecoder.so.3; '
                 'sha256sum "$TENSORFOLD_LIBTURBOJPEG" "$TENSORFOLD_LIBWEBP"; ./photo-linux-tests')
        text = run(docker + ['run', '--rm', '-i', '--network', 'none', '--entrypoint', '/bin/sh', image_id, '-c', shell],
                   scratch, stdin=archive.getvalue())
        (scratch / 'linux.log').write_text(text)
        linux = dict(target=args.linux_target, cpu='generic', image=image_id, tests_passed=require_pass(text),
                     executable_sha256=sha(scratch / 'photo-linux-tests'), output=text)
    receipt = dict(status='PASS', timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   platform=platform.platform(), machine=platform.machine(), python=platform.python_version(),
                   zig=version, zig_sha256=sha(Path(shutil.which(args.zig) or args.zig)), reference=fixtures['reference'], fixtures_sha256=sha(HERE / 'cases.json'),
                   patch_sha256=sha(patch), source_files=manifest['files'],
                   unchanged_decoder_files=manifest['unchanged_decoder_files'],
                   test_sources={n: sha(HERE / n) for n in ('run.py', 'native_photo_test.zig', 'make_fixtures.py')},
                   native=dict(tests_passed=tests, jpeg=dict(path=str(jpeg), sha256=sha(jpeg)),
                               webp=dict(path=str(webp), sha256=sha(webp)), executable_sha256=sha(scratch / 'photo-tests')),
                   linux=linux, pixel_arrays_matched=49, refusals_matched=10, skipped=0,
                   missing_codec_negative_control='failed with NoJpegLibrary as required', scratch=str(scratch))
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'PASS: {tests} native tests; actual decoded pixels verified; scratch={scratch}', flush=True)


if __name__ == '__main__':
    main()
