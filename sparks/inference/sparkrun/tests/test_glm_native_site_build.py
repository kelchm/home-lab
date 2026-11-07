"""The revised native bundle verifies upstream and site patch inputs independently."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'mods/mia-tensorfold-glm53-1104-r2'
spec = importlib.util.spec_from_file_location('glm_site_inputs', MOD / 'inputs.py')
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


@pytest.fixture
def context(tmp_path):
    kit, mod = tmp_path / 'kit', tmp_path / 'mod'
    (kit / 'patches/engine').mkdir(parents=True)
    (kit / 'templates').mkdir()
    (mod / 'patches').mkdir(parents=True)
    (kit / 'patches/engine/0001-upstream.patch').write_bytes(b'upstream\n')
    (mod / 'patches/0200-site.patch').write_bytes(b'site\n')
    (kit / 'templates/glm-5.3-flash-vision.jinja').write_bytes(b'template\n')
    (mod / 'chat_template.jinja').write_bytes(b'template\n')
    lock = {'kit_patch_count': 1, 'kit_patches_sha256': hashlib.sha256(b'upstream\n').hexdigest(),
            'site_patch_count': 1, 'site_patches_sha256': hashlib.sha256(b'site\n').hexdigest()}
    (mod / 'upstream.lock.json').write_text(json.dumps(lock))
    return kit, mod, lock


def test_site_patches_follow_verified_upstream_inputs(context):
    kit, mod, _ = context
    inputs.stage(kit, mod)
    assert (kit / 'patches/engine/0200-site.patch').read_bytes() == b'site\n'


@pytest.mark.parametrize('drift', ['upstream', 'site', 'template', 'count', 'collision', 'link', 'linked-kit-directory'])
def test_unreviewed_inputs_cannot_enter_the_build(context, drift):
    kit, mod, lock = context
    if drift == 'upstream':
        (kit / 'patches/engine/0001-upstream.patch').write_bytes(b'changed')
    elif drift == 'site':
        (mod / 'patches/0200-site.patch').write_bytes(b'changed')
    elif drift == 'template':
        (mod / 'chat_template.jinja').write_bytes(b'changed')
    elif drift == 'count':
        lock['site_patch_count'] = 2
        (mod / 'upstream.lock.json').write_text(json.dumps(lock))
    elif drift == 'collision':
        # A site patch may not replace an upstream patch, even with matching bytes.
        (mod / 'patches/0200-site.patch').rename(mod / 'patches/0001-upstream.patch')
    elif drift == 'link':
        patch = mod / 'patches/0200-site.patch'
        outside = mod / 'outside'
        patch.rename(outside)
        patch.symlink_to(outside)
    else:
        directory = kit / 'patches/engine'
        outside = kit / 'real-engine'
        directory.rename(outside)
        directory.symlink_to(outside)
    with pytest.raises(ValueError):
        inputs.stage(kit, mod)
    assert not (kit / 'patches/engine/0200-site.patch').exists()


def test_optimized_interpreter_still_rejects_site_drift(context):
    import os
    kit, mod, _ = context
    (mod / 'patches/0200-site.patch').write_bytes(b'changed')
    result = subprocess.run([sys.executable, str(MOD / 'inputs.py'), str(kit), str(mod)],
                            env={**os.environ, 'PYTHONOPTIMIZE': '1'}, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'site: patch count or content differs' in result.stderr


def test_source_preparation_uses_the_locked_archive_not_a_dirty_checkout(tmp_path, monkeypatch):
    import difflib
    import os

    repository = tmp_path / 'repository'
    repository.mkdir()
    env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}

    def git(*args):
        return subprocess.run(['git', '-C', str(repository), *args], env=env,
                              capture_output=True, text=True, check=True).stdout.strip()

    git('init', '-q')
    (repository / 'state').write_text('before\n')
    git('add', 'state')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Fixture')
    revision = git('rev-parse', 'HEAD')
    (repository / 'state').write_text('unreviewed working-tree edit\n')
    (repository / 'untracked').write_text('must not enter the prepared source')
    kit, mod = tmp_path / 'kit', tmp_path / 'mod'
    (kit / 'patches/engine').mkdir(parents=True)
    (kit / 'templates').mkdir()
    (mod / 'patches').mkdir(parents=True)
    (kit / 'templates/glm-5.3-flash-vision.jinja').write_text('template')
    (mod / 'chat_template.jinja').write_text('template')

    def patch(path, before, after):
        path.write_text(''.join(difflib.unified_diff([before + '\n'], [after + '\n'],
                                                    fromfile='a/state', tofile='b/state')))

    upstream, site = kit / 'patches/engine/0001.patch', mod / 'patches/0200.patch'
    patch(upstream, 'before', 'upstream')
    patch(site, 'upstream', 'site')
    lock = {'engine_repository': 'example/engine', 'engine_revision': revision,
            'kit_repository': 'example/kit', 'kit_revision': 'a' * 40,
            'kit_patch_count': 1, 'kit_patches_sha256': inputs.patch_digest([upstream]),
            'site_patch_count': 1, 'site_patches_sha256': inputs.patch_digest([site])}
    (mod / 'upstream.lock.json').write_text(json.dumps(lock))
    monkeypatch.setitem(sys.modules, 'inputs', inputs)
    spec = importlib.util.spec_from_file_location('glm_site_source', MOD / 'source.py')
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    monkeypatch.setattr(source, 'HERE', mod)
    output = tmp_path / 'prepared'
    source.prepare(repository, kit, output)
    assert (output / 'state').read_text() == 'site\n'
    assert not (output / 'untracked').exists()
    assert (repository / 'state').read_text() == 'unreviewed working-tree edit\n'
    assert not (kit / 'patches/engine/0200.patch').exists()
    identity = json.loads((output / 'HOME-LAB-SOURCES.json').read_text())
    assert {key: identity[key] for key in lock} == lock
    assert identity['source_files'] == {}
    with pytest.raises(FileExistsError):
        source.prepare(repository, kit, output)


@pytest.mark.parametrize('drift', ['edit', 'new', 'link', 'pin'])
def test_prepared_native_source_cannot_silently_change(tmp_path, monkeypatch, drift):
    monkeypatch.setitem(sys.modules, 'inputs', inputs)
    spec = importlib.util.spec_from_file_location('glm_site_source', MOD / 'source.py')
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    (tmp_path / 'zig').mkdir()
    native = tmp_path / 'zig/state.zig'
    native.write_text('reviewed source')
    lock = {key: value for key, value in (
        ('engine_repository', 'example/engine'), ('engine_revision', 'a' * 40),
        ('kit_repository', 'example/kit'), ('kit_revision', 'b' * 40),
        ('kit_patch_count', 108), ('kit_patches_sha256', 'c' * 64),
        ('site_patch_count', 6), ('site_patches_sha256', 'd' * 64))}
    receipt = {**lock, 'source_files': source.source_files(tmp_path)}
    (tmp_path / 'HOME-LAB-SOURCES.json').write_text(json.dumps(receipt))
    source.verify_source(tmp_path, lock)
    if drift == 'edit':
        native.write_text('changed')
    elif drift == 'new':
        (tmp_path / 'zig/unreviewed.zig').write_text('new')
    elif drift == 'link':
        native.unlink()
        native.symlink_to(tmp_path / 'HOME-LAB-SOURCES.json')
    else:
        lock['engine_revision'] = 'e' * 40
    with pytest.raises(ValueError):
        source.verify_source(tmp_path, lock)
