#!/usr/bin/env python3
"""Verify/apply 070 to a disposable copy of the fully composed 020..060 engine.

The input stays read-only. No Git repository, model, device, deployment, network,
branch, commit or push is used. CUDA calls in executable tests are recording ABIs;
compile-only Linux CUDA targets have inert GPU images.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for name in ('zig', 'tools', 'build.zig', 'build.zig.zon', '.zig-version'):
        source = root / name
        files = sorted(source.rglob('*')) if source.is_dir() else [source]
        for path in files:
            if not path.is_file() or any(p in {'.git', '.zig-cache', 'zig-out', '__pycache__'} for p in path.parts):
                continue
            digest.update(f'{path.relative_to(root).as_posix()}\0{sha(path)}\n'.encode())
    return digest.hexdigest()


def run(command: list[str], cwd: Path, log: Path | None = None) -> None:
    print('+ ' + ' '.join(command), flush=True)
    if log is None:
        subprocess.run(command, cwd=cwd, check=True)
        return
    with log.open('w') as output:
        result = subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                env=dict(os.environ, TF_GLM_CLEAR_THINKING='0'))
    print(log.read_text(), end='', flush=True)
    result.check_returncode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True, help='Mia108 + local020,030,040,050,060 (already composed)')
    parser.add_argument('--zig', required=True)
    parser.add_argument('--cpu', default='apple_m1')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--no-compile-native', action='store_true')
    args = parser.parse_args()
    version = subprocess.check_output([args.zig, 'version'], text=True).strip()
    if version != '0.17.0':
        parser.error(f'expected Zig 0.17.0, found {version}')
    here = Path(__file__).resolve().parent
    mod = here.parent.parent
    manifest = json.loads((here / 'source-manifest.json').read_text())
    patch = mod / 'patches' / manifest['patch']
    if sha(patch) != manifest['patch_sha256']:
        raise RuntimeError('patch hash differs from the source manifest')
    engine = args.engine.resolve(strict=True)
    before = tree_hash(engine)
    for item in manifest['files']:
        if sha(engine / item['path']) != item['before_sha256']:
            raise RuntimeError(f'input differs from composed baseline: {item["path"]}')
    if args.work_dir:
        work = args.work_dir.resolve()
        work.mkdir(parents=True, exist_ok=False)
    else:
        work = Path(tempfile.mkdtemp(prefix='glm070-media-test-'))
    print(f'scratch={work}\npatch={patch.name} sha256={sha(patch)}\ninput_tree_sha256={before}', flush=True)
    for name in ('zig', 'tools', 'build.zig', 'build.zig.zon', '.zig-version'):
        source = engine / name
        if source.is_dir():
            shutil.copytree(source, work / name, ignore=shutil.ignore_patterns('.git', '.zig-cache', 'zig-out', '__pycache__'))
        else:
            shutil.copy2(source, work / name)
    run(['git', 'apply', '--check', str(patch)], work)
    run(['git', 'apply', str(patch)], work)
    for item in manifest['files']:
        if sha(work / item['path']) != item['after_sha256']:
            raise RuntimeError(f'applied output hash differs: {item["path"]}')
    applied = tree_hash(work)
    run([args.zig, 'fmt', '--check', *[item['path'] for item in manifest['files']]], work)
    server = work / 'zig/src/server'
    cases = (here.parent / 'media/native_media_test.zig').read_text()
    needle = '.ctx = d.ctx, .prepare = d.prepare } else null'
    if cases.count(needle) != 1:
        raise RuntimeError('existing media fixture adapter changed')
    cases = cases.replace(needle, '.ctx = d.ctx, .prepare = d.prepare, .checked = d.checked } else null')
    (server / 'native_media_test.zig').write_text(cases + '\n' + (here / 'server_cases.zig').read_text())
    shutil.copytree(here.parent / 'media/fixtures', server / 'media-fixtures')
    shutil.copy2(mod / 'chat_template.jinja', server / 'native_media_template.jinja')
    (work / 'zig/src/glm_media_cpu.zig').write_text(
        'pub const picture = @import("core/picture/picture.zig");\n'
        'pub const Config = @import("families/glm/vision_config.zig").Config;\n'
        'pub const image = @import("families/glm/image_core.zig").For(picture);\n'
        'pub const video = @import("families/glm/video_core.zig").For(picture);\n')
    with (server / 'root.zig').open('a') as root:
        root.write('\ntest { _ = @import("native_media_test.zig"); }\n')
    build = work / 'zig/build/cuda.zig'
    source = build.read_text()
    for needle, addition in [
        ('    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);', '''
    server_tests.addImport("glm_media", b.createModule(.{ .root_source_file = b.path("zig/src/glm_media_cpu.zig"), .target = host, .link_libc = true }));
    b.step("test-native-media-parity", "Production GLM media paths on CPU").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{"native media parity:", "media lifecycle:"} })).step);
'''),
        ('    @import("glm_contracts.zig").tests(b, glm, mods.lanes, cuda, mods.core);', '''
    b.step("test-media-lifecycle", "Bounded GLM CUDA media with recording driver").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = glm, .filters = &.{"media lifecycle:", "a follower's failed image admission", "128 video pairs cross", "a row budget whose", "a request with images and a video", "a splice's map"} })).step);
'''),
    ]:
        if source.count(needle) != 1:
            raise RuntimeError('build graph changed; update the focused test-step insertion')
        source = source.replace(needle, needle + addition)
    build.write_text(source)
    command = [args.zig, 'build', 'test-media-lifecycle', 'test-native-media-parity',
               'test-glm-protocol-host', 'test-glm-kv-pages-host', 'test-omni-video-host']
    if not args.no_compile_native:
        command += ['compile-glm-native-open', 'compile-omni-video-host']
    if args.cpu:
        command.append(f'-Dcpu={args.cpu}')
    command += ['--summary', 'all']
    run(command, work, work / 'test.log')
    after = tree_hash(engine)
    if before != after:
        raise RuntimeError('read-only input changed during the gate')
    receipt = {
        'patch': patch.name, 'patch_sha256': sha(patch), 'zig_version': version,
        'zig_sha256': sha(Path(args.zig).resolve()), 'command': command, 'scratch': str(work),
        'input_tree_sha256': before, 'input_after_tree_sha256': after,
        'patched_tree_sha256_before_test_instrumentation': applied,
        'log_sha256': sha(work / 'test.log'), 'status': 'pass',
        'boundary': 'CPU recording-driver and source compilation only; no GPU numerical, codec or durable checkpoint qualification',
    }
    (work / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'PASS receipt={work / "receipt.json"}', flush=True)


if __name__ == '__main__':
    main()
