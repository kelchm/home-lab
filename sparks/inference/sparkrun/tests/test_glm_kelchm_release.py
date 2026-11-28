"""Off-host contract and lock tests for the kitless glm53-kelchm native release recipe."""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
from tensorfold import SERVE_OUTPUT, TensorFoldRuntime, hf_snapshot_path, _FABRIC_MASTER
from sparkrun.core.recipe import Recipe
from sparkrun.core.validation import ERROR
from sparkrun.orchestration.executors.docker import DockerExecutor
from sparkrun.runtimes._cluster_ops import ClusterContext

RELEASES = (
    pytest.param(
        "2026.11.27",
        23,
        {
            "TF_GLM_REQUEST_STARTS": "1",
        },
        (),
        id="2026.11.27",
    ),
    pytest.param(
        "2026.11.28",
        46,
        {
            "TF_GLM_REQUEST_STARTS": "marks",
            "TF_EXL3_PROMPT_GU": "pc",
            "TF_GLM_MM_FAST4": "1",
            "TF_GLM_SHARED_SLOT": "1",
            "TF_GLM_DENSE_PRUNE": "1",
            "TF_GLM_HC_FUSE": "1",
            "TF_GLM_ROUTER_FUSE": "1",
            "TF_GLM_CHUNK_SOLO": "4096",
            "TF_GLM_PROMPT_PAIR": "solo",
        },
        ("TF_GLM_EXL3_BF16_SLOTS", "TF_GLM_ABSORB_MMA"),
        id="2026.11.28",
    ),
)


def release_paths(version: str):
    mod = ROOT / f"mods/kelchm-tensorfold-glm53-{version}"
    profile = ROOT / f"kelchm-tensorfold-glm53-native-tp2/glm53-kelchm-{version}-agent-1m.yaml"
    learn = f"/cache/huggingface/glm53-kelchm-{version}-learn"
    return mod, profile, learn


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load("kelchm_release_validator", ROOT / "validate_locks.py")


def parse(command):
    output, _, command = command.partition("\n")
    assert output == SERVE_OUTPUT
    tokens = shlex.split(command)
    assert tokens[0] == "env"
    env = {}
    for token in tokens[1:]:
        if "=" not in token or token.startswith("/"):
            break
        key, _, value = token.partition("=")
        env[key] = value
    return env, tokens[len(env) + 1 :]


