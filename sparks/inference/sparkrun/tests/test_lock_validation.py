"""Semantic rejection fixtures; all operations stay inside local temporary bundles."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("validate_locks", ROOT / "validate_locks.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

MODS = {
    "qwen1": "mia-tensorfold-qwen38fn-tp1-656879fd",
    "qwen2": "mia-tensorfold-qwen38fn-v1.1-discovery",
    "glm": "mia-tensorfold-glm53-bf5f9452",
    "deepseek": "bertholomus-tensorfold-dsv41-2970bcd-mia-fixes",
    "readiness": "tensorfold-readiness",
}


@pytest.fixture
def source(tmp_path):
    return Path(shutil.copytree(ROOT, tmp_path / "source", ignore=shutil.ignore_patterns(
        "__pycache__", "tests", "plugins", "benchmarks", "out")))


def manifest(root, kind):
    return root / "mods" / MODS[kind] / "upstream.lock.json"


def edit_json(path, keys, value):
    data = json.loads(path.read_text())
    target = data
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    path.write_text(json.dumps(data))


def recipe_for(root, kind):
    for path in root.glob("*/*.yaml"):
        if f"../mods/{MODS[kind]}" in yaml.safe_load(path.read_text())["mods"]:
            return path
    raise AssertionError(f"no recipe for {kind}")


def checksum_passes(path):
    result = subprocess.run([sys.executable, str(path.parent / "prepare.py"), "--check"],
                            check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Verified" in result.stdout


def test_current_reviewed_sources_pass_and_cli_is_collectable_by_ci():
    assert all(validator.validate_repository(ROOT))
    result = subprocess.run([sys.executable, str(ROOT / "validate_locks.py")], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Verified dependency pins and agreement" in result.stdout


# The audit's manifest-only edits still pass their frozen-file checker. Several
# cross-file edits were already caught by profile tests; this shared layer adds
# pin syntax/schema checks and applies agreement rules to every discovered owner.
@pytest.mark.parametrize("kind,keys,value,error", [
    ("qwen1", ("build", "base_image"), "nvcr.io/nvidia/pytorch:26.09-py3", "build.base_image"),
    ("qwen1", ("build", "pip_packages"), ["transformers", "av"], "exact-version"),
    ("qwen1", ("engine_revision",), "0" * 40, "engine_revision.*zero"),
    ("glm", ("launcher_env", "TF_GLM_FILL_ROWS"), "4096", "launcher_env.TF_GLM_FILL_ROWS.*disagrees"),
    ("glm", ("image",), "ghcr.io/example/glm:latest", "immutable named image"),
    ("glm", ("image_id",), "sha256:" + "0" * 64, "image_id.*zero"),
    ("glm", ("image",), "ghcr.io/example/glm@sha256:" + "a" * 64, "container.*disagrees"),
    ("deepseek", ("inputs_revision",), "main", "inputs_revision"),
    ("deepseek", ("kit", "revision"), "0" * 40, "kit.revision.*zero"),
    ("deepseek", ("kit", "bias_vl_sha256"), "0" * 64, "bias_vl_sha256.*zero"),
    ("deepseek", ("engram", "revision"), "1bae0e1", "engram.revision"),
    ("deepseek", ("engram", "files", "config.json"), "0" * 40, "config.json.*zero"),
    ("deepseek", ("image",), "dsv41-tf:latest", "local tag disagrees"),
])
def test_audit_bypasses_pass_checksums_but_fail_semantics(source, kind, keys, value, error):
    path = manifest(source, kind)
    edit_json(path, keys, value)
    checksum_passes(path)
    with pytest.raises(validator.InvalidManifest, match=error):
        validator.validate_repository(source)


@pytest.mark.parametrize("package", [
    "av>=19.0.1", "av==19.*", "av~=19.0", "av===19.0.1", "av==19.0oops", "av==19.0.1; python_version>'3'",
    "av @ https://example.com/av.whl", "--extra-index-url=https://example.com", "av==19.0.1 --upgrade",
])
def test_package_ranges_urls_markers_options_and_malformed_versions_are_refused(source, package):
    edit_json(manifest(source, "qwen1"), ("build", "pip_packages"), [package])
    with pytest.raises(validator.InvalidManifest, match="build.pip_packages.*exact-version"):
        validator.validate_repository(source)


@pytest.mark.parametrize("kind,keys,value,error", [
    ("qwen1", ("kit_revision",), "abcd1234", "kit_revision"),
    ("qwen1", ("server_patches", 0, "base"), "0" * 40, "base.*zero"),
    ("qwen2", ("server_patches", 0, "commit"), "main", "commit"),
    ("qwen2", ("build", "fatbin_layer"), "sha256:" + "a" * 12, "fatbin_layer"),
    ("qwen2", ("build", "published_binary_sha256"), "0" * 64, "published_binary_sha256.*zero"),
    ("qwen2", ("binary_sha256",), None, "binary_sha256"),
    ("qwen2", ("upstream_patch_hash",), "a" * 64, "upstream_patch_hash.*12-digit"),
    ("qwen1", ("build", "base_image"), "ubuntu@sha256:" + "0" * 64, "base_image.*zero"),
    ("glm", ("installed_files", "cuda/http.py", "upstream_sha256"), "abc", "upstream_sha256"),
    ("glm", ("launcher_env", "TF_GLM_MAX_QUEUED"), None, "launcher_env.*expected a string"),
    ("deepseek", ("engine_repository",), "https://github.com/example/engine", "engine_repository"),
    ("deepseek", ("engram", "files", "model-00047-of-00048.safetensors"), "a" * 40, "64-digit"),
    ("readiness", ("mod_files", "readiness.py"), "0" * 64, "mod_files.*zero"),
])
def test_declared_structured_pin_formats(source, kind, keys, value, error):
    edit_json(manifest(source, kind), keys, value)
    with pytest.raises(validator.InvalidManifest, match=error):
        validator.validate_repository(source)


@pytest.mark.parametrize("kind,keys", [
    ("qwen1", ("build",)), ("qwen2", ("image_id",)), ("glm", ("launcher_env",)),
    ("deepseek", ("kit", "bias_vl_sha256")), ("deepseek", ("engram", "revision")),
    ("qwen1", ("weights", "revision")), ("qwen1", ("server_patches", 0, "commit")),
    ("glm", ("installed_files", "cuda/http.py", "site_sha256")),
    ("glm", ("launcher_env", "TF_GLM_FILL_ROWS")), ("glm", ("installed_files", "cuda/http.py")),
])
def test_required_dependency_fields_cannot_disappear(source, kind, keys):
    path = manifest(source, kind)
    lock = json.loads(path.read_text())
    target = lock
    for key in keys[:-1]:
        target = target[key]
    del target[keys[-1]]
    path.write_text(json.dumps(lock))
    with pytest.raises(validator.InvalidManifest, match=f"missing.*{keys[-1]}"):
        validator.validate_repository(source)


@pytest.mark.parametrize("kind,keys,value,error", [
    ("deepseek", ("metadata", "engine_revision"), "a" * 40, "metadata.engine_revision"),
    ("deepseek", ("metadata", "image"), "dsv41-tf:other", "metadata.image"),
    ("deepseek", ("distribution_config", "models", "entries", 1, "revision"), "a" * 40, "distribution sources"),
    ("glm", ("defaults", "drafter_revision"), "main", "defaults.drafter_revision"),
    ("glm", ("metadata", "draft_model_revision"), "a" * 40, "metadata.draft_model_revision"),
    ("qwen1", ("metadata", "target_model_revision"), "a" * 40, "metadata.target_model_revision"),
    ("qwen1", ("defaults", "context"), 12345, "post_exec --context"),
    ("qwen2", ("defaults", "served_model_name"), "other", "post_exec --model"),
    ("qwen2", ("container",), "qwen:latest", "container"),
    ("glm", ("defaults", "entrypoint"), "/bin/other", "defaults.entrypoint"),
])
def test_recipe_contradictions(source, kind, keys, value, error):
    path = recipe_for(source, kind)
    recipe = yaml.safe_load(path.read_text())
    target = recipe
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    path.write_text(yaml.safe_dump(recipe))
    with pytest.raises(validator.InvalidManifest, match=error):
        validator.validate_repository(source)


def test_coordinated_zero_source_edit_is_not_saved_by_recipe_equality(source):
    path = recipe_for(source, "qwen1")
    recipe = yaml.safe_load(path.read_text())
    recipe["metadata"]["engine_revision"] = "0" * 40
    path.write_text(yaml.safe_dump(recipe))
    edit_json(manifest(source, "qwen1"), ("engine_revision",), "0" * 40)
    checksum_passes(manifest(source, "qwen1"))
    with pytest.raises(validator.InvalidManifest, match="engine_revision.*zero"):
        validator.validate_repository(source)


def test_semantics_do_not_authenticate_a_well_formed_coordinated_revision(source):
    path = recipe_for(source, "qwen1")
    recipe = yaml.safe_load(path.read_text())
    recipe["metadata"]["engine_revision"] = "a" * 40
    path.write_text(yaml.safe_dump(recipe))
    edit_json(manifest(source, "qwen1"), ("engine_revision",), "a" * 40)
    assert all(validator.validate_repository(source))


@pytest.mark.parametrize("alter_id,alter_name", [(True, False), (False, False), (False, True)])
def test_retained_image_reference_has_one_config_id_but_aliases_are_allowed(source, alter_id, alter_name):
    original = manifest(source, "deepseek")
    copied_mod = original.parent.with_name(original.parent.name + "-copy")
    shutil.copytree(original.parent, copied_mod)
    lock_path = copied_mod / "upstream.lock.json"
    lock = json.loads(lock_path.read_text())
    if alter_id:
        lock["image_id"] = "sha256:" + "a" * 64
    if alter_name:
        lock["image"] = "alias-" + lock["image"]
    lock_path.write_text(json.dumps(lock))
    path = recipe_for(source, "deepseek")
    recipe = yaml.safe_load(path.read_text())
    copy_path = path.with_stem(path.stem + "-copy")
    recipe["metadata"].update(profile=copy_path.stem, image=lock["image"])
    recipe["container"] = lock["image_id"]
    recipe["mods"][-1] = "../mods/" + copied_mod.name
    copy_path.write_text(yaml.safe_dump(recipe))
    checksum_passes(lock_path)
    if alter_id:
        with pytest.raises(validator.InvalidManifest, match="same reference, different config IDs"):
            validator.validate_repository(source)
    else:
        assert all(validator.validate_repository(source))


def test_unknown_locks_and_new_fields_are_not_silently_skipped(source):
    edit_json(manifest(source, "qwen1"), ("new_dependency",), "main")
    with pytest.raises(validator.InvalidManifest, match="unexpected.*new_dependency"):
        validator.validate_repository(source)


def test_a_new_checksum_only_mod_needs_an_explicit_supported_shape(source):
    other = source / "mods" / "unknown"
    shutil.copytree(manifest(source, "readiness").parent, other)
    with pytest.raises(validator.InvalidManifest, match="unsupported lock shape"):
        validator.validate_repository(source)


def test_missing_new_lock_and_missing_owning_recipe_are_reported(source):
    other = source / "mods" / "unknown"
    other.mkdir()
    with pytest.raises(validator.InvalidManifest, match="missing required upstream.lock.json"):
        validator.validate_repository(source)
    other.rmdir()
    recipe_for(source, "deepseek").unlink()
    with pytest.raises(validator.InvalidManifest, match="locks without owning recipes"):
        validator.validate_repository(source)


def test_deepseek_tag_agrees_with_sources_and_frozen_patch_hash(source):
    edit_json(manifest(source, "deepseek"), ("inputs_revision",), "a" * 40)
    checksum_passes(manifest(source, "deepseek"))
    with pytest.raises(validator.InvalidManifest, match="tag disagrees with engine/inputs"):
        validator.validate_repository(source)


@pytest.mark.parametrize("instruction", ["FROM", "  from"])
def test_deepseek_dockerfile_base_is_declared_outside_the_lock(source, instruction):
    path = manifest(source, "deepseek")
    dockerfile = path.parent / "build/Dockerfile"
    data = dockerfile.read_text()
    original = next(line for line in data.splitlines() if line.startswith("FROM "))
    dockerfile.write_text(data.replace(original, f"{instruction} nvcr.io/nvidia/pytorch:26.09-py3"))
    # A reviewed-manifest checksum refresh alone must not bless a mutable base.
    edit_json(path, ("mod_files", "build/Dockerfile"), hashlib.sha256(dockerfile.read_bytes()).hexdigest())
    checksum_passes(path)
    with pytest.raises(validator.InvalidManifest, match="Dockerfile FROM.*immutable"):
        validator.validate_repository(source)


def test_different_exact_package_versions_and_narrative_provenance_remain_reviewed_data(source):
    edit_json(manifest(source, "qwen1"), ("build", "pip_packages"), ["transformers==6.1.0rc1", "av==20.0.0"])
    path = manifest(source, "deepseek")
    lock = json.loads(path.read_text())
    first_patch = next(iter(lock["patches"]))
    edit_json(path, ("patches", first_patch), "local backport reviewed in an issue; not a structured hash field")
    assert all(validator.validate_repository(source))


def test_duplicate_json_fields_have_no_ambiguous_meaning(source):
    path = manifest(source, "qwen1")
    path.write_text(path.read_text().replace('{', '{"engine_revision":"main",', 1))
    with pytest.raises(validator.InvalidManifest, match="duplicate JSON field engine_revision"):
        validator.validate_repository(source)


def test_cli_fails_with_the_lock_path_and_bad_field(source):
    edit_json(manifest(source, "qwen1"), ("build", "pip_packages"), ["av"])
    result = subprocess.run([sys.executable, str(ROOT / "validate_locks.py"), "--root", str(source)],
                            check=False, capture_output=True, text=True)
    assert result.returncode != 0
    assert f"mods/{MODS['qwen1']}/upstream.lock.json" in result.stderr
    assert "build.pip_packages" in result.stderr
