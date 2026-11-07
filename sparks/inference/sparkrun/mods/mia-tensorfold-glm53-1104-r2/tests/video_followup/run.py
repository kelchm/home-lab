#!/usr/bin/env python3
"""Reproduce warm-canvas reuse and edit-list compatibility without GPUs or a model."""
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


def negative_controls(runner, args, portable, work):
    results = []
    for name in ('no-warm-reuse', 'declared-tail-required'):
        target = work / name
        runner.copy_source(portable, target)
        build = target / 'zig/build/cuda.zig'
        text = build.read_text()
        original = '&.{"video prepare real:"}'
        if text.count(original) != 1:
            raise ValueError('The control build filter changed')
        if name == 'no-warm-reuse':
            filters = '&.{"data URL warm passes", "SSE closes"}'
            source = target / 'zig/src/server/images.zig'
            body = source.read_text()
            match = 'if (saved.matches(v, ps)) saved else null'
            if body.count(match) != 1:
                raise ValueError('The warm reuse control changed')
            source.write_text(body.replace(match, 'if (saved.matches(v, ps) and ps.len == 0) saved else null'))
        else:
            filters = '&.{"valid edit-list-trimmed MP4"}'
            shutil.copy2(args.source / 'zig/src/families/glm/video_core.zig',
                         target / 'zig/src/families/glm/video_core.zig')
        build.write_text(text.replace(original, filters))
        compiled = runner.command([str(args.zig.resolve()), 'build', 'compile-followup-video',
                                   '-j3', '--summary', 'all'], target, work / (name + '-compile.log'))
        log = work / (name + '.log')
        try:
            runner.docker_run(target / 'zig-out/bin/video-prepare-real', args.library,
                              'colima-tf-build', args.image, log)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('The negative control unexpectedly passed: ' + name)
        output = log.read_text()
        witness = ('FAIL (TestExpectedEqual)' in output and 'found 2' in output
                   if name == 'no-warm-reuse' else 'FAIL (Short)' in output)
        if not witness:
            raise RuntimeError('The control failed without its intended assertion: ' + name)
        results.append({'name': name, 'compile': compiled, 'intended_test_failure': True,
                        'log': str(log), 'log_sha256': runner.sha(log)})
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--zig', required=True, type=Path)
    p.add_argument('--library', required=True, type=Path)
    p.add_argument('--image', required=True)
    p.add_argument('--long-clip', required=True, type=Path)
    p.add_argument('--work-dir', type=Path)
    p.add_argument('--negative-controls', action='store_true', help='Compile and require both regression mutants to fail')
    args = p.parse_args()
    runner = module('video_fixture_runner', MOD / 'tests/video_prepare/run.py')
    manifest = json.loads((HERE / 'source-manifest.json').read_text())
    patch = MOD / 'patches/0940-native-video-warm-reuse.patch'
    for path, digest in ((args.source / 'HOME-LAB-SOURCES.json', manifest['base_source_marker_sha256']),
                         (MOD / 'source.py', manifest['native_source_helper_sha256']),
                         (patch, manifest['patch_sha256']), (args.library, manifest['library_sha256']),
                         (args.long_clip, manifest['long_fixture_sha256']),
                         (HERE / 'fixtures/trimmed-edit-list.mp4', manifest['trimmed_fixture_sha256'])):
        if runner.sha(path) != digest:
            raise ValueError('Input differs: ' + str(path))
    identity = json.loads((args.source / 'HOME-LAB-SOURCES.json').read_text())
    native_source.verify_source(args.source, identity)
    if subprocess.check_output([str(args.zig), 'version'], text=True).strip() != '0.17.0':
        raise ValueError('Zig 0.17.0 is required')
    work = args.work_dir or Path(tempfile.mkdtemp(prefix='glm-video-followup-'))
    work.mkdir(exist_ok=True)
    host = work / 'host'
    portable = work / 'portable'
    fixtures = module('followup_frozen_host', HERE / 'host_fixtures.py')
    for target in (host, portable):
        runner.copy_source(args.source, target)
        runner.apply(target, patch)
        for name, hashes in manifest['production_files'].items():
            if runner.sha(target / name) != hashes['after']:
                raise ValueError('Incorrect patch output: ' + name)
        fixtures.install_tests(target)
    server = portable / 'zig/src/server'
    guard = '    if (api.tfvideo.version() == null) return error.SkipZigTest;\n'
    for filename in ('video_prepare_core_test.zig', 'video_prepare_http_test.zig'):
        path = server / filename
        path.write_text(path.read_text().replace(guard, ''))
    shutil.copy2(args.long_clip, server / 'video-fixtures/long.mp4')
    with (server / 'video_prepare_core_test.zig').open('a') as out:
        out.write((MOD / 'tests/video_prepare/long_case.zig').read_text())
    build = portable / 'zig/build/cuda.zig'
    text = build.read_text()
    needle = '    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);'
    if text.count(needle) != 1 or text.count('    const host = b.graph.host;') != 1:
        raise ValueError('The portable build graph changed')
    text = text.replace(needle, needle + '''
    const real_video = b.addTest(.{ .name = "video-prepare-real", .root_module = server_tests, .filters = &.{"video prepare real:"} });
    b.step("compile-followup-video", "Required real decoder and warm EOF tests").dependOn(&b.addInstallArtifact(real_video, .{}).step);
''')
    text = text.replace('    const host = b.graph.host;', '    const host = b.resolveTargetQuery(.{ .cpu_arch = .aarch64, .os_tag = .linux, .abi = .gnu, .cpu_model = .baseline });', 1)
    build.write_text(text)
    zig = str(args.zig.resolve())
    checks = [runner.command([zig, 'build', 'test-native-video-prepare', 'test-native-bundle-contract',
                               'test-native-prepare-memory', 'test-native-media-errors',
                               'compile-glm-native-open', '-Dcpu=apple_m1', '-j3', '--summary', 'all'],
                              host, work / 'host.log')]
    checks.append(runner.command([zig, 'build', 'compile-followup-video', '-j3', '--summary', 'all'],
                                 portable, work / 'compile.log'))
    checks.append(runner.docker_run(portable / 'zig-out/bin/video-prepare-real', args.library,
                                    'colima-tf-build', args.image, work / 'portable.log'))
    log = (work / 'portable.log').read_text()
    if 'All 34 tests passed.' not in log or 'SKIP' in log:
        raise RuntimeError('The required 34-test real-library gate did not run completely')
    native_source.verify_source(args.source, identity)
    receipt = {'schema': 'glm-video-warm-reuse/v1', 'patch_sha256': manifest['patch_sha256'],
               'tests_real_library_passed': 34, 'skipped': 0, 'checks': checks,
               'input_source_unchanged': True, 'gpu_model_executed': False,
               'negative_controls_completed': False}
    if args.negative_controls:
        receipt['negative_controls'] = negative_controls(runner, args, portable, work)
        receipt['negative_controls_completed'] = True
    (work / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('PASS: ' + str(work / 'receipt.json'))


if __name__ == '__main__':
    main()
