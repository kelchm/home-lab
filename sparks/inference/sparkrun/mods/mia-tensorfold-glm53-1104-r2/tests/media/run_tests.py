#!/usr/bin/env python3
"""Apply 030 media patches to a disposable source copy and run real native Zig CPU tests.

The supplied --engine is read-only. No clone/fetch, model host, GPU execution, branch,
commit, push, or package install occurs. --with-api also applies 020 before 030.
--compile-native compiles real Linux CUDA startup/offer bodies with inert GPU images;
it does not run a CUDA model. Requires the pinned local Zig 0.17.0 toolchain.
"""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def run(argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print('+ ' + ' '.join(argv), flush=True)
    subprocess.run(argv, cwd=cwd, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--zig', default='zig')
    parser.add_argument('--cpu')
    parser.add_argument('--with-api', action='store_true')
    parser.add_argument('--compile-native', action='store_true')
    args = parser.parse_args()
    version = subprocess.check_output([args.zig, 'version'], text=True).strip()
    if version != '0.17.0':
        parser.error(f'expected Zig 0.17.0, found {version}')
    engine = args.engine.resolve(strict=True)
    here = Path(__file__).resolve().parent
    mod = here.parent.parent
    patches = sorted((mod / 'patches').glob('030*.patch'))
    if not patches:
        parser.error('no 030 media patches found')
    if args.with_api:
        patches = sorted((mod / 'patches').glob('020*.patch')) + patches
    scratch = Path(tempfile.mkdtemp(prefix='glm-native-media-test-'))
    print(f'scratch={scratch}', flush=True)
    for name in ('zig', 'tools', 'build.zig', 'build.zig.zon', '.zig-version'):
        source = engine / name
        if source.is_dir():
            shutil.copytree(source, scratch / name)
        else:
            shutil.copy2(source, scratch / name)
    for patch in patches:
        print(f'patch={patch.name} sha256={hashlib.sha256(patch.read_bytes()).hexdigest()}', flush=True)
        run(['git', 'apply', '--check', str(patch)], scratch)
        run(['git', 'apply', str(patch)], scratch)
    server = scratch / 'zig/src/server'
    shutil.copy2(here / 'native_media_test.zig', server / 'native_media_test.zig')
    shutil.copytree(here / 'fixtures', server / 'media-fixtures')
    shutil.copy2(mod / 'chat_template.jinja', server / 'native_media_template.jinja')
    (scratch / 'zig/src/glm_media_cpu.zig').write_text(
        'pub const picture = @import("core/picture/picture.zig");\n'
        'pub const Config = @import("families/glm/vision_config.zig").Config;\n'
        'pub const image = @import("families/glm/image_core.zig").For(picture);\n'
        'pub const video = @import("families/glm/video_core.zig").For(picture);\n'
    )
    with (server / 'root.zig').open('a') as root:
        root.write('\ntest { _ = @import("native_media_test.zig"); }\n')
    build = scratch / 'zig/build/cuda.zig'
    source = build.read_text()
    needle = '    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);'
    if source.count(needle) != 1:
        raise RuntimeError('server build graph changed; update the focused test-step insertion')
    addition = '''
    server_tests.addImport("glm_media", b.createModule(.{ .root_source_file = b.path("zig/src/glm_media_cpu.zig"), .target = host, .link_libc = true }));
    b.step("test-native-media-parity", "Production GLM media paths on CPU").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{"native media parity:"} })).step);
'''
    build.write_text(source.replace(needle, needle + addition))
    command = [args.zig, 'build', 'test-native-media-parity']
    if args.compile_native:
        command.extend(['compile-glm-native-open', 'compile-omni-video-host'])
    if args.cpu:
        command.append(f'-Dcpu={args.cpu}')
    command.extend(['--summary', 'all'])
    run(command, scratch, dict(os.environ, TF_GLM_CLEAR_THINKING='0'))
    print(f'PASS scratch={scratch}', flush=True)


if __name__ == '__main__':
    main()
