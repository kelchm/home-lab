#!/usr/bin/env python3
"""Reproduce the rejected slicing donor's real native lifecycle failures, without a GPU.

This is a blocker receipt, not a candidate implementation or a qualification gate.
The supplied nine-patch source is verified and only disposable copies are modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile

PARTS = ('zig', 'tools', 'build.zig', 'build.zig.zon', '.zig-version',
         'README.md', 'CHANGELOG.md', 'RUNBOOK.md')
IGNORE = {'.git', '.zig-cache', 'zig-out', '__pycache__'}
COMMON = ['compile-glm-native-open', 'test-glm-prompts-host',
          'test-glm-protocol-host', 'test-glm-logprobs-host', 'test-glm-snapshots-host']
SAFETY = [
    'cancellation restores the preceding committed prefix',
    'retry after cancellation consumes the original layer input',
    'repeated twice-baseline observations converge to two',
    'the lane cancellation at cached zero drops its suspended chunk',
    'the first calibration call schedules one layer',
    'nonfinite calibration is refused',
    'allocation failure followed by calibration retry retains no orphan scratch',
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root):
    result = {}
    for name in PARTS:
        item = root / name
        paths = sorted(item.rglob('*')) if item.is_dir() else [item]
        for path in paths:
            if path.is_symlink():
                raise ValueError(f'linked source: {path}')
            if path.is_file() and not any(p in IGNORE for p in path.relative_to(root).parts):
                result[path.relative_to(root).as_posix()] = sha(path)
    return dict(sorted(result.items()))


def tree_digest(values):
    return hashlib.sha256(''.join(f'{name}\0{value}\n' for name, value in sorted(values.items())).encode()).hexdigest()


def copy_source(source, dest):
    dest.mkdir()
    for name in PARTS:
        item = source / name
        if item.is_dir():
            shutil.copytree(item, dest / name, ignore=shutil.ignore_patterns(*IGNORE))
        else:
            shutil.copy2(item, dest / name)


def replace(path, before, after):
    text = path.read_text()
    if text.count(before) != 1:
        raise ValueError(f'expected one source anchor in {path.name}: {before!r}')
    path.write_text(text.replace(before, after))


def resolve_reject(root):
    path = root / 'zig/src/families/glm/cuda_follow.zig'
    rejected = path.with_suffix('.zig.rej')
    actual = list(root.rglob('*.rej'))
    if actual != [rejected]:
        raise ValueError(f'unexpected rejected hunks: {actual}')
    addition = '\n'.join(line[1:] for line in rejected.read_text().splitlines()
                         if line.startswith('+') and not line.startswith('+++')) + '\n'
    if addition.count('pub fn chunkSliced(') != 1 or addition.count('pub fn cancelSlice(') != 1:
        raise ValueError('the rejected donor hunk changed')
    replace(path, '    fn cancelMedia(_: ?*anyopaque) bool {',
            addition + '\n    fn cancelMedia(_: ?*anyopaque) bool {')
    rejected.unlink()


def instrument(root, here):
    shutil.copy2(here / 'cancel_probe.zig', root / 'zig/glm_fairness_host.zig')
    targets = {
        'test-fairness-witness': ['fairness witness:', 'fairness control:'],
        'test-fairness-safety': ['fairness safety:'],
        'test-fairness-cancel-control': ['fairness safety: ' + SAFETY[0]],
        'test-fairness-ewma-control': ['fairness safety: ' + SAFETY[2]],
    }
    addition = '''
    const fair = b.createModule(.{ .root_source_file = b.path("zig/glm_fairness_host.zig"), .target = host, .optimize = .debug, .link_libc = true, .imports = &.{ .{ .name = "glm", .module = glm }, .{ .name = "cuda", .module = cuda }, .{ .name = "lanes", .module = lanes } } });
'''
    for name, filters in targets.items():
        strings = ', '.join(json.dumps(s) for s in filters)
        addition += f'    b.step("{name}", "Slicing donor blocker probe").dependOn(&b.addRunArtifact(b.addTest(.{{ .root_module = fair, .filters = &.{{ {strings} }} }})).step);\n'
    needle = '    const host = b.graph.host;'
    replace(root / 'zig/build/glm_contracts.zig', needle, needle + addition)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--donor', type=Path, required=True)
    parser.add_argument('--zig', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--cpu', default='apple_m1')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / 'source-manifest.json').read_text())
    source, donor, zig = (p.resolve(strict=True) for p in (args.source, args.donor, args.zig))
    if subprocess.check_output([str(zig), 'version'], text=True).strip() != '0.17.0':
        parser.error('Zig 0.17.0 is required')
    marker = source / 'HOME-LAB-SOURCES.json'
    if sha(marker) != manifest['marker_sha256'] or sha(donor) != manifest['donor']['sha256']:
        raise ValueError('source marker or donor checksum mismatch')
    before = files(source)
    if before != json.loads(marker.read_text())['source_files']:
        raise ValueError('immutable source inventory differs from its nine-patch marker')
    if tree_digest(before) != manifest['input_tree_sha256']:
        raise ValueError('immutable source tree differs')
    for name, expected in manifest['test_inputs'].items():
        if sha(here / name) != expected:
            raise ValueError(f'test input differs: {name}')
    work = args.work_dir.resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix='glm1104-fairness-')) / 'run'
    work.mkdir(parents=True, exist_ok=False)
    base, candidate = work / 'baseline', work / 'donor'
    copy_source(source, base)
    copy_source(source, candidate)
    commands = []
    flags = [f'-Dcpu={args.cpu}', '-j2', '--summary', 'all']

    def run(command, cwd, name, fail=False, required=()):
        print(f'{name}: starting', flush=True)
        log = work / name
        with log.open('w') as out:
            process = subprocess.Popen(command, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, TF_GLM_CLEAR_THINKING='0'), start_new_session=True)
            try:
                status = process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise RuntimeError(f'bounded command exceeded 600 seconds: {name}')
        output = log.read_text()
        if (status == 0) == fail:
            raise RuntimeError(f'unexpected exit {status}: {log}')
        if 'compilation errors' in output or ' crashed' in output or 'terminated with signal' in output:
            raise RuntimeError(f'fixture/compiler failure is not a contract failure: {log}')
        for needle in required:
            if needle not in output:
                raise RuntimeError(f'missing intended result {needle!r}: {log}')
        # Receipts retain source locations while making the local compiler/workspace paths portable.
        replacements = [(str(work), '<work>'), (str(zig.parent), '<zig-root>'), (str(donor), '<donor>')]
        for path, token in replacements:
            output = output.replace('/private' + path, token).replace(path, token)
        log.write_text(output)
        cmd = [str(v).replace(str(zig), '<zig>').replace(str(donor), '<donor>') for v in command]
        row = {'command': cmd, 'cwd': cwd.name, 'exit': status, 'log': name, 'log_sha256': sha(log),
               'summaries': re.findall(r'^Build Summary:.*$', output, re.MULTILINE)}
        commands.append(row)
        print(f'{name}: exit {status}; ' + ' '.join(row['summaries']), flush=True)

    def build(cwd, name, targets, **kwargs):
        run([str(zig), 'build', *targets, *flags], cwd, name, **kwargs)

    build(base, 'baseline-host.log', COMMON, required=['50/50 tests passed', 'compile-glm-native-open success'])
    run(['git', 'apply', '--reject', str(donor)], candidate, 'donor-apply.log', fail=True,
        required=['Applied patch zig/src/families/glm/cuda_engine.zig cleanly.', 'Rejected hunk #2.'])
    resolve_reject(candidate)
    for name, expected in manifest['resolved_donor_files'].items():
        if sha(candidate / name) != expected:
            raise ValueError(f'resolved donor differs: {name}')
    build(candidate, 'donor-host.log', COMMON, fail=True,
          required=['49/50 tests passed', 'expected 12, found 13', 'compile-glm-native-open success'])
    instrument(candidate, here)
    build(candidate, 'raw-donor-witness.log', ['test-fairness-witness'], fail=True,
          required=['return error.BadRange', '3/6 tests passed'])
    engine = candidate / 'zig/src/families/glm/cuda_engine.zig'
    replace(engine, '        var lo: usize = undefined;', '        var lo: usize = 0;')
    build(candidate, 'normalized-witness.log', ['test-fairness-witness'], required=['6/6 tests passed',
          'parity=1,0 pending=4096 pos=4096 snapshot=NotAtMark',
          'original state=0x200000 cancelled output=0x220000 retry input=0x220000'])
    build(candidate, 'normalized-safety.log', ['test-fairness-safety'], fail=True,
          required=['0/7 tests passed', *[f'fairness safety: {name}\' failed:' for name in SAFETY]])

    follow = candidate / 'zig/src/families/glm/cuda_follow.zig'
    old = follow.read_text()
    try:
        needle = '        _ = x.e.dropPartialOf(x.e.slot);'
        replace(follow, needle, '        try x.e.begin(null); // external control: resetting loses the completed prefix\n' + needle)
        build(candidate, 'reset-control.log', ['test-fairness-cancel-control'], fail=True,
              required=['expected 4096, found 0', '0/1 tests passed'])
    finally:
        follow.write_text(old)

    old = engine.read_text()
    try:
        replace(engine, 'c.scale * (0.8 + 0.2 * ms / predicted)', '0.8 * c.scale + 0.2 * ms / predicted')
        build(candidate, 'ewma-control.log', ['test-fairness-ewma-control'], required=['1/1 tests passed'])
    finally:
        engine.write_text(old)
    after = files(source)
    if after != before or sha(marker) != manifest['marker_sha256']:
        raise RuntimeError('immutable source changed during the run')
    result = {'status': 'blocked-no-production-patch', 'zig_version': '0.17.0',
              'source_manifest_sha256': sha(here / 'source-manifest.json'),
              'immutable_before_sha256': tree_digest(before), 'immutable_after_sha256': tree_digest(after),
              'commands': commands,
              'limitations': ['Recording CUDA/NCCL drivers; no GPU arithmetic or real peer execution.',
                              'No TP exactness, cache/restore, media slicing, or performance qualification.',
                              'The one-line lo initialization only exposes deeper donor faults; it is not a candidate fix.',
                              'Existing broader baseline of 4421 passes/18 skips/319 steps was supplied, not rerun here.']}
    (work / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'BLOCKED: reproduced; production patch withheld. receipt={work / "receipt.json"}', flush=True)


if __name__ == '__main__':
    main()
