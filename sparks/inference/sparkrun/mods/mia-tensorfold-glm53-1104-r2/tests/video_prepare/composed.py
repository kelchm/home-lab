#!/usr/bin/env python3
"""Require real portable video decoding on the final immutable composed source.

Executes a generic Linux CPU test binary, never the inference binary. The library
must be exported from the selected candidate image; no GPU or Spark is accessed.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MOD = HERE.parents[1]
sys.path.insert(0, str(MOD))
import source as native_source


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--zig', required=True, type=Path)
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--image', required=True, help='immutable candidate daemon ID')
    parser.add_argument('--long-clip', required=True, type=Path)
    parser.add_argument('--docker-context', default='colima-tf-build')
    parser.add_argument('--work-dir', type=Path)
    args = parser.parse_args()
    if not args.image.startswith('sha256:') or len(args.image) != 71:
        parser.error('--image must be the verified immutable candidate daemon ID')
    if subprocess.check_output([str(args.zig), 'version'], text=True).strip() != '0.17.0':
        parser.error('Zig 0.17.0 is required')
    subprocess.run([sys.executable, str(MOD / 'prepare.py'), '--check'], check=True)
    lock = json.loads((MOD / 'upstream.lock.json').read_text())
    identity = native_source.verify_source(args.source, lock)
    runner = module('video_fixture_runner', HERE / 'run.py')
    manifest = json.loads((HERE / 'source-manifest.json').read_text())
    if runner.sha(args.long_clip) != manifest['long_fixture']['sha256']:
        raise ValueError('The long fixture differs from the recorded generator output')
    if runner.sha(args.library) != manifest['library_sha256']:
        raise ValueError('The portable library differs from the reviewed decoder')
    work = args.work_dir or Path(tempfile.mkdtemp(prefix='glm-video-composed-'))
    work.mkdir(exist_ok=True)
    scratch = work / 'source'
    runner.copy_source(args.source, scratch)
    host = module('composed_host_fixtures', MOD / 'tests/run_host.py')
    host.install_tests(scratch)
    server = scratch / 'zig/src/server'
    # The generic host gate permits absent FFmpeg. This required library gate does not.
    guard = '    if (api.tfvideo.version() == null) return error.SkipZigTest;\n'
    for filename in ('video_prepare_core_test.zig', 'video_prepare_http_test.zig'):
        path = server / filename
        text = path.read_text()
        path.write_text(text.replace(guard, ''))
    shutil.copy2(args.long_clip, server / 'video-fixtures/long.mp4')
    with (server / 'video_prepare_core_test.zig').open('a') as output:
        output.write((HERE / 'long_case.zig').read_text())
    build = scratch / 'zig/build/cuda.zig'
    text = build.read_text()
    needle = '    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);'
    if text.count(needle) != 1 or text.count('    const host = b.graph.host;') != 1:
        raise ValueError('The native portable test build graph changed')
    addition = '''
    const real_video = b.addTest(.{ .name = "video-prepare-real", .root_module = server_tests, .filters = &.{"video prepare real:"} });
    b.step("compile-composed-video", "Required real decoder and HTTP tests").dependOn(&b.addInstallArtifact(real_video, .{}).step);
'''
    text = text.replace(needle, needle + addition)
    text = text.replace('    const host = b.graph.host;', '    const host = b.resolveTargetQuery(.{ .cpu_arch = .aarch64, .os_tag = .linux, .abi = .gnu, .cpu_model = .baseline });', 1)
    build.write_text(text)
    zig = str(args.zig.resolve())
    compile_check = runner.command([zig, 'build', 'compile-composed-video', '-j3', '--summary', 'all'], scratch, work / 'compile.log')
    check = runner.docker_run(scratch / 'zig-out/bin/video-prepare-real', args.library,
                              args.docker_context, args.image, work / 'portable.log')
    output = (work / 'portable.log').read_text()
    if 'All 32 tests passed.' not in output or 'SKIP' in output:
        raise RuntimeError('The complete 32-test real-library gate did not run')
    native_source.verify_source(args.source, lock)
    receipt = {'schema': 'glm-composed-video/v1', 'source_identity': identity,
               'image': args.image, 'library_sha256': runner.sha(args.library),
               'long_clip_sha256': runner.sha(args.long_clip), 'tests_passed': 32,
               'skipped': 0, 'checks': [compile_check, check], 'gpu_model_executed': False}
    (work / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('PASS: ' + str(work / 'receipt.json'))


if __name__ == '__main__':
    main()
