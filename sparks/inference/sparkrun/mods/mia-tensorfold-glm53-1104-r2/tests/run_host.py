#!/usr/bin/env python3
"""Run the composed native bundle's API, media, HTTP and checkpoint gates without GPUs.

--source must come from this mod's source.py. It stays read-only; compilation and
test-only build-graph additions use a fresh disposable directory. No target engine,
Docker daemon, model endpoint, package installation or network is used.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

MOD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOD))
import source as native_source


def install_tests(scratch, mod=MOD):
    server = scratch / 'zig/src/server'
    for source, target in (
        ('api/native_api_test.zig', 'native_api_test.zig'),
        ('api/reasoning_cache_test.zig', 'native_api_cache_test.zig'),
        ('media/native_media_test.zig', 'native_media_test.zig'),
    ):
        shutil.copy2(mod / 'tests' / source, server / target)
    api_fixture = server / 'native_api_test.zig'
    api_fixture.write_text(api_fixture.read_text() + '\n' + (mod / 'tests/media_errors/server_cases.zig').read_text())
    media = server / 'native_media_test.zig'
    content = media.read_text()
    adapter = '.ctx = d.ctx, .prepare = d.prepare } else null'
    budgets = 'for (1..5) |n| try t.expectEqual(@as(u32, 16384), images.videoTokenBudget(o, n));'
    if content.count(adapter) != 1 or content.count(budgets) != 1:
        raise ValueError('The historical 030 media fixture changed; review the final contract adapter')
    # 070 adds checked lazy callbacks; 071 restores Python's pre-planning clip shares.
    content = content.replace(adapter, '.ctx = d.ctx, .prepare = d.prepare, .checked = d.checked } else null')
    content = content.replace(budgets, 'for ([_]u32{ 16384, 16384, 10922, 8192 }, 1..) |cap, n| try t.expectEqual(cap, images.videoTokenBudget(o, n));')
    content = content.replace('GLM video clips have independent 16384 ceilings and actual rows share 32768',
                              'GLM video clips share 32768 within their 16384 ceilings')
    # The preparation HTTP harness consumes the same real template/PNG fixtures.
    content = re.sub(r'^(const|fn) ', r'pub \1 ', content, flags=re.M)
    content = content.replace('    fn init(', '    pub fn init(').replace('    fn deinit(', '    pub fn deinit(')
    media.write_text(content + '\n' + (mod / 'tests/compat/server_cases.zig').read_text())
    for name in ('api', 'media'):
        shutil.copy2(mod / 'chat_template.jinja', server / f'native_{name}_template.jinja')
    shutil.copytree(mod / 'tests/media/fixtures', server / 'media-fixtures')
    (scratch / 'zig/src/glm_media_cpu.zig').write_text(
        'pub const picture = @import("core/picture/picture.zig");\n'
        'pub const Config = @import("families/glm/vision_config.zig").Config;\n'
        'pub const image = @import("families/glm/image_core.zig").For(picture);\n'
        'pub const video = @import("families/glm/video_core.zig").For(picture);\n')
    with (server / 'root.zig').open('a') as root:
        for name in ('native_api_test.zig', 'native_api_cache_test.zig', 'native_media_test.zig'):
            root.write(f'\ntest {{ _ = @import("{name}"); }}\n')
        for name, target in (
            ('harness.zig', 'prepare_harness.zig'),
            ('server_cases.zig', 'prepare_contract_test.zig'),
            ('arena_probe.zig', 'prepare_arena_probe.zig'),
        ):
            shutil.copy2(mod / 'tests/prepare_memory' / name, server / target)
            if name != 'harness.zig':
                root.write(f'\ntest {{ _ = @import("{target}"); }}\n')
    build = scratch / 'zig/build/cuda.zig'
    content = build.read_text()
    needle = '    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);'
    if content.count(needle) != 1:
        raise ValueError('The native test build graph changed')
    addition = '''
    server_tests.addImport("glm_media", b.createModule(.{ .root_source_file = b.path("zig/src/glm_media_cpu.zig"), .target = host, .link_libc = true }));
    b.step("test-native-bundle-contract", "Composed site API, media and body policies").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{"native API parity:", "native media parity:", "HTTP body profile:", "media compatibility:", "media lifecycle:"} })).step);
    b.step("test-native-media-budget", "Active preparation time excludes queue and prefill waits").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = mods.lanes, .filters = &.{"media compatibility:"} })).step);
    b.step("test-native-prepare-memory", "Request ownership, admission and actual allocator accounting").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{"prepare memory:", "prepare arena receipt:"} })).step);
    const media_errors = b.step("test-native-media-errors", "Typed late media refusals, isolated lanes and API errors");
    for ([_]*std.Build.Module{ server_tests, mods.api }) |module| media_errors.dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = module, .filters = &.{"media request errors:"} })).step);
'''
    content = content.replace(needle, needle + addition)
    needle = '    @import("glm_contracts.zig").tests(b, glm, mods.lanes, cuda, mods.core);'
    if content.count(needle) != 1:
        raise ValueError('The native GLM host build graph changed')
    addition = '''
    b.step("test-native-media-lifecycle", "Bounded media rows and actual TP protocol").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = glm, .filters = &.{"media lifecycle:", "a follower's failed image admission", "128 video pairs cross", "a row budget whose", "a request with images and a video", "a splice's map"} })).step);
    media_errors.dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = glm, .filters = &.{"media request errors:"} })).step);
'''
    build.write_text(content.replace(needle, needle + addition))
    install_video_tests(scratch, mod)


def install_video_tests(scratch, mod=MOD):
    here = mod / 'tests/video_prepare'
    relative = 'zig/src/families/glm/video_core.zig'
    # Reconstruct the frozen eager oracle in a separate directory; native inputs stay intact.
    with tempfile.TemporaryDirectory(prefix='glm-video-oracle-') as name:
        reference = Path(name)
        path = reference / relative
        path.parent.mkdir(parents=True)
        shutil.copy2(scratch / relative, path)
        correction = mod / 'patches/0940-native-video-warm-reuse.patch'
        if correction.is_file():
            subprocess.run(['git', 'apply', '--reverse', '--include=' + relative, str(correction)], cwd=reference, check=True)
        patch = str(mod / 'patches/0750-native-video-single-pass.patch')
        subprocess.run(['git', 'apply', '--reverse', '--include=' + relative, patch], cwd=reference, check=True)
        expected = json.loads((here / 'source-manifest.json').read_text())['input_files'][relative]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('The frozen eager video oracle changed')
        shutil.copy2(path, scratch / 'zig/src/families/glm/video_eager_reference.zig')
    server = scratch / 'zig/src/server'
    for source, target in (
        ('core_cases.zig', 'video_prepare_core_test.zig'),
        ('server_cases.zig', 'video_prepare_http_test.zig'),
        ('harness.zig', 'video_prepare_harness.zig'),
    ):
        content = (here / source).read_text()
        if source != 'harness.zig':
            # The required separate Linux gate runs the untouched fixtures with the exact portable library.
            # The generic host suite has no required FFmpeg installation; report these as skipped when absent.
            content = re.sub(r'(^test "video prepare real:[^\n]+\{\n)',
                             r'\1    if (api.tfvideo.version() == null) return error.SkipZigTest;\n', content, flags=re.M)
        (server / target).write_text(content)
    shutil.copytree(here / 'fixtures', server / 'video-fixtures')
    followup = mod / 'tests/video_followup/fixtures.py'
    if followup.is_file():
        spec = importlib.util.spec_from_file_location('video_followup_fixtures', followup)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.install(scratch)
    with (server / 'root.zig').open('a') as root:
        root.write('\ntest { _ = @import("video_prepare_core_test.zig"); _ = @import("video_prepare_http_test.zig"); }\n')
    with (scratch / 'zig/src/glm_media_cpu.zig').open('a') as media:
        media.write('pub const eager = @import("families/glm/video_eager_reference.zig").For(picture);\n')
    with (scratch / 'zig/src/families/glm/cuda_images_interleave_test.zig').open('a') as tests:
        tests.write((here / 'window_case.zig').read_text())
    build = scratch / 'zig/build/cuda.zig'
    content = build.read_text()
    needle = '    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);'
    addition = '''
    b.step("test-native-video-prepare", "Single-pass video canvases, allocator bounds and independent eager parity").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{"video prepare:", "video followup:"} })).step);
'''
    if content.count(needle) != 1:
        raise ValueError('The video host build graph changed')
    build.write_text(content.replace(needle, needle + addition))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--zig', required=True)
    parser.add_argument('--cpu')
    parser.add_argument('--jobs', type=int, default=3)
    parser.add_argument('--full', action='store_true', help='also run broad CUDA-host, runtime and server suites')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error('--jobs must be between 1 and 32')
    if subprocess.check_output([args.zig, 'version'], text=True).strip() != '0.17.0':
        parser.error('This bundle requires Zig 0.17.0')
    subprocess.run([sys.executable, str(MOD / 'prepare.py'), '--check'], check=True)
    lock = json.loads((MOD / 'upstream.lock.json').read_text())
    native_source.verify_source(args.source, lock)
    scratch = Path(tempfile.mkdtemp(prefix='glm1104-bundle-host-'))
    for name in native_source.SOURCE_PARTS:
        path = args.source / name
        if path.is_dir():
            shutil.copytree(path, scratch / name)
        else:
            shutil.copy2(path, scratch / name)
    install_tests(scratch)
    command = [args.zig, 'build', 'test-native-bundle-contract', 'test-checkpoint-bounded-host',
               'test-checkpoint-restore-host', 'test-native-prepare-memory',
               'test-native-media-errors',
               'test-native-video-prepare',
               'test-native-media-budget', 'test-native-media-lifecycle', 'test-glm-prompts-host',
               'test-glm-protocol-host', 'test-glm-kv-pages-host', 'test-omni-video-host',
               'test-glm-snapshots-host', 'compile-glm-native-open', 'compile-omni-video-host']
    if args.full:
        command.extend(['test-cuda-host', 'test-runtime-host', 'test-server-cpu'])
    if args.cpu:
        command.append(f'-Dcpu={args.cpu}')
    command.extend([f'-j{args.jobs}', '--summary', 'all'])
    print(f'scratch={scratch}', flush=True)
    print('site_patches_sha256=' + lock['site_patches_sha256'], flush=True)
    print('+ ' + ' '.join(command), flush=True)
    subprocess.run(command, cwd=scratch, env={**os.environ, 'TF_GLM_CLEAR_THINKING': '0'}, check=True)
    print('PASS: native host gates; GPU and durable serving remain unqualified', flush=True)


if __name__ == '__main__':
    main()
