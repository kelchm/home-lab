#!/usr/bin/env python3
"""Reproduce 090 on disposable copies of the hashed Mia108 + 020/025/030/040/050/060/065/070/071 source."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

COPY = ('zig', 'tools', 'build.zig', 'build.zig.zon', '.zig-version')
IGNORE = {'.git', '.zig-cache', 'zig-out', '__pycache__'}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for name in COPY:
        source = root / name
        files = sorted(source.rglob('*')) if source.is_dir() else [source]
        for path in files:
            if path.is_file() and not any(part in IGNORE for part in path.parts):
                digest.update(f'{path.relative_to(root).as_posix()}\0{sha(path)}\n'.encode())
    return digest.hexdigest()


def copy_source(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=False)
    for name in COPY:
        item = source / name
        if item.is_dir():
            shutil.copytree(item, dest / name, ignore=shutil.ignore_patterns(*IGNORE))
        else:
            shutil.copy2(item, dest / name)


def run(command: list[str], cwd: Path, log: Path, expected_failure: bool = False) -> dict:
    print('+ ' + ' '.join(command), flush=True)
    with log.open('w') as output:
        result = subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                env=dict(os.environ, TF_GLM_CLEAR_THINKING='0'))
    if (result.returncode == 0) == expected_failure:
        print(log.read_text(), flush=True)
        raise RuntimeError(f'unexpected exit {result.returncode}: {log}')
    print(f'exit={result.returncode} log={log}', flush=True)
    return {'command': command, 'exit': result.returncode, 'log': log.name, 'log_sha256': sha(log)}


def instrument(root: Path, here: Path, baseline: bool = False) -> None:
    mod = here.parent.parent
    target = root / 'zig/src/server'
    fixture = (mod / 'tests/media/native_media_test.zig').read_text()
    fixture = fixture.replace('.ctx = d.ctx, .prepare = d.prepare } else null',
                              '.ctx = d.ctx, .prepare = d.prepare, .checked = d.checked } else null')
    fixture = re.sub(r'^(const|fn) ', r'pub \1 ', fixture, flags=re.M)
    fixture = fixture.replace('    fn init(', '    pub fn init(').replace('    fn deinit(', '    pub fn deinit(')
    # The earlier media fixture predates locked 071's fair per-clip share. Adapt only this stale expectation.
    old = 'for (1..5) |n| try t.expectEqual(@as(u32, 16384), images.videoTokenBudget(o, n));'
    new = 'for (1..5) |n| try t.expectEqual(@min(@as(u32, 16384), @as(u32, 32768) / @as(u32, @intCast(n))), images.videoTokenBudget(o, n));'
    if fixture.count(old) != 1:
        raise RuntimeError('the pinned media fixture changed')
    (target / 'native_media_test.zig').write_text(fixture.replace(old, new))
    shutil.copytree(mod / 'tests/media/fixtures', target / 'media-fixtures')
    shutil.copy2(mod / 'chat_template.jinja', target / 'native_media_template.jinja')
    shutil.copy2(here / 'arena_probe.zig', target / 'prepare_arena_probe.zig')
    imports = '\ntest { _ = @import("prepare_arena_probe.zig"); }\n'
    if not baseline:
        shutil.copy2(here / 'harness.zig', target / 'prepare_harness.zig')
        shutil.copy2(here / 'server_cases.zig', target / 'prepare_contract_test.zig')
        imports += '\ntest { _ = @import("prepare_contract_test.zig"); }\n'
    with (target / 'root.zig').open('a') as out:
        out.write(imports)
    (root / 'zig/src/glm_media_cpu.zig').write_text(
        'pub const picture = @import("core/picture/picture.zig");\n'
        'pub const Config = @import("families/glm/vision_config.zig").Config;\n'
        'pub const image = @import("families/glm/image_core.zig").For(picture);\n'
        'pub const video = @import("families/glm/video_core.zig").For(picture);\n')
    path = root / 'zig/build/cuda.zig'
    source = path.read_text()
    needle = '    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);'
    filters = '"prepare arena receipt:"' if baseline else '"prepare memory:", "prepare arena receipt:", "native media parity:"'
    addition = '''
    server_tests.addImport("glm_media", b.createModule(.{ .root_source_file = b.path("zig/src/glm_media_cpu.zig"), .target = host, .link_libc = true }));
    b.step("test-prepare-memory", "Native resident memory contract without devices").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{FILTERS} })).step);
    b.step("test-prepare-negative", "Planted admission bypass").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{"prepare memory: four delayed consumers"} })).step);
'''.replace('FILTERS', filters)
    if source.count(needle) != 1:
        raise RuntimeError('the native host build graph changed')
    source = source.replace(needle, needle + addition)
    needle = '    @import("glm_contracts.zig").tests(b, glm, mods.lanes, cuda, mods.core);'
    addition = '''
    b.step("test-prepare-media-lifecycle", "Existing GLM media allocator and TP lifecycle").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = glm, .filters = &.{"media lifecycle:", "a follower's failed image admission", "128 video pairs cross", "a row budget whose", "a request with images and a video", "a splice's map"} })).step);
'''
    if source.count(needle) != 1:
        raise RuntimeError('the native GLM build graph changed')
    path.write_text(source.replace(needle, needle + addition))


def lean(root: Path) -> Counter:
    root = root.resolve()
    path = root / 'tools/zig/lean_check.py'
    namespace = {'__file__': str(path), '__name__': 'lean_probe'}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    found = Counter()
    for name in namespace['TREES']:
        for item in (root / name).rglob('*'):
            if item.is_file() and item.suffix in namespace['COMMENT']:
                for problem in namespace['problems'](item):
                    kind = problem.split(': ', 1)[1]
                    if 'lines (split past' in kind:
                        kind = 'file over 600 lines'
                    found[(item.relative_to(root).as_posix(), kind)] += 1
    return found


def arena_rows(log: Path) -> list[dict]:
    rows = []
    for line in log.read_text().splitlines():
        if not line.startswith('ARENA_RECEIPT '):
            continue
        row = dict(word.split('=', 1) for word in line.split()[1:])
        rows.append(row)
    if len(rows) != 2:
        raise RuntimeError(f'missing before/after arena measurements in {log}')
    return sorted(rows, key=lambda row: row['case'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--zig', type=Path, required=True)
    parser.add_argument('--cpu', default='apple_m1')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--skip-negative', action='store_true')
    args = parser.parse_args()
    zig = str(args.zig.resolve(strict=True))
    version = subprocess.check_output([zig, 'version'], text=True).strip()
    if version != '0.17.0':
        parser.error(f'expected Zig 0.17.0, found {version}')
    here = Path(__file__).resolve().parent
    mod = here.parent.parent
    manifest = json.loads((here / 'source-manifest.json').read_text())
    source = args.engine.resolve(strict=True)
    marker = source / 'HOME-LAB-SOURCES.json'
    if sha(marker) != manifest['marker_sha256']:
        raise RuntimeError('the locked-source marker differs')
    for name, expected in json.loads(marker.read_text())['source_files'].items():
        if sha(source / name) != expected:
            raise RuntimeError(f'input source hash mismatch: {name}')
    before = tree_hash(source)
    if before != manifest['input_tree_sha256']:
        raise RuntimeError('the full input build/source tree differs')
    patch = mod / 'patches' / manifest['patch']
    if sha(patch) != manifest['patch_sha256']:
        raise RuntimeError('patch checksum differs')
    for name, expected in manifest['test_inputs'].items():
        if sha(mod / name) != expected:
            raise RuntimeError(f'test input differs: {name}')
    work = args.work_dir.resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix='glm090-prepare-')) / 'run'
    work.mkdir(parents=True, exist_ok=False)
    base, candidate = work / 'baseline', work / 'candidate'
    copy_source(source, base)
    copy_source(source, candidate)
    commands = []
    commands.append(run(['git', 'apply', '--check', str(patch)], candidate, work / 'apply-check.log'))
    commands.append(run(['git', 'apply', str(patch)], candidate, work / 'apply.log'))
    for item in manifest['files']:
        if sha(candidate / item['path']) != item['after_sha256']:
            raise RuntimeError(f'patched hash mismatch: {item["path"]}')
    applied = tree_hash(candidate)
    old_lean, new_lean = lean(base), lean(candidate)
    added_lean = new_lean - old_lean
    if added_lean:
        raise RuntimeError(f'new lean findings: {added_lean}')
    commands.append(run([zig, 'fmt', '--check', *[item['path'] for item in manifest['files']]], candidate, work / 'fmt.log'))
    instrument(base, here, baseline=True)
    instrument(candidate, here)
    flags = [f'-Dcpu={args.cpu}', '-j2', '--summary', 'all']
    commands.append(run([zig, 'build', 'test-prepare-memory', *flags], base, work / 'baseline.log'))
    targets = ['test-prepare-memory', 'test-prepare-media-lifecycle', 'test-admission-host',
               'test-http-limits-host', 'test-server-recovery-host', 'test-glm-protocol-host',
               'test-glm-kv-pages-host', 'compile-glm-native-open', 'compile-omni-video-host', 'server-cpu']
    commands.append(run([zig, 'build', *targets, *flags], candidate, work / 'test.log'))
    old, new = arena_rows(work / 'baseline.log'), arena_rows(work / 'test.log')
    for a, b in zip(old, new):
        for field in ('case', 'ids', 'sha', 'key', 'images'):
            if a[field] != b[field]:
                raise RuntimeError(f'prompt output changed: {field}')
        if int(b['retained']) >= int(a['retained']):
            raise RuntimeError('temporary prompt storage was not reduced')
    negative = None
    if not args.skip_negative:
        path = candidate / 'zig/src/server/prepare_gate.zig'
        original = path.read_text()
        needle = 'if (policy.residents == 0) return false;'
        if original.count(needle) != 1:
            raise RuntimeError('admission bypass control insertion changed')
        try:
            path.write_text(original.replace(needle, 'if (policy.residents <= 128) return false;'))
            negative = run([zig, 'build', 'test-prepare-negative', *flags], candidate, work / 'negative.log', expected_failure=True)
            if 'gate waiting expected 8, actual 0' not in (work / 'negative.log').read_text():
                raise RuntimeError('negative control did not fail the intended body-admission assertion')
        finally:
            path.write_text(original)
    after = tree_hash(source)
    if after != before or sha(marker) != manifest['marker_sha256']:
        raise RuntimeError('read-only input changed during verification')
    receipt = {
        'status': 'pass', 'patch': patch.name, 'patch_sha256': sha(patch),
        'zig_version': version, 'zig_sha256': sha(Path(zig)), 'cpu': args.cpu,
        'marker_sha256': sha(marker), 'input_tree_sha256': before, 'input_after_tree_sha256': after,
        'patched_tree_sha256_before_instrumentation': applied,
        'commands': commands, 'negative_control': negative,
        'lean': {'baseline': sum(old_lean.values()), 'candidate': sum(new_lean.values()), 'new': 0},
        'arena_before': old, 'arena_after': new,
        'boundary': 'CPU native HTTP/allocators/GLM media and recording TP; Linux CUDA compile only; no model or GPU execution; no 080 integration',
    }
    (work / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'PASS receipt={work / "receipt.json"}', flush=True)


if __name__ == '__main__':
    main()
