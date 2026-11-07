#!/usr/bin/env python3
"""Reproduce 0750 on disposable locked-source copies; no engine/model binary or GPU is executed."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
MOD = HERE.parent.parent
PARTS = ('zig', 'tools', 'build.zig', 'build.zig.zon', '.zig-version')
LATER = ('0800-native-checkpoint-restore-epochs.patch', '0820-native-checkpoint-media-capacity.patch',
         '0900-native-prepare-memory-contract.patch', '0920-native-preparation-test-fixture.patch')
PATCH = '0750-native-video-single-pass.patch'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(root):
    out = {}
    for name in PARTS:
        item = root / name
        for path in sorted(item.rglob('*')) if item.is_dir() else [item]:
            if path.is_file() and not any(x in path.parts for x in ('.zig-cache', 'zig-out', '__pycache__')):
                out[str(path.relative_to(root))] = sha(path)
    return out


def copy_source(source, dest):
    dest.mkdir()
    for name in PARTS:
        item = source / name
        if item.is_dir():
            shutil.copytree(item, dest / name, ignore=shutil.ignore_patterns('.zig-cache', 'zig-out', '__pycache__'))
        else:
            shutil.copy2(item, dest / name)


def command(args, cwd, log):
    print('+ ' + ' '.join(map(str, args)), flush=True)
    with log.open('w') as output:
        result = subprocess.run(args, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                env={**os.environ, 'TF_GLM_CLEAR_THINKING': '0'})
    if result.returncode:
        print(log.read_text()[-18000:], flush=True)
        raise RuntimeError(f'exit={result.returncode}: {log}')
    return {'command': list(map(str, args)), 'exit': result.returncode, 'log': log.name, 'sha256': sha(log)}


def apply(root, patch, reverse=False):
    subprocess.run(['git', 'apply', '--check', *(['--reverse'] if reverse else []), str(patch)], cwd=root, check=True)
    subprocess.run(['git', 'apply', *(['--reverse'] if reverse else []), str(patch)], cwd=root, check=True)


def instrument(root, source, portable=False, long_clip=None):
    spec = importlib.util.spec_from_file_location('site_host', HERE / 'host_fixtures.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    host.install_tests(root)
    with (root / 'zig/src/families/glm/cuda_images_interleave_test.zig').open('a') as out:
        out.write((HERE / 'window_case.zig').read_text())
    server = root / 'zig/src/server'
    for name, target in [('core_cases.zig', 'video_prepare_core_test.zig'),
                         ('server_cases.zig', 'video_prepare_http_test.zig'),
                         ('harness.zig', 'video_prepare_harness.zig')]:
        shutil.copy2(HERE / name, server / target)
    shutil.copytree(HERE / 'fixtures', server / 'video-fixtures')
    if long_clip is not None:
        shutil.copy2(long_clip, server / 'video-fixtures/long.mp4')
        with (server / 'video_prepare_core_test.zig').open('a') as out:
            out.write((HERE / 'long_case.zig').read_text())
    with (server / 'root.zig').open('a') as out:
        out.write('\ntest { _ = @import("video_prepare_core_test.zig"); _ = @import("video_prepare_http_test.zig"); }\n')
    # Keep the original eager code intact as the independent float oracle; never import the edited patchifier.
    shutil.copy2(source / 'zig/src/families/glm/video_core.zig', root / 'zig/src/families/glm/video_eager_reference.zig')
    with (root / 'zig/src/glm_media_cpu.zig').open('a') as out:
        out.write('pub const eager = @import("families/glm/video_eager_reference.zig").For(picture);\n')
    build = root / 'zig/build/cuda.zig'
    text = build.read_text()
    needle = '    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);'
    addition = '''
    const video_test = b.addTest(.{ .name = "video-prepare-test", .root_module = server_tests, .filters = &.{"video prepare:"} });
    b.step("test-video-prepare", "Single-pass preparation allocator and failure gates").dependOn(&b.addRunArtifact(video_test).step);
    const video_real = b.addTest(.{ .name = "video-prepare-real", .root_module = server_tests, .filters = &.{"video prepare real:"} });
    b.step("compile-video-prepare-real", "Portable library and native HTTP tests, never an inference executable").dependOn(&b.addInstallArtifact(video_real, .{}).step);
'''
    if text.count(needle) != 1:
        raise ValueError('test build insertion changed')
    if portable:
        # Test-only cross target. The inference target and source build graph are never edited in the bundle.
        text = text.replace('    const host = b.graph.host;', '    const host = b.resolveTargetQuery(.{ .cpu_arch = .aarch64, .os_tag = .linux, .abi = .gnu, .cpu_model = .baseline });', 1)
    build.write_text(text.replace(needle, needle + addition))


def docker_run(binary, library, docker_context, toolchain, log):
    docker = ['docker', '--context', docker_context]
    image = subprocess.check_output([*docker, 'image', 'inspect', '--format', '{{.Id}}', toolchain], text=True).strip()
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w') as archive:
        archive.add(binary, arcname='video-prepare-real')
        archive.add(library, arcname='libtfvideo.so')
    name = 'glm-video-prepare-' + uuid.uuid4().hex
    args = [*docker, 'run', '--rm', '--name', name, '-i', '--network', 'none', '--entrypoint', 'bash', image,
            '-c', 'mkdir /probe && tar -xf - -C /probe && cd /probe && LD_LIBRARY_PATH=/probe ./video-prepare-real']
    try:
        with log.open('w') as output:
            result = subprocess.run(args, input=payload.getvalue(), stdout=output, stderr=subprocess.STDOUT, timeout=300)
        if result.returncode:
            print(log.read_text()[-22000:])
            raise RuntimeError(f'portable tests failed: {log}')
    finally:
        subprocess.run([*docker, 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'command': args, 'image': image, 'library_sha256': sha(library), 'binary_sha256': sha(binary),
            'exit': result.returncode, 'log': log.name, 'sha256': sha(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--zig', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--long-clip', type=Path, help='33-second 1080p fixture from make_long_fixture.py')
    parser.add_argument('--docker-context', default='colima-tf-build')
    parser.add_argument('--toolchain-image', default='home-lab-glm53-ffmpeg:9.0.2-8c385028')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    manifest = json.loads((HERE / 'source-manifest.json').read_text())
    before = hashes(source)
    if before != manifest['input_files'] or sha(source / 'HOME-LAB-SOURCES.json') != manifest['input_marker_sha256']:
        raise ValueError('immutable source differs')
    for name, digest in manifest['harness_inputs'].items():
        if sha(MOD / name) != digest:
            raise ValueError(f'host fixture input differs: {name}')
    if args.long_clip is not None and sha(args.long_clip) != manifest['long_fixture']['sha256']:
        raise ValueError('long fixture differs from the recorded generator output')
    for name, digest in manifest['fixtures'].items():
        if sha(HERE / 'fixtures' / name) != digest:
            raise ValueError(f'fixture differs: {name}')
    for name, digest in manifest['later_patches'].items():
        if sha(MOD / 'patches' / name) != digest:
            raise ValueError(f'composition patch differs: {name}')
    if sha(args.library) != manifest['library_sha256']:
        raise ValueError('expected the immutable candidate-9 portable library')
    if subprocess.check_output([str(args.zig), 'version'], text=True).strip() != '0.17.0':
        raise ValueError('expected Zig 0.17.0')
    work = args.work_dir or Path(tempfile.mkdtemp(prefix='glm-video-prepare-'))
    work.mkdir(exist_ok=True)
    composed = work / 'composed'
    copy_source(source, composed)
    for name in reversed(LATER):
        apply(composed, MOD / 'patches' / name, reverse=True)
    apply(composed, MOD / 'patches' / PATCH)
    for name in LATER:
        apply(composed, MOD / 'patches' / name)
    receipt = {'schema': 'glm-video-single-pass/v1', 'patch_sha256': sha(MOD / 'patches' / PATCH),
               'filename_order_applies': True, 'checks': [], 'gpu_pixel_model_quality': 'unqualified'}
    receipt['composed_files'] = hashes(composed)
    host = work / 'host'
    portable = work / 'portable'
    copy_source(composed, host)
    copy_source(composed, portable)
    instrument(host, source, long_clip=args.long_clip)
    instrument(portable, source, portable=True, long_clip=args.long_clip)
    if args.long_clip is not None:
        receipt['long_clip_sha256'] = sha(args.long_clip)
    zig = str(args.zig.resolve())
    receipt['checks'].append(command([zig, 'build', 'test-video-prepare', 'test-native-bundle-contract',
        'test-native-media-lifecycle', 'test-native-prepare-memory', 'test-native-media-budget',
        'compile-glm-native-open', 'test-omni-video-host', '-Dcpu=apple_m1', '-j3', '--summary', 'all'], host, work / 'host.log'))
    receipt['checks'].append(command([zig, 'build', 'compile-video-prepare-real', '-j3', '--summary', 'all'], portable, work / 'portable-compile.log'))
    receipt['checks'].append(docker_run(portable / 'zig-out/bin/video-prepare-real', args.library, args.docker_context, args.toolchain_image, work / 'portable.log'))
    if hashes(source) != before:
        raise RuntimeError('immutable source was modified')
    if sha(args.library) != manifest['library_sha256']:
        raise RuntimeError('immutable library was modified')
    receipt['input_unchanged'] = True
    receipt['library_unchanged'] = True
    (work / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'PASS: {work / "receipt.json"}; GPU pixel/model quality unqualified', flush=True)


if __name__ == '__main__':
    main()