@pytest.mark.parametrize("version,carry_count,expected_env,forbidden_env", RELEASES)
def test_kelchm_native_recipe_validates_with_zero_errors_and_no_managed_comm_env(
    monkeypatch, version, carry_count, expected_env, forbidden_env
):
    assert importlib.metadata.version("sparkrun") == "0.4.2"
    mod, profile, learn = release_paths(version)

    def forbidden(*a, **kw):
        raise AssertionError("Off-host SDK test attempted a process or socket")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)

    recipe = Recipe(yaml.safe_load(profile.read_text()), source_path=str(profile))
    runtime = TensorFoldRuntime()
    issues = runtime.validate_recipe(recipe)
    assert not [issue for issue in issues if issue.severity == ERROR]
    assert not [issue for issue in issues if "managed-comm-env" in getattr(issue, "message", "").lower()]

    # Managed RDMA/NCCL fabric variables are left to SparkRun auto-detection, not hardcoded in recipe env.
    for managed in ("NCCL_IB_HCA", "NCCL_IB_GID_INDEX", "NCCL_SOCKET_IFNAME", "TF_ROCE_HCA", "TF_ROCE_GID_INDEX"):
        assert managed not in recipe.env

    assert recipe.mods == ["../mods/tensorfold-readiness-v2", f"../mods/{mod.name}"]
    assert all((profile.parent / m / "run.sh").is_file() for m in recipe.mods)
    assert recipe.post_exec == [
        "python3 /opt/tensorfold-readiness/readiness.py --model GLM-5.3-Flash --context 1048576 --lanes 4 --efforts max,high,low"
    ]
    assert recipe.metadata["carry_count"] == carry_count
    assert recipe.env["CHAT_TEMPLATE"] == "/opt/tensorfold-mod/chat_template.jinja"
    assert recipe.env["GLM_LEARN_ROOT"] == "/cache/huggingface"
    assert recipe.env["GLM_LEARN_DIR"] == learn
    assert recipe.env["TF_KV_PAGES"] == "on"
    assert recipe.env["TF_GLM_PRICED_WINDOWS"] == "0"
    assert recipe.env["TF_IMPRINT_VERIFY"] == "0"
    for key, value in expected_env.items():
        assert recipe.env[key] == value
    for key in forbidden_env:
        assert key not in recipe.env

    ctx = ClusterContext.build(
        runtime,
        ["192.0.2.1", "192.0.2.2"],
        recipe.container,
        "local-kelchm-test",
        recipe.env,
        None,
        None,
        dry_run=True,
        recipe=recipe,
    )
    script = DockerExecutor().generate_launch_script(
        image=recipe.container,
        container_name="local-kelchm-test",
        command="sleep infinity",
        env=ctx.all_env,
        volumes=ctx.volumes,
    )
    tokens = shlex.split(script)
    assert "WORLD_SIZE=2" in tokens
    assert "TF_GLM_ROCE_FUSE=1" in tokens
    for key, value in expected_env.items():
        assert f"{key}={value}" in tokens

    token = _FABRIC_MASTER.set("198.18.0.1")
    try:
        for rank in (0, 1):
            env, argv = parse(
                runtime.generate_node_command(
                    recipe,
                    {},
                    head_ip="192.0.2.1",
                    num_nodes=2,
                    node_rank=rank,
                    init_port=29511,
                    hosts=["192.0.2.1", "192.0.2.2"],
                )
            )
            assert argv == ["/usr/local/bin/tensorfold-entrypoint"]
            assert env["RANK"] == str(rank)
            assert env["FABRIC_MASTER"] == "198.18.0.1"
            assert env["MODEL_PATH"] == hf_snapshot_path(recipe.model, recipe.model_revision)
            assert env["DRAFTER"] == hf_snapshot_path(recipe.defaults["drafter"], recipe.defaults["drafter_revision"])
            assert runtime.native_rendezvous_port(recipe, {}, num_nodes=2, init_port=29511) == 29511
    finally:
        _FABRIC_MASTER.reset(token)


@pytest.mark.parametrize("version,carry_count,expected_env,forbidden_env", RELEASES)
def test_kelchm_mod_preserves_baked_entrypoint(version, carry_count, expected_env, forbidden_env):
    mod, _, _ = release_paths(version)
    run_sh = (mod / "run.sh").read_text()
    assert "/usr/local/bin/tensorfold-entrypoint" not in run_sh
    assert "install -m 0644 chat_template.jinja /opt/tensorfold-mod/chat_template.jinja" in run_sh
    assert not (mod / "serve.py").exists()


