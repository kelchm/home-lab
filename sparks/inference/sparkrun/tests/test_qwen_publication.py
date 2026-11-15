"""Failure injection through the real wrappers and frozen builders, without Docker/network."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TP1 = ROOT / "mods/mia-tensorfold-qwen38fn-tp1-656879fd"
TP2 = ROOT / "mods/mia-tensorfold-qwen38fn-v1.1-discovery"
PATCH = b"offline kit patch fixture\n"
BINARY = b"qualified rebuilt binary fixture\n"
PUBLISHED = b"published binary fixture\n"
ID = "sha256:" + "a" * 64

DOCKER = r'''
import io, json, os, sys, tarfile
from pathlib import Path
args = sys.argv[1:]
state_path = Path(os.environ['STATE'])
state = json.loads(state_path.read_text())
with open(os.environ['CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\n')
mode = os.environ.get('FAILURE', '')
def require(value):
    if not value: raise RuntimeError(args)
if args[0] == 'build':
    if '--output' in args:
        output = Path(args[args.index('--output')+1].split('dest=')[1])
        require(output != Path(os.environ['MOD']) / 'out')
        output.mkdir()
        (output / 'tensorfold-native').write_bytes(b'drift' if mode == 'mismatch' else b'qualified rebuilt binary fixture\n')
    else:
        tag = args[args.index('-t')+1]
        require(tag.startswith('home-lab-qwen-candidate:'))
        require(Path(args[args.index('-f')+1]).read_bytes() == (Path(os.environ['MOD']) / 'build/Dockerfile').read_bytes())
        state[tag] = os.environ['DAEMON_ID']
    state_path.write_text(json.dumps(state))
    if mode == 'build': sys.exit(10)
elif args[:4] == ['image', 'inspect', '--format', '{{.Id}}']:
    print(state[args[4]])
    if mode == 'retag': state[args[4]] = 'sha256:' + 'f'*64
elif args[:2] == ['image', 'save']:
    require(args[2] == os.environ['DAEMON_ID'])
    if mode == 'export': sys.exit(11)
    digest = ('sha256:' + 'f'*64 if mode == 'mismatch' else os.environ['CONFIG_ID']).removeprefix('sha256:')
    data = json.dumps([{'Config': 'blobs/sha256/' + digest}]).encode()
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
        entry = tarfile.TarInfo('manifest.json'); entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    if mode == 'export-late': sys.exit(12)
elif args[:2] == ['image', 'tag']:
    require(args[2] == os.environ['DAEMON_ID'] and args[3] == os.environ['TAG'])
    state[args[3]] = args[2]
elif args[:2] == ['image', 'rm']:
    require(args[2].startswith('home-lab-qwen-candidate:'))
    state.pop(args[2], None)
else:
    raise RuntimeError(args)
state_path.write_text(json.dumps(state))
'''
GIT = r'''
import os, sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == 'init':
    path = Path(args[-1]); path.mkdir(); (path / '.git').mkdir()
elif args[0] == '-C':
    path = Path(args[1]); op = args[2]
    if op == 'fetch':
        (path / '.revision').write_text(args[-1])
        if path.name == 'kit':
            (path / 'patches').mkdir()
            (path / 'patches/0001.patch').write_bytes(b'offline kit patch fixture\n')
    elif op == 'rev-parse': print((path / '.revision').read_text())
    elif op not in ('remote', 'checkout', 'apply'): raise RuntimeError(args)
else: raise RuntimeError(args)
'''
CURL = r'''
import os, shutil, sys
args = sys.argv[1:]
if '-o' in args: shutil.copyfile(os.environ['LAYER'], args[args.index('-o')+1])
else: print('{"token": "offline-token"}')
'''


class QwenPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / 'mock bin'
        self.bin.mkdir()
        for name, code in (('docker', DOCKER), ('git', GIT), ('curl', CURL)):
            path = self.bin / name
            path.write_text(f'#!{sys.executable}\n' + code)
            path.chmod(0o755)
        self.state = self.work / 'state.json'
        self.calls = self.work / 'calls.jsonl'
        self.env = {**os.environ, 'PATH': f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                    'STATE': str(self.state), 'CALLS': str(self.calls), 'DAEMON_ID': ID, 'FAILURE': ''}

    def setup_mod(self, source):
        self.mod = self.work / 'frozen mod'
        shutil.copytree(source, self.mod, ignore=shutil.ignore_patterns('out', '__pycache__'))
        self.lock = json.loads((self.mod / 'upstream.lock.json').read_text())
        self.env['MOD'] = str(self.mod)
        if source == TP1:
            patches = PATCH + b''.join(path.read_bytes() for path in sorted((self.mod / 'patches').glob('*.patch')))
            self.lock['kit_patches_sha256'] = hashlib.sha256(PATCH).hexdigest()
            self.lock['image'] = 'qwen-fixture:' + '-' + hashlib.sha256(patches).hexdigest()[:12]
            self.env.update(TAG=self.lock['image'], CONFIG_ID=self.lock['image_id'])
            self.state.write_text(json.dumps({self.lock['image']: self.lock['image_id']}))
        else:
            layer = self.work / 'layer.tar.gz'
            with tarfile.open(layer, 'w:gz') as archive:
                for name, data in (('opt/tensorfold/fatbin/kernel', b'fatbin fixture'),
                                   ('opt/tensorfold/native/bin/tensorfold-native', PUBLISHED)):
                    entry = tarfile.TarInfo(name); entry.size = len(data)
                    archive.addfile(entry, io.BytesIO(data))
            self.lock['build']['fatbin_layer'] = 'sha256:' + hashlib.sha256(layer.read_bytes()).hexdigest()
            self.lock['build']['published_binary_sha256'] = hashlib.sha256(PUBLISHED).hexdigest()
            self.lock['binary_sha256'] = hashlib.sha256(BINARY).hexdigest()
            self.env['LAYER'] = str(layer)
            self.state.write_text('{}')
            (self.mod / 'out').mkdir()
            (self.mod / 'out/tensorfold-native').write_bytes(BINARY)
            (self.mod / 'out/prior-export-receipt').write_text('preserve me')
        # Only artifact/upstream fixture pins differ; frozen sources/checksums remain unchanged.
        (self.mod / 'upstream.lock.json').write_text(json.dumps(self.lock))

    def run_build(self, kind, *args):
        return subprocess.run(['bash', str(ROOT / f'build-qwen-{kind}.sh'), str(self.mod), *args],
                              env=self.env, text=True, capture_output=True, timeout=30)

    def test_tp1_publishes_only_matching_immutable_image_after_export(self):
        self.setup_mod(TP1)
        self.env['FAILURE'] = 'retag'
        result = self.run_build('image')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.state.read_text()), {self.lock['image']: ID})
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertLess(next(i for i,c in enumerate(calls) if c[:2] == ['image', 'save']),
                        next(i for i,c in enumerate(calls) if c[:2] == ['image', 'tag']))
        self.assertIn(['image', 'save', ID], calls)
        self.assertIn(['image', 'tag', ID, self.lock['image']], calls)

    def test_tp1_mismatch_build_and_failed_exports_preserve_qualified_tag(self):
        self.setup_mod(TP1)
        for failure in ('mismatch', 'build', 'export', 'export-late'):
            with self.subTest(failure=failure):
                self.env['FAILURE'] = failure
                self.calls.unlink(missing_ok=True)
                result = self.run_build('image')
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(json.loads(self.state.read_text()), {self.lock['image']: self.lock['image_id']})
                calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
                self.assertFalse(any(c[:2] == ['image', 'tag'] for c in calls))

    def test_binary_success_preserves_previous_receipt_and_publishes_verified_binary(self):
        self.setup_mod(TP2)
        previous_inode = (self.mod / 'out/tensorfold-native').stat().st_ino
        result = self.run_build('binary')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual((self.mod / 'out/tensorfold-native').stat().st_ino, previous_inode)
        self.assertEqual((self.mod / 'out/tensorfold-native').read_bytes(), BINARY)
        self.assertEqual((self.mod / 'out/prior-export-receipt').read_text(), 'preserve me')
        self.assertEqual(list(self.work.glob('.qwen-binary-candidate.*')), [])

    def test_binary_mismatch_and_partial_failed_export_preserve_entire_output(self):
        self.setup_mod(TP2)
        before = {p.name: p.read_bytes() for p in (self.mod / 'out').iterdir()}
        for failure in ('mismatch', 'build'):
            self.env['FAILURE'] = failure
            result = self.run_build('binary')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual({p.name: p.read_bytes() for p in (self.mod / 'out').iterdir()}, before)
            self.assertEqual(list(self.work.glob('.qwen-binary-candidate.*')), [])

    def test_no_record_bypass_and_changed_frozen_inputs_rejected(self):
        self.setup_mod(TP2)
        result = self.run_build('binary', '--record')
        self.assertEqual(result.returncode, 2)
        (self.mod / 'build/build.sh').write_text('changed')
        result = self.run_build('binary')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())


if __name__ == '__main__':
    unittest.main()
