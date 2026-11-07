#!/usr/bin/env python3
"""Reproduce typed media refusals and their negative control in a disposable source copy."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--zig', required=True)
    parser.add_argument('--cpu', default='apple_m1')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    mod = here.parents[1]
    manifest = json.loads((here / 'source-manifest.json').read_text())
    source = args.source.resolve(strict=True)
    sys.path.insert(0, str(mod))
    import source as native_source
    marker = source / 'HOME-LAB-SOURCES.json'
    if digest(marker) != manifest['marker_sha256']:
        raise ValueError('Expected the documented 13-patch source marker')
    identity = json.loads(marker.read_text())
    if native_source.source_files(source) != identity['source_files']:
        raise ValueError('Prepared native source changed')
    patch = mod / 'patches' / manifest['patch']
    if digest(patch) != manifest['patch_sha256']:
        raise ValueError('Patch changed')
    for name, expected in manifest['test_inputs'].items():
        if digest(mod / name) != expected:
            raise ValueError(f'Test input changed: {name}')
    work = Path(tempfile.mkdtemp(prefix='glm076-media-errors-'))
    for name in native_source.SOURCE_PARTS:
        path = source / name
        if path.is_dir():
            shutil.copytree(path, work / name)
        else:
            shutil.copy2(path, work / name)
    print(f'scratch={work}', flush=True)

    def run(command, name, negative=False):
        with (work / name).open('w') as log:
            process = subprocess.Popen(command, cwd=work, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True, env={**os.environ, 'TF_GLM_CLEAR_THINKING': '0'})
            try:
                code = process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        if (code == 0) == negative:
            raise RuntimeError(f'Unexpected exit {code}: {work / name}')
        return {'exit_code': code, 'log': name, 'sha256': digest(work / name)}

    run(['git', 'apply', '--check', str(patch)], 'apply-check.log')
    run(['git', 'apply', str(patch)], 'apply.log')
    for item in manifest['files']:
        if digest(work / item['path']) != item['after_sha256']:
            raise ValueError(f'Patched native file differs: {item["path"]}')
    spec = importlib.util.spec_from_file_location('media_error_host', here / 'host_fixtures.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    host.install_tests(work)
    command = [args.zig, 'build', 'test-native-media-errors', 'compile-glm-native-open',
               f'-Dcpu={args.cpu}', '-j3', '--summary', 'all']
    passed = run(command, 'focused.log')
    path = work / 'zig/src/server/chat.zig'
    original = path.read_text()
    needle = '.refused => return cx.refuse('
    if original.count(needle) != 1:
        raise ValueError('Negative-control insertion point changed')
    try:
        path.write_text(original.replace(needle, '.refused => return cx.other('))
        negative = run(command, 'negative.log', negative=True)
        text = (work / 'negative.log').read_text()
        if ('media refusal path=/v1/chat/completions stream=false wire=HTTP/1.1 500' not in text
                or 'compilation errors' in text):
            raise ValueError('Negative control failed for a different reason')
    finally:
        path.write_text(original)
    if native_source.source_files(source) != identity['source_files']:
        raise ValueError('Read-only source changed')
    (work / 'receipt.json').write_text(json.dumps({'focused': passed, 'negative': negative,
        'patch_sha256': digest(patch), 'gpu_executed': False}, indent=2) + '\n')
    print(f'PASS: typed refusal and intentional HTTP500 control; receipt={work / "receipt.json"}')


if __name__ == '__main__':
    main()