@pytest.mark.parametrize("version,carry_count,expected_env,forbidden_env", RELEASES)
@pytest.mark.parametrize("mutation", ["content", "symlink", "missing", "manifest", "prepare-link"])
def test_kelchm_frozen_mod_fails_before_installation(tmp_path, version, carry_count, expected_env, forbidden_env, mutation):
    mod, _, _ = release_paths(version)
    bundle = tmp_path / mod.name
    shutil.copytree(mod, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    check = [sys.executable, str(bundle / "prepare.py"), "--check"]
    subprocess.run(check, check=True, capture_output=True)
    if mutation == "content":
        (bundle / "chat_template.jinja").write_text("changed")
    elif mutation in ("symlink", "missing"):
        (bundle / "chat_template.jinja").unlink()
        if mutation == "symlink":
            (bundle / "chat_template.jinja").symlink_to(mod / "chat_template.jinja")
    elif mutation == "manifest":
        (bundle / "upstream.lock.json").unlink()
        (bundle / "upstream.lock.json").symlink_to(mod / "upstream.lock.json")
    else:
        (bundle / "chat_template.jinja").write_text("changed")
        (bundle / "prepare.py").unlink()
        (bundle / "prepare.py").symlink_to(mod / "prepare.py")
    assert subprocess.run(check, capture_output=True).returncode != 0


@pytest.mark.parametrize("version,carry_count,expected_env,forbidden_env", RELEASES)
def test_kelchm_native_lock_is_strict_and_bound_to_copied_release(tmp_path, version, carry_count, expected_env, forbidden_env):
    orig_mod, orig_profile, _ = release_paths(version)
    source = Path(
        shutil.copytree(ROOT, tmp_path / "source", ignore=shutil.ignore_patterns("__pycache__", "tests", "plugins", "benchmarks", "out"))
    )
    assert all(validator.validate_repository(source))

    mod = source / "mods" / orig_mod.name
    lock_path = mod / "upstream.lock.json"
    recipe_path = source / "kelchm-tensorfold-glm53-native-tp2" / orig_profile.name

    # Mutating carry_count in recipe metadata must fail validation.
    recipe = yaml.safe_load(recipe_path.read_text())
    recipe["metadata"]["carry_count"] = carry_count - 1
    recipe_path.write_text(yaml.safe_dump(recipe))
    with pytest.raises(validator.InvalidManifest, match="metadata.carry_count"):
        validator.validate_repository(source)
    recipe["metadata"]["carry_count"] = carry_count
    recipe_path.write_text(yaml.safe_dump(recipe))

    # Mutating engine_upstream to a zero pin or wrong commit must fail.
    lock = json.loads(lock_path.read_text())
    orig_upstream = lock["engine_upstream"]
    lock["engine_upstream"] = "ashhart/TensorFold@" + "0" * 40
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(validator.InvalidManifest, match="zero is not a dependency pin"):
        validator.validate_repository(source)
    lock["engine_upstream"] = orig_upstream
    lock_path.write_text(json.dumps(lock))

    # Mutating release.json without matching lock fields must fail even after updating release_sha256.
    rel = json.loads((mod / "release.json").read_text())
    rel["engine"]["commit"] = "a" * 40
    rel_bytes = (json.dumps(rel, indent=2) + "\n").encode()
    (mod / "release.json").write_bytes(rel_bytes)
    new_sha = hashlib.sha256(rel_bytes).hexdigest()
    lock["release_sha256"] = new_sha
    lock["mod_files"]["release.json"] = new_sha
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(validator.InvalidManifest, match="engine_revision.*disagrees"):
        validator.validate_repository(source)


@pytest.mark.parametrize("version,carry_count,expected_env,forbidden_env", RELEASES)
@pytest.mark.parametrize("keys,value,error", [
    (("recipe_commit",), "main", "release.recipe_commit"),
    (("recipe_commit",), "0" * 40, "release.recipe_commit.*zero"),
    (("build", "base_image"), "nvidia/cuda:latest", "release.build.base_image.*immutable"),
    (("build", "runtime_image"), "nvidia/cuda:latest", "release.build.runtime_image.*immutable"),
    (("build", "zig_sha256"), "0" * 64, "release.build.zig_sha256.*zero"),
])
def test_kelchm_release_mutable_build_or_recipe_pins_rejected_after_rehash(
    tmp_path, version, carry_count, expected_env, forbidden_env, keys, value, error
):
    orig_mod, _, _ = release_paths(version)
    source = Path(
        shutil.copytree(ROOT, tmp_path / "source", ignore=shutil.ignore_patterns("__pycache__", "tests", "plugins", "benchmarks", "out"))
    )
    mod = source / "mods" / orig_mod.name
    lock_path = mod / "upstream.lock.json"
    rel_path = mod / "release.json"

    rel = json.loads(rel_path.read_text())
    target = rel
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    rel_bytes = (json.dumps(rel, indent=2) + "\n").encode()
    rel_path.write_bytes(rel_bytes)
    new_sha = hashlib.sha256(rel_bytes).hexdigest()

    lock = json.loads(lock_path.read_text())
    lock["release_sha256"] = new_sha
    lock["mod_files"]["release.json"] = new_sha
    lock_path.write_text(json.dumps(lock))

    # Rehashed mod passes checksum verification alone, but semantic lock validation must refuse it.
    subprocess.run([sys.executable, str(mod / "prepare.py"), "--check"], check=True, capture_output=True)
    with pytest.raises(validator.InvalidManifest, match=error):
        validator.validate_repository(source)
