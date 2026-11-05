"""Exercise the native GLM builder's image publication boundary without Docker or network."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'mods/mia-tensorfold-glm53-1104'
DAEMON = 'sha256:' + 'a' * 64
CONFIG = 'sha256:' + 'b' * 64


@pytest.fixture
def builder(tmp_path):
    mod = tmp_path / 'mod'
    shutil.copytree(MOD, mod, ignore=shutil.ignore_patterns('__pycache__'))
    kit = tmp_path / 'kit'
    (kit / 'patches/engine').mkdir(parents=True)
    (kit / 'templates').mkdir()
    (kit / 'patches/engine/0001-fixture.patch').write_bytes(b'fixture patch\n')
    shutil.copy(mod / 'chat_template.jinja', kit / 'templates/glm-5.3-flash-vision.jinja')
    lock = json.loads((mod / 'upstream.lock.json').read_text())
    lock.update(image_id=CONFIG, kit_patch_count=1, kit_patches_sha256=hashlib.sha256(b'fixture patch\n').hexdigest())
    (mod / 'upstream.lock.json').write_text(json.dumps(lock))
    subprocess.run([sys.executable, str(mod / 'prepare.py'), '--record'], check=True, capture_output=True)
    bins = tmp_path / 'bin'
    bins.mkdir()
    git = bins / 'git'
    git.write_text('#!' + sys.executable + '''
import os, pathlib, shutil, sys
args=sys.argv[1:]
if args[:2]==['init','-q']:
    shutil.copytree(os.environ['FAKE_KIT'],args[-1])
elif 'rev-parse' in args:
    print(os.environ['FAKE_REVISION'])
''')
    docker = bins / 'docker'
    docker.write_text('#!' + sys.executable + '''
import io,json,os,sys,tarfile
args=sys.argv[1:]
assert os.environ['DOCKER_CONTEXT']=='colima-tf-build'
with open(os.environ['FAKE_CALLS'],'a') as f: f.write(json.dumps(args)+'\\n')
if args[0]=='build':
    if os.environ.get('FAIL_BUILD'): sys.exit(1)
elif args[:2]==['image','inspect']:
    print(os.environ['FAKE_DAEMON'])
elif args[0]=='save':
    assert args[1]==os.environ['FAKE_DAEMON']
    data=json.dumps([{'Config': 'blobs/sha256/'+os.environ['FAKE_CONFIG'][7:]}]).encode()
    with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as t:
        m=tarfile.TarInfo('manifest.json'); m.size=len(data); t.addfile(m,io.BytesIO(data))
elif args[0]=='tag':
    assert args[1]==os.environ['FAKE_DAEMON']
elif args[:2]!=['image','rm']:
    raise AssertionError(args)
''')
    for path in (git, docker):
        path.chmod(0o755)
    calls = tmp_path / 'calls'
    env = {**os.environ, 'PATH': str(bins) + os.pathsep + os.environ['PATH'], 'FAKE_KIT': str(kit),
           'FAKE_REVISION': lock['kit_revision'], 'FAKE_CALLS': str(calls), 'FAKE_DAEMON': DAEMON, 'FAKE_CONFIG': CONFIG}

    def run(*args, **changes):
        result = subprocess.run(['bash', str(mod / 'build/build.sh'), *args], env={**env, **changes}, capture_output=True, text=True)
        recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return result, recorded

    return run, lock


def test_build_publishes_only_the_matching_immutable_daemon_id(builder):
    run, lock = builder
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert calls[0][:2] == ['build', '--platform']
    assert calls[2] == ['save', DAEMON]  # config digest comes from the inspected object, not its mutable tag
    assert calls[3] == ['tag', DAEMON, lock['image']]
    assert calls[4][:2] == ['image', 'rm']


@pytest.mark.parametrize('changes', [{'FAKE_CONFIG': 'sha256:' + 'c' * 64}, {'FAKE_DAEMON': 'invalid'}, {'FAIL_BUILD': '1'}])
def test_drift_or_build_inspection_failure_cannot_replace_locked_tag(builder, changes):
    run, _ = builder
    result, calls = run(**changes)
    assert result.returncode != 0
    assert not any(call[0] == 'tag' for call in calls)


def test_candidate_mode_retains_new_content_without_publishing_it(builder):
    run, _ = builder
    result, calls = run('--candidate', FAKE_CONFIG='sha256:' + 'c' * 64)
    assert result.returncode == 0, result.stderr
    assert not any(call[0] == 'tag' or call[:2] == ['image', 'rm'] for call in calls)
