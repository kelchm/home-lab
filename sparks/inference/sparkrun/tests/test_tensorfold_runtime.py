"""TensorFold SparkRun runtime adapter — discovery, topology, rendezvous, cache, health."""

from __future__ import annotations

import hashlib
import json
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
if str(PLUGINS) not in sys.path:
    sys.path.insert(0, str(PLUGINS))

from tensorfold import REQUIRED_DEFAULTS, TensorFoldRuntime, hf_snapshot_path  # noqa: E402

from sparkrun.core.bootstrap import get_runtime, init_sparkrun  # noqa: E402
from sparkrun.core.external_plugins import load_external_plugins  # noqa: E402
from sparkrun.core.log_source import MODE_FILE, SCOPE_ALL  # noqa: E402
from sparkrun.core.mods import resolve_and_inject_mods  # noqa: E402
from sparkrun.core.recipe import Recipe  # noqa: E402
from sparkrun.core.runtime_cache import (  # noqa: E402
    RUNTIME_CACHE_CONTAINER_PATH,
    build_runtime_cache_mounts,
    resolve_runtime_cache_root,
    resolve_runtime_cache_settings,
)
from sparkrun.core.validation import ERROR  # noqa: E402
from sparkrun.orchestration.job_metadata import derive_recipe_fingerprint, generate_intent_id  # noqa: E402
from sparkrun.orchestration.executors.docker import DockerExecutor  # noqa: E402
from sparkrun.runtimes import _cluster_ops  # noqa: E402
from sparkrun.runtimes._cluster_ops import ClusterContext  # noqa: E402

TARGET = "Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw"
TARGET_REV = "25a44fdbf16862a46b7cc9921142c6c81350af2f"
DRAFT = "incoai/GLM-5.3-Flash-DFlash2"
DRAFT_REV = "dc77ff1c99eeb2df044ee3d4f0094eb033fee410"
MIA_RECIPE_DIR = "mia-tensorfold-glm53-exl3-tp2"
MIA_PROFILES = [
    ("glm53-exl3-tp2-mia-tf-v1.2-bmm-850k", TARGET, TARGET_REV, "850000"),
    ("glm53-exl3-tp2-mia-tf-v1.5-bmm-850k", TARGET, TARGET_REV, "850000"),
    ("glm53-exl3-tp2-mia-tf-v1.5-mia-1m", "Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold",
     "078455ffe6472f9a52fbc1139f58b9db2881b25c", "1048576"),
    ("glm53-exl3-tp2-mia-tf-v1.8-bmm-850k", TARGET, TARGET_REV, "850000"),
    ("glm53-exl3-tp2-mia-tf-v1.8-mia-1m", "Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold",
     "078455ffe6472f9a52fbc1139f58b9db2881b25c", "1048576"),
    ("glm53-exl3-tp2-mia-tf-v1.10-mia-1m", "Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold",
     "078455ffe6472f9a52fbc1139f58b9db2881b25c", "1048576"),
]
QWEN_RECIPE_DIR = "mia-tensorfold-qwen38fn-tp2"
QWEN_PROFILES = [
    ("qwen38fn-nvfp4-tp2-mia-tf-v1.1-nvidia-1m", "nvidia/Qwen3.8-Flash-Next-NVFP4",
     "fc694b54fb0174e0913e6adf86691ef85a4ead47", "Qwen3.8-Flash-Next"),
    ("qwen38fn-int4ar-tp2-mia-tf-v1.1-azampatti-1m", "azampatti/Qwen3.8-Flash-Next-125B-A5B-INT4-AutoRound",
     "1464274120d36a4d8fcaa934552334a7d83ce0fd", "Qwen3.8-Flash-Next-INT4-AR"),
]
QWEN_DISCOVERY_PROFILE = "qwen38fn-nvfp4-tp2-mia-tf-v1.1-discovery-nvidia-1m"
QWEN_DISCOVERY_MOD = "mia-tensorfold-qwen38fn-v1.1-discovery"
QWEN_DISCOVERY_ENTRY = {
    "id": "Qwen3.8-Flash-Next", "object": "model", "created": 0, "owned_by": "tensorfold",
    "context_length": 1048576, "max_model_len": 1048576,
    "reasoning": {"default_enabled": True, "supported_efforts": ["xhigh", "medium", "low", "none"]},
}
MIA_MODS = Path(__file__).resolve().parents[1] / "mods"
FABRIC_HEAD = "172.16.21.31"
FABRIC_WORKER = "172.16.21.32"
ENTRYPOINT = "/usr/local/bin/tf-test-entrypoint"
SERVED = "test-model"
CONTEXT = "850000"


def _recipe(defaults=None, **extra):
    data = {
        "recipe_version": "2",
        "name": "tf",
        "model": TARGET,
        "runtime": "tensorfold",
        "min_nodes": 2,
        "max_nodes": 2,
        "model_revision": TARGET_REV,
        "defaults": {
            "tensor_parallel": 2,
            "pipeline_parallel": 1,
            "port": 8888,
            "host": "0.0.0.0",
            "served_model_name": SERVED,
            "context": int(CONTEXT),
            "drafter": DRAFT,
            "drafter_revision": DRAFT_REV,
            "max_tokens": 65536,
            "entrypoint": ENTRYPOINT,
        },
    }
    data.update(extra)
    if defaults is not None:
        merged = dict(data["defaults"])
        merged.update(defaults)
        data["defaults"] = merged
    return Recipe.from_dict(data)


def _parse_env_command(command: str) -> tuple[dict[str, str], list[str]]:
    tokens = shlex.split(command)
    assert tokens, command
    assert tokens[0] == "env", command
    env: dict[str, str] = {}
    argv: list[str] = []
    for token in tokens[1:]:
        if argv or "=" not in token or token.startswith("-"):
            argv.append(token)
            continue
        key, _, value = token.partition("=")
        if not key or not key.replace("_", "").isalnum() or not key.isupper():
            argv.append(token)
            continue
        env[key] = value
    return env, argv


def _node_command(runtime, recipe, rank, *, head_ip=FABRIC_HEAD, init_port=29500, hosts=None, overrides=None):
    return runtime.generate_node_command(
        recipe,
        overrides or {},
        head_ip=head_ip,
        num_nodes=2,
        node_rank=rank,
        init_port=init_port,
        hosts=hosts or [FABRIC_HEAD, FABRIC_WORKER],
    )


@pytest.fixture
def runtime():
    return TensorFoldRuntime()


def test_plugin_discovery_via_external_loader(monkeypatch, tmp_path):
    monkeypatch.setenv("SPARKRUN_NO_EXTERNAL_PLUGINS", "1")
    monkeypatch.setenv("STATEFUL_ROOT", str(tmp_path / "stateful"))
    from scitrera_app_framework.core.plugins import _multi_ext_options

    from sparkrun.runtimes.base import EXT_RUNTIME

    v = init_sparkrun()
    eor = _multi_ext_options(EXT_RUNTIME, v)
    before = set(eor)
    loaded = load_external_plugins(v, paths=[PLUGINS])
    try:
        assert "tensorfold" in loaded
        plugin = get_runtime("tensorfold", v)
        assert isinstance(plugin, TensorFoldRuntime)
        assert plugin.runtime_name == "tensorfold"
        assert plugin.cluster_strategy() == "native"
        assert plugin.prefer_ib_for_init_addr() is True
        assert plugin.native_rendezvous_port(_recipe(), {}, num_nodes=2, init_port=29500) == 29500
    finally:
        for name in set(eor) - before:
            eor.pop(name, None)


def test_rank_commands_propagate_fabric_rendezvous(runtime):
    recipe = _recipe()
    rank0_env, rank0_argv = _parse_env_command(_node_command(runtime, recipe, 0, init_port=29511))
    rank1_env, rank1_argv = _parse_env_command(_node_command(runtime, recipe, 1, init_port=29511))

    assert rank0_argv == [ENTRYPOINT]
    assert rank1_argv == [ENTRYPOINT]
    assert rank0_env["RANK"] == "0"
    assert rank1_env["RANK"] == "1"
    assert rank0_env["MASTER"] == FABRIC_HEAD
    assert rank1_env["MASTER"] == FABRIC_HEAD
    assert rank0_env["MASTER_PORT"] == "29511"
    assert rank1_env["MASTER_PORT"] == rank0_env["MASTER_PORT"]
    assert "10.32.21.31" not in rank0_env["MASTER"]
    assert rank0_env["MODEL_PATH"] == hf_snapshot_path(TARGET, TARGET_REV)
    assert rank0_env["DRAFTER"] == hf_snapshot_path(DRAFT, DRAFT_REV)
    assert rank0_env["CONTEXT"] == CONTEXT
    assert rank0_env["SERVED_NAME"] == SERVED
    assert "FABRIC_MASTER" not in rank0_env
    assert rank0_env["HOST"] == "0.0.0.0"
    assert rank0_env["PORT"] == "8888"
    assert rank0_env["MAX_TOKENS"] == "65536"
    assert rank0_env["MODEL_PATH"].startswith("/cache/huggingface/hub/")
    assert "snapshots/" in rank0_env["MODEL_PATH"]
    assert rank0_env["MODEL_PATH"] == rank1_env["MODEL_PATH"]
    assert "--tensor-parallel-size" not in rank0_argv
    assert "vllm" not in rank0_argv[0]


def test_overrides_and_recipe_command_template_skip_vllm_flags(runtime):
    recipe = _recipe(
        defaults={
            "model_path": "/cache/huggingface/hub/custom-snap",
            "context": 4096,
            "entrypoint": ENTRYPOINT,
        },
        command="{entrypoint}",
    )
    env, argv = _parse_env_command(
        _node_command(runtime, recipe, 0, overrides={"port": 9999, "served_model_name": SERVED})
    )
    assert argv == [ENTRYPOINT]
    assert env["MODEL_PATH"] == "/cache/huggingface/hub/custom-snap"
    assert env["CONTEXT"] == "4096"
    assert env["PORT"] == "9999"
    joined = " ".join(argv)
    assert "--gpu-memory-utilization" not in joined
    assert "--distributed-executor-backend" not in joined
    assert "vllm serve" not in joined


def test_unsupported_topology_rejected(runtime):
    with pytest.raises(ValueError, match="two nodes"):
        runtime.generate_node_command(_recipe(), {}, head_ip=FABRIC_HEAD, num_nodes=1, node_rank=0)
    with pytest.raises(ValueError, match="two nodes"):
        runtime.generate_node_command(_recipe(), {}, head_ip=FABRIC_HEAD, num_nodes=3, node_rank=0)
    with pytest.raises(ValueError, match="RANK"):
        runtime.generate_node_command(_recipe(), {}, head_ip=FABRIC_HEAD, num_nodes=2, node_rank=2)
    with pytest.raises(ValueError, match="tensor_parallel"):
        runtime.generate_node_command(_recipe(defaults={"tensor_parallel": 1}), {}, head_ip=FABRIC_HEAD, num_nodes=2, node_rank=0)
    with pytest.raises(ValueError, match="pipeline_parallel"):
        runtime.generate_node_command(_recipe(defaults={"pipeline_parallel": 2}), {}, head_ip=FABRIC_HEAD, num_nodes=2, node_rank=0)
    with pytest.raises(ValueError, match="data_parallel"):
        runtime.generate_node_command(_recipe(defaults={"data_parallel": 2}), {}, head_ip=FABRIC_HEAD, num_nodes=2, node_rank=0)
    with pytest.raises(ValueError, match="solo"):
        runtime.generate_command(_recipe(), {}, is_cluster=False)

    issues = runtime.validate_recipe(_recipe(min_nodes=1, max_nodes=1, defaults={"tensor_parallel": 1}))
    errors = [issue for issue in issues if getattr(issue, "severity", None) == ERROR]
    assert errors
    assert any("two nodes" in issue.message for issue in errors)


def test_prepare_distributes_declared_drafter(runtime):
    recipe = _recipe()
    before = {entry.name for entry in recipe.distribution_config.models.entries}
    runtime.prepare(recipe, [FABRIC_HEAD, FABRIC_WORKER])
    names = {entry.name: entry.revision for entry in recipe.distribution_config.models.entries}
    assert DRAFT in names
    assert names[DRAFT] == DRAFT_REV
    assert DRAFT not in before


@pytest.mark.parametrize("profile,target,revision,context", MIA_PROFILES)
def test_mia_recipe_reuses_native_rank_wiring(runtime, profile, target, revision, context):
    root = Path(__file__).resolve().parents[1] / MIA_RECIPE_DIR
    path = root / f"{profile}.yaml"
    recipe = Recipe(yaml.safe_load(path.read_text()), source_path=str(path))
    assert recipe.name == recipe.metadata["profile"] == profile
    assert not [issue for issue in runtime.validate_recipe(recipe) if getattr(issue, "severity", None) == ERROR]
    # The rank command inherits these settings from its container.
    ctx = ClusterContext.build(
        runtime, [FABRIC_HEAD, FABRIC_WORKER], recipe.container, "tf-profile-test",
        recipe.env, None, None, dry_run=True, recipe=recipe,
    )
    script = DockerExecutor().generate_launch_script(
        image=recipe.container, container_name="tf-profile-test", command="sleep infinity",
        env=ctx.all_env, volumes=ctx.volumes,
    )
    release = recipe.metadata["upstream_release"]
    if release in ("v1.8", "v1.10") and recipe.metadata["weights"] == "mia":
        assert "NCCL_CUMEM_ENABLE" not in script
    else:
        assert "NCCL_CUMEM_ENABLE=0" in shlex.split(script)
    assert ("TENSORFOLD_NUCLEUS_UNION=1" in shlex.split(script)) is (release == "v1.10")
    if release in ("v1.8", "v1.10"):
        assert "TF_ROCE_WAIT_S=300" in shlex.split(script)
        if recipe.metadata["weights"] == "mia":
            assert "TF_GLM_MAX_QUEUED=" in shlex.split(script)
            assert "TF_GLM_DISPLAY_KV_MIB=0" in shlex.split(script)
        else:
            assert "TF_GLM_MAX_QUEUED" not in script
    spec = importlib.util.spec_from_file_location("mia_serve", root / recipe.mods[0] / "serve.py")
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    for rank in (0, 1):
        env, argv = _parse_env_command(_node_command(runtime, recipe, rank, init_port=29511))
        assert argv == ["/usr/local/bin/glm53-mia-tf-entrypoint"]
        assert env["MODEL_PATH"] == hf_snapshot_path(target, revision)
        assert env["DRAFTER"] == hf_snapshot_path(DRAFT, "bf582e4eacc1810f76656d1811693ff6c6737d2a")
        args = serve.command(env)
        assert args[:3] == ["tensorfold", "serve", env["MODEL_PATH"]]
        for flag, expected in (("--tp", "2"), ("--rank", str(rank)), ("--master", FABRIC_HEAD),
                               ("--master-port", "29511"), ("--context", context),
                               ("--parallel", "4"), ("--max-tokens", "32768")):
            assert args[args.index(flag) + 1] == expected
        assert ("--name" in args) is (rank == 0)
        assert "--vision" in args and "--thinking" in args
        assert not [arg for arg in args if arg.startswith(("--spill", "--snapshot-dir", "--mtp", "--no-drafts"))]
    assert recipe.runtime_cache["key_by_image"] is True


def test_mia_profiles_have_separate_native_cache_namespaces(runtime):
    base = Path(__file__).resolve().parents[1]
    paths = [base / MIA_RECIPE_DIR / f"{row[0]}.yaml" for row in MIA_PROFILES]
    paths += [base / QWEN_RECIPE_DIR / f"{row[0]}.yaml" for row in QWEN_PROFILES]
    leaves = []
    for path in paths:
        recipe = Recipe.from_dict(yaml.safe_load(path.read_text()))
        mounts = build_runtime_cache_mounts(
            runtime=runtime, recipe=recipe,
            settings=resolve_runtime_cache_settings(runtime=runtime, recipe=recipe),
            root="/opt/spark-cache/huggingface/runtime-cache", image=recipe.container,
        )
        leaves.append(mounts.leaf)
    assert len(set(leaves)) == len(paths)


@pytest.mark.parametrize("profile,target,revision,context", MIA_PROFILES)
def test_mia_native_mod_transfer_is_self_contained(tmp_path, profile, target, revision, context):
    root = Path(__file__).resolve().parents[1] / MIA_RECIPE_DIR
    path = root / f"{profile}.yaml"
    recipe = Recipe(yaml.safe_load(path.read_text()), source_path=str(path))
    fingerprint = derive_recipe_fingerprint(recipe)
    registry = mock.Mock()
    resolve_and_inject_mods(recipe, registry)
    registry.get_registry.assert_not_called()
    copy, command = recipe.pre_exec
    assert copy["dest"] == "/workspace/mods/" + Path(recipe.mods[0]).name
    assert copy["dest"] in command
    assert derive_recipe_fingerprint(recipe) == fingerprint
    bundle = tmp_path / Path(copy["copy"]).name
    shutil.copytree(copy["copy"], bundle, ignore=shutil.ignore_patterns("__pycache__"))
    lock = json.loads((bundle / "upstream.lock.json").read_text())
    assert lock["image"] == recipe.container
    subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], cwd=tmp_path, check=True)

    spec = importlib.util.spec_from_file_location("mia_readiness", bundle / "readiness.py")
    ready = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ready)
    hook = shlex.split(recipe.post_exec[0])
    expected_context = int(hook[hook.index("--context") + 1])
    body = {"ok": True, "mode": "strict", "streams": {"max": 4},
            "context_length": int(context), "pool_tokens": 1200000}
    ready.check_health(body, 4, expected_context, 1200000)
    with pytest.raises(RuntimeError, match="context"):
        ready.check_health({**body, "context_length": int(context) - 1}, 4, expected_context, 1200000)


@pytest.mark.parametrize("version", ("v1.2", "v1.5", "v1.8", "v1.10"))
@pytest.mark.parametrize("change", ("content", "symlink"))
def test_mia_frozen_bundle_rejects_changed_helpers(tmp_path, version, change):
    source = Path(__file__).resolve().parents[1] / f"mods/mia-tensorfold-{version}"
    bundle = tmp_path / source.name
    shutil.copytree(source, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    helper = bundle / "readiness.py"
    if change == "content":
        helper.write_text(helper.read_text() + "\n# Unreviewed change\n")
    else:
        outside = tmp_path / "shared-readiness.py"
        helper.rename(outside)
        helper.symlink_to(outside)
    result = subprocess.run([sys.executable, str(bundle / "prepare.py")], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Missing or changed frozen mod file: readiness.py" in result.stderr


@pytest.mark.parametrize("profile,target,revision,served", QWEN_PROFILES)
def test_qwen_recipe_maps_native_rank_wiring_to_zig_cli(runtime, tmp_path, profile, target, revision, served):
    root = Path(__file__).resolve().parents[1] / QWEN_RECIPE_DIR
    path = root / f"{profile}.yaml"
    recipe = Recipe(yaml.safe_load(path.read_text()), source_path=str(path))
    assert recipe.name == recipe.metadata["profile"] == profile
    assert not [issue for issue in runtime.validate_recipe(recipe) if getattr(issue, "severity", None) == ERROR]
    ctx = ClusterContext.build(
        runtime, [FABRIC_HEAD, FABRIC_WORKER], recipe.container, "tf-profile-test",
        recipe.env, None, None, dry_run=True, recipe=recipe,
    )
    script = shlex.split(DockerExecutor().generate_launch_script(
        image=recipe.container, container_name="tf-profile-test", command="sleep infinity",
        env=ctx.all_env, volumes=ctx.volumes,
    ))
    for setting in ("TF_FLASHNEXT_YARN=4", "TF_FLASHNEXT_ROCE=1", "TENSORFOLD_MEMORY_RESERVE_GIB=10",
                    "NCCL_MIN_NCHANNELS=4", "NCCL_MAX_NCHANNELS=4"):
        assert setting in script
    mod = root / recipe.mods[0]
    lock = json.loads((mod / "upstream.lock.json").read_text())
    assert lock["image"] == recipe.container
    assert lock["kit_revision"] == recipe.metadata["kit_revision"]
    bundle = tmp_path / mod.name
    shutil.copytree(mod, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], cwd=tmp_path, check=True)
    spec = importlib.util.spec_from_file_location("qwen_serve", bundle / "serve.py")
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    for rank in (0, 1):
        env, argv = _parse_env_command(_node_command(runtime, recipe, rank, init_port=29511))
        assert argv == ["/usr/local/bin/qwen38fn-mia-tf-entrypoint"]
        assert "DRAFTER" not in env
        # The rank command inherits the recipe env from its container.
        args = serve.command({**recipe.env, **env})
        assert args[:3] == ["tensorfold-native", "serve", hf_snapshot_path(target, revision)]
        for flag, expected in (("--name", served), ("--port", "8888"), ("--context", "1048576"),
                               ("--parallel", "16"), ("--max-tokens", "32768"), ("--kv-dtype", "fp8"),
                               ("--vision-max-images", "50"), ("--vision-max-videos", "4"),
                               ("--vision-image-tokens", "16384"), ("--tp", "2"), ("--rank", str(rank)),
                               ("--master", FABRIC_HEAD), ("--master-port", "29511")):
            assert args[args.index(flag) + 1] == expected
        assert "--vision" in args and "--thinking" in args and "--no-drafts" not in args

    spec = importlib.util.spec_from_file_location("qwen_readiness", bundle / "readiness.py")
    ready = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ready)
    hook = shlex.split(recipe.post_exec[0])
    assert hook[hook.index("--model") + 1] == served
    body = {"status": "ok", "warming": False, "model": served, "max_batch_size": 16}
    ready.check_health(body, served, 16)
    for change in ({"status": "error"}, {"warming": True}, {"model": "other"}, {"max_batch_size": 8}):
        with pytest.raises(RuntimeError):
            ready.check_health({**body, **change}, served, 16)


def test_qwen_frozen_bundle_rejects_changed_helpers(tmp_path):
    source = Path(__file__).resolve().parents[1] / "mods/mia-tensorfold-qwen38fn-v1.1"
    bundle = tmp_path / source.name
    shutil.copytree(source, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    helper = bundle / "serve.py"
    helper.write_text(helper.read_text() + "\n# Unreviewed change\n")
    result = subprocess.run([sys.executable, str(bundle / "prepare.py")], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Missing or changed frozen mod file: serve.py" in result.stderr


def test_qwen_discovery_profile_is_the_v11_workload_with_its_own_mod(runtime):
    root = Path(__file__).resolve().parents[1] / QWEN_RECIPE_DIR
    base = yaml.safe_load((root / f"{QWEN_PROFILES[0][0]}.yaml").read_text())
    data = yaml.safe_load((root / f"{QWEN_DISCOVERY_PROFILE}.yaml").read_text())
    recipe = Recipe(data, source_path=str(root / f"{QWEN_DISCOVERY_PROFILE}.yaml"))
    assert recipe.name == recipe.metadata["profile"] == QWEN_DISCOVERY_PROFILE
    lock = json.loads((MIA_MODS / QWEN_DISCOVERY_MOD / "upstream.lock.json").read_text())
    assert data["metadata"].pop("server_patch_count") == len(lock["server_patches"]) == 2
    assert data.pop("mods") == [f"../mods/{QWEN_DISCOVERY_MOD}"] and base.pop("mods")
    hook = shlex.split(data.pop("post_exec")[0])
    assert hook[:-2] == shlex.split(base.pop("post_exec")[0])
    assert hook[-2:] == ["--context", str(data["defaults"]["context"])]
    for key in ("profile", "description"):
        assert data["metadata"].pop(key) != base["metadata"].pop(key)
    # Everything SparkRun addresses a workload and keys its runtime cache by is the v1.1 recipe's.
    assert data == base
    original = Recipe(yaml.safe_load((root / f"{QWEN_PROFILES[0][0]}.yaml").read_text()))
    assert generate_intent_id(recipe) == generate_intent_id(original)
    leaves = {build_runtime_cache_mounts(
        runtime=runtime, recipe=each, settings=resolve_runtime_cache_settings(runtime=runtime, recipe=each),
        root="/opt/spark-cache/huggingface/runtime-cache", image=each.container).leaf for each in (recipe, original)}
    assert len(leaves) == 1
    for key in ("image", "image_id", "kit_revision", "engine_revision", "upstream_patch_hash"):
        assert lock[key] == json.loads((MIA_MODS / "mia-tensorfold-qwen38fn-v1.1/upstream.lock.json").read_text())[key]
    assert [patch["paths"] for patch in lock["server_patches"]] == [
        ["zig/src/server/status_routes.zig"], ["zig/src/server/server.zig", "zig/src/server/status_routes.zig"]]


def _qwen_discovery_bundle(tmp_path, binary=None):
    bundle = tmp_path / QWEN_DISCOVERY_MOD
    shutil.copytree(MIA_MODS / QWEN_DISCOVERY_MOD, bundle, ignore=shutil.ignore_patterns("__pycache__", "out"))
    if binary is not None:
        (bundle / "out").mkdir()
        (bundle / "out/tensorfold-native").write_bytes(binary)
        lock = json.loads((bundle / "upstream.lock.json").read_text())
        lock["binary_sha256"] = hashlib.sha256(binary).hexdigest()
        lock["build"]["published_binary_sha256"] = hashlib.sha256(b"published").hexdigest()
        (bundle / "upstream.lock.json").write_text(json.dumps(lock))
    return bundle


def test_qwen_discovery_bundle_installs_only_over_the_published_binary(tmp_path):
    bundle = _qwen_discovery_bundle(tmp_path, b"rebuilt")
    prepare = [sys.executable, str(bundle / "prepare.py")]
    image = tmp_path / "image/opt/tensorfold/native/bin/tensorfold-native"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"something else")
    result = subprocess.run([*prepare, "--install", str(tmp_path / "image")], capture_output=True, text=True)
    assert result.returncode != 0 and "Unexpected tensorfold-native in the image" in result.stderr
    assert image.read_bytes() == b"something else"
    image.write_bytes(b"published")
    for _ in range(2):
        subprocess.run([*prepare, "--install", str(tmp_path / "image")], check=True, capture_output=True)
        assert image.read_bytes() == b"rebuilt" and image.stat().st_mode & 0o111
    (bundle / "out/tensorfold-native").write_bytes(b"tampered")
    result = subprocess.run([*prepare, "--check"], capture_output=True, text=True)
    assert result.returncode != 0 and "Rebuilt binary does not match the lock" in result.stderr


@pytest.mark.parametrize("name", ("serve.py", "build/Dockerfile", "patches/0011-models-reasoning.patch"))
def test_qwen_discovery_bundle_rejects_changed_files_and_a_missing_binary(tmp_path, name):
    bundle = _qwen_discovery_bundle(tmp_path)
    prepare = [sys.executable, str(bundle / "prepare.py")]
    subprocess.run([*prepare, "--check"], check=True, capture_output=True)
    for flag in (["--check", "--require-binary"], ["--install", str(tmp_path)]):
        result = subprocess.run([*prepare, *flag], capture_output=True, text=True)
        assert result.returncode != 0 and "Missing rebuilt binary" in result.stderr
    (bundle / name).write_text((bundle / name).read_text() + "\n# Unreviewed change\n")
    result = subprocess.run([*prepare, "--check"], capture_output=True, text=True)
    assert result.returncode != 0 and f"Missing or changed frozen mod file: {name}" in result.stderr


def test_qwen_discovery_readiness_and_live_check_pin_the_model_entry():
    v11 = MIA_MODS / "mia-tensorfold-qwen38fn-v1.1"
    assert (MIA_MODS / QWEN_DISCOVERY_MOD / "serve.py").read_bytes() == (v11 / "serve.py").read_bytes()
    modules = {}
    for label, path in (("ready", MIA_MODS / QWEN_DISCOVERY_MOD / "readiness.py"),
                        ("live", Path(__file__).with_name("qwen_model_discovery.py"))):
        spec = importlib.util.spec_from_file_location(f"qwen_discovery_{label}", path)
        modules[label] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modules[label])
    ready, live = modules["ready"], modules["live"]
    served = {"object": "list", "data": [QWEN_DISCOVERY_ENTRY]}
    ready.check_models(served, "Qwen3.8-Flash-Next", 1048576)
    # The published v1.1 server lists the model with no window, which is what a failed overlay would serve.
    published = {"object": "list", "data": [{key: QWEN_DISCOVERY_ENTRY[key] for key in ("id", "object", "created", "owned_by")}]}
    for body, context in ((published, 1048576), (served, 524288), ({"object": "list", "data": []}, 1048576)):
        with pytest.raises(RuntimeError):
            ready.check_models(body, "Qwen3.8-Flash-Next", context)

    reasoning = live.check_entry(QWEN_DISCOVERY_ENTRY, 1048576)
    for change in ({"context_length": 524288}, {"default_max_tokens": 32768}, {"reasoning": None},
                   {"reasoning": {**reasoning, "supported_efforts": ["xhigh", "low", "low"]}},
                   {"reasoning": {**reasoning, "supported_efforts": ["xhigh", "turbo"]}},
                   {"reasoning": {**reasoning, "supported_efforts": []}},
                   {"reasoning": {**reasoning, "supported_efforts": ["xhigh", "low", "none"]}},
                   {"reasoning": {**reasoning, "supported_efforts": ["medium", "xhigh", "low", "none"]}},
                   {"reasoning": {**reasoning, "default_effort": "high"}}):
        with pytest.raises(RuntimeError):
            live.check_entry({**QWEN_DISCOVERY_ENTRY, **change}, 1048576)
    with pytest.raises(RuntimeError):
        live.check_entry(QWEN_DISCOVERY_ENTRY, 524288)
    live.check_echo(reasoning, None, {"tensorfold": {"enable_thinking": True, "reasoning_effort": None}})
    live.check_echo({**reasoning, "default_effort": "low"}, None, {"tensorfold": {"enable_thinking": True, "reasoning_effort": "low"}})
    live.check_echo(reasoning, "high", {"tensorfold": {"enable_thinking": True, "reasoning_effort": "xhigh"}})
    live.check_echo(reasoning, "minimal", {"tensorfold": {"enable_thinking": True, "reasoning_effort": "low"}})
    live.check_echo(reasoning, "none", {"tensorfold": {"enable_thinking": False, "reasoning_effort": "none"}})
    for effort, echo in ((None, {"enable_thinking": False, "reasoning_effort": None}),
                         (None, {"enable_thinking": True, "reasoning_effort": "xhigh"}),
                         ("high", {"enable_thinking": True, "reasoning_effort": "high"}),
                         ("medium", {"enable_thinking": True, "reasoning_effort": "low"}),
                         ("none", {"enable_thinking": True, "reasoning_effort": "none"}),
                         ("low", {"enable_thinking": False, "reasoning_effort": "low"})):
        with pytest.raises(RuntimeError):
            live.check_echo(reasoning, effort, {"tensorfold": echo})


def test_mia_v110_recipe_states_the_pinned_launcher_defaults():
    path = Path(__file__).resolve().parents[1] / MIA_RECIPE_DIR / "glm53-exl3-tp2-mia-tf-v1.10-mia-1m.yaml"
    recipe = Recipe(yaml.safe_load(path.read_text()), source_path=str(path))
    lock = json.loads((MIA_MODS / "mia-tensorfold-v1.10/upstream.lock.json").read_text())
    # The lock records what the pinned scripts/config.sh exports with no local.sh or .env.
    launcher = lock["launcher_env"]
    assert len(launcher) == 38
    assert {key: recipe.env.get(key) for key in launcher} == launcher
    engine_only = {key for key in set(recipe.env) - set(launcher) if key.startswith(("TENSORFOLD_", "TF_"))}
    assert engine_only == {
        "TF_GLM_LOOP_GUARD", "TF_GLM_KEEP_REASONING", "TF_GLM_KEEP_REASONING_MB", "TENSORFOLD_GLM_IMAGE_TOKENS",
        "TENSORFOLD_GLM_VIDEO_TOKENS", "TENSORFOLD_GLM_VIDEO_FRAMES", "TENSORFOLD_GLM_MAX_IMAGES",
        "TENSORFOLD_GLM_MAX_VIDEOS", "TENSORFOLD_GLM_REQUEST_IMAGE_TOKENS", "TENSORFOLD_GLM_REQUEST_VIDEO_TOKENS"}
    # Stated independently of the lock: the sampled-decode default is on and every optional v1.9/v1.10 switch is off.
    assert recipe.env["TENSORFOLD_NUCLEUS_UNION"] == "1"
    for key, off in (("TF_GLM_EFFORT_TAIL", "0"), ("TF_GLM_CACHE_SHARE_PCT", "0"), ("TF_GLM_LOOP_GUARD", "0"),
                     ("TF_GLM_KEPT_BYTES_GIB", "0"), ("TF_GLM_KEEP_PER_CHAT", "0"), ("TF_GLM_EXL3_DEC_ORDER", "0"),
                     ("TF_GLM_DISPLAY_KV_MIB", "0"), ("TF_GLM_DISPLAY_KV_BACKEND", "drm"), ("TF_GLM_MAX_QUEUED", ""),
                     ("TF_GLM_MTP", "auto")):
        assert recipe.env[key] == off, key
    assert not [key for key in recipe.env if "SPILL" in key]
    assert (recipe.env["TENSORFOLD_MEMORY_RESERVE_GIB"], recipe.env["TF_GLM_CACHE_GIB"]) == ("14.5", "12.5")
    for field in ("kit_revision", "engine_revision", "upstream_patch_count", "upstream_patch_hash"):
        assert recipe.metadata[field] == lock[field], field
    assert recipe.metadata["site_backports"] == "exact-tool-turn-reasoning, strict-concurrent-health"
    hook = shlex.split(recipe.post_exec[0])
    assert hook[hook.index("--min-pool-tokens") + 1] == "1200000"


def test_mia_v110_bundle_has_no_sibling_or_linked_fallback(tmp_path):
    for version in ("v1.8", "v1.10"):
        shutil.copytree(MIA_MODS / f"mia-tensorfold-{version}", tmp_path / f"mia-tensorfold-{version}",
                        ignore=shutil.ignore_patterns("__pycache__"))
    bundle = tmp_path / "mia-tensorfold-v1.10"
    check = [sys.executable, str(bundle / "prepare.py"), "--check"]
    subprocess.run(check, check=True, capture_output=True)
    # v1.8's launcher and readiness gate are byte-identical, and still must not stand in for a missing helper.
    for name in ("serve.py", "readiness.py"):
        assert (bundle / name).read_bytes() == (tmp_path / "mia-tensorfold-v1.8" / name).read_bytes()
        (bundle / name).rename(bundle / (name + ".moved"))
        result = subprocess.run(check, capture_output=True, text=True)
        assert result.returncode != 0 and f"Missing or changed frozen mod file: {name}" in result.stderr
        (bundle / (name + ".moved")).rename(bundle / name)
    manifest = bundle / "upstream.lock.json"
    manifest.rename(tmp_path / "shared.lock.json")
    manifest.symlink_to(tmp_path / "shared.lock.json")
    result = subprocess.run(check, capture_output=True, text=True)
    assert result.returncode != 0 and "Missing or linked Mia TensorFold mod manifest" in result.stderr


def _synthetic_guard_tree(tmp_path):
    """A mod copy whose pins describe small stand-in sources; the real pins accept only the pinned image's files."""
    bundle = tmp_path / "mia-tensorfold-v1.10"
    shutil.copytree(MIA_MODS / "mia-tensorfold-v1.10", bundle, ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("mia_site_fixes", bundle / "site_fixes.py")
    fixes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixes)
    sources = {
        "families/glm5_next/cuda/kept_reasoning.py": (fixes.reasoning, '"""Stand-in.\n\n'
            'ids). Here the signature covers the whole conversation before the calls, not only the last user message, '
            'so a reply\nis only ever put back into the conversation it was written for.\n"""\n'
            'def _keys(history, calls):\n    ids = []\n'
            '    return [i for i in ids if isinstance(i, str) and _OUR_ID.match(i)] + [signature(history, calls)]\n'),
        "cuda/health.py": (fixes.health, "def snapshot(app, scheduler=None, decoder=None):\n    if app:\n"
                                         "        body = {}\n        return body\n"),
        "cuda/http.py": (fixes.http, "def get(self, app, health):\n    if app:\n        if health:\n"
                                     "            if self:\n                self._json(200, health.of(app).snapshot(app))\n"),
    }
    root = tmp_path / "tensorfold"
    lock = json.loads((bundle / "upstream.lock.json").read_text())
    for name, (transform, text) in sources.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
        lock["installed_files"][name] = {
            "upstream_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "site_sha256": hashlib.sha256(transform(text).encode()).hexdigest()}
    (bundle / "upstream.lock.json").write_text(json.dumps(lock))
    return fixes, root, {name: text for name, (_, text) in sources.items()}


def test_mia_v110_site_guards_check_every_source_before_writing(tmp_path):
    fixes, root, upstream = _synthetic_guard_tree(tmp_path)
    read = lambda: {name: (root / name).read_text() for name in upstream}
    # The real lock refuses anything but the pinned image's source.
    result = subprocess.run([sys.executable, str(MIA_MODS / "mia-tensorfold-v1.10/site_fixes.py"), "--root", str(root)],
                            capture_output=True, text=True)
    assert result.returncode != 0 and "installed source drifted" in result.stderr
    assert read() == upstream

    last = "cuda/http.py"  # Checked last: a refusal here must leave the first two untouched.
    (root / last).write_text(upstream[last] + "# Unreviewed change\n")
    with pytest.raises(ValueError, match="installed source drifted: cuda/http.py"):
        fixes.apply(root)
    assert {name: text for name, text in read().items() if name != last} == \
        {name: text for name, text in upstream.items() if name != last}

    outside = tmp_path / "shared-http.py"
    outside.write_text(upstream[last])
    (root / last).unlink()
    (root / last).symlink_to(outside)
    with pytest.raises(ValueError, match="Missing or linked installed source: cuda/http.py"):
        fixes.apply(root)
    (root / last).unlink()
    with pytest.raises(ValueError, match="Missing or linked installed source: cuda/http.py"):
        fixes.apply(root)
    assert outside.read_text() == upstream[last]
    assert {name: (root / name).read_text() for name in upstream if name != last} == \
        {name: text for name, text in upstream.items() if name != last}

    (root / last).write_text(upstream[last])
    fixes.apply(root)
    guarded = read()
    assert all(guarded[name] != upstream[name] for name in upstream)
    compile(guarded["cuda/health.py"], "health.py", "exec")
    fixes.apply(root)  # A second pass, and a pass after a partial write, change nothing further.
    assert read() == guarded
    (root / last).write_text(upstream[last])
    fixes.apply(root)
    assert read() == guarded


def test_readiness_requires_reported_pool_capacity():
    path = Path(__file__).resolve().parents[1] / "mods/tensorfold/readiness.py"
    spec = importlib.util.spec_from_file_location("tf_readiness", path)
    ready = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ready)
    body = {"ok": True, "mode": "strict", "streams": {"max": 4}, "context_length": 850000}
    ready.check_health(body, 4, 850000)  # Existing W20 gate does not require a pool field.
    for pool in (None, True, "1200000", 1199999):
        with pytest.raises(RuntimeError, match="pool tokens"):
            ready.check_health({**body, "pool_tokens": pool}, 4, 850000, 1200000)
    ready.check_health({**body, "pool_tokens": 1200000}, 4, 850000, 1200000)


def test_runtime_cache_and_extra_env_stay_on_managed_namespace(runtime):
    recipe = _recipe()
    settings = resolve_runtime_cache_settings(runtime=runtime, recipe=recipe)
    mounts = build_runtime_cache_mounts(
        runtime=runtime,
        recipe=recipe,
        settings=settings,
        root="/opt/spark-cache/huggingface/runtime-cache",
        image="tensorfold:dev",
        fingerprint="abc",
    )
    assert mounts is not None
    assert set(runtime.runtime_cache_paths()) == {
        "CUDA_CACHE_PATH", "TRITON_CACHE_DIR", "TORCH_EXTENSIONS_DIR", "TORCHINDUCTOR_CACHE_DIR"}
    for key in runtime.runtime_cache_paths():
        assert mounts.env[key].startswith(RUNTIME_CACHE_CONTAINER_PATH + "/")
        assert "/cache/huggingface" not in mounts.env[key]
    extra = runtime.get_extra_env()
    assert extra["HF_HOME"] == "/cache/huggingface"
    assert extra["HF_HUB_CACHE"] == "/cache/huggingface/hub"
    # The adapter sets no engine's own variables; recipes and mods do.
    assert not [key for key in extra if key.startswith(("GLM", "TF_", "TENSORFOLD"))]


@pytest.mark.parametrize("key", REQUIRED_DEFAULTS)
def test_recipe_names_its_own_engine_model_and_window(runtime, key):
    recipe = _recipe()
    del recipe.defaults[key]
    errors = [issue for issue in runtime.validate_recipe(recipe) if getattr(issue, "severity", None) == ERROR]
    assert any("defaults.%s" % key in issue.message for issue in errors)
    if key == "entrypoint":
        with pytest.raises(ValueError, match="defaults.entrypoint"):
            _node_command(runtime, recipe, 0)


def test_fabric_master_is_the_heads_cx7_address_for_one_launch(runtime, monkeypatch):
    recipe = _recipe()
    seen = {}

    def native(**kwargs):
        for rank in (0, 1):
            seen[rank], _ = _parse_env_command(_node_command(runtime, recipe, rank))
        return 0

    monkeypatch.setattr(runtime, "_run_native_cluster", native)
    fabric = {"10.0.0.1": "198.18.0.1", "10.0.0.2": "198.18.0.2"}
    assert runtime._run_cluster(["10.0.0.1", "10.0.0.2"], "img", recipe=recipe, ib_ip_map=fabric) == 0
    assert seen[0]["FABRIC_MASTER"] == seen[1]["FABRIC_MASTER"] == "198.18.0.1"
    # Cleared after the launch, and absent when SparkRun found no reachable CX7 address.
    env, _ = _parse_env_command(_node_command(runtime, recipe, 0))
    assert "FABRIC_MASTER" not in env
    assert runtime._run_cluster(["10.0.0.1", "10.0.0.2"], "img", recipe=recipe, ib_ip_map={}) == 0
    assert "FABRIC_MASTER" not in seen[0] and "FABRIC_MASTER" not in seen[1]


def test_native_lifecycle_gates_tcpstore_then_starts_rank1(runtime, monkeypatch):
    from sparkrun.orchestration.comm_env import ClusterCommEnv

    order: list[str] = []
    captured: dict[str, str] = {}

    monkeypatch.setattr(_cluster_ops, "detect_ib_with_ips", lambda *a, **k: (ClusterCommEnv.empty(), {}, {}))
    monkeypatch.setattr(_cluster_ops, "detect_head_ip", lambda ctx: FABRIC_HEAD)
    monkeypatch.setattr(_cluster_ops, "resolve_hosts_for_init", lambda ctx, head_ip: [FABRIC_HEAD, FABRIC_WORKER])
    monkeypatch.setattr("sparkrun.runtimes._init_network.workers_can_reach", lambda ctx, target: True)
    monkeypatch.setattr(_cluster_ops, "launch_containers_parallel", lambda *a, **k: order.append("containers") or 0)
    monkeypatch.setattr(_cluster_ops, "run_pre_serve_hooks", lambda *a, **k: order.append("pre_serve"))
    monkeypatch.setattr(_cluster_ops, "cleanup_ranked_containers", lambda *a, **k: None)
    monkeypatch.setattr(_cluster_ops, "find_port", lambda ctx, host, port: port)

    def fake_wait(host, port, **kwargs):
        order.append("wait:%s:%s" % (host, port))
        return True

    monkeypatch.setattr("sparkrun.orchestration.primitives.wait_for_port", fake_wait)

    def run_script(host, script, **kwargs):
        order.append("exec:%s" % host)
        return mock.MagicMock(success=True, returncode=0, stderr="", stdout="")

    monkeypatch.setattr("sparkrun.orchestration.ssh.run_remote_script", run_script)
    monkeypatch.setattr("sparkrun.orchestration.ssh.start_log_capture", lambda *a, **k: None)
    monkeypatch.setattr("sparkrun.orchestration.ssh.stop_log_capture", lambda *a, **k: [])

    original = runtime.generate_node_command

    def capturing(**kwargs):
        cmd = original(**kwargs)
        captured[kwargs["node_rank"]] = cmd
        return cmd

    runtime.generate_node_command = capturing  # type: ignore[method-assign]
    executor = mock.MagicMock()
    executor.node_container_name = lambda cid, rank: "%s_node_%d" % (cid, rank)
    executor.workload_labels_for_cluster = lambda **kw: {}
    executor.generate_exec_serve_script = lambda **kw: "#!/bin/bash\necho noop\n"
    runtime.executor = executor

    recipe = _recipe()
    ctx = ClusterContext(
        hosts=["h1", "h2"],
        head_host="h1",
        worker_hosts=["h2"],
        num_nodes=2,
        ssh_kwargs={},
        volumes={},
        all_env={},
        cluster_id="cid",
        image="glm53-tensorfold:dev",
        dry_run=False,
        config=None,
    )
    rc = _cluster_ops.run_native_cluster(
        runtime=runtime,
        ctx=ctx,
        recipe=recipe,
        overrides={},
        init_port=29511,
        follow=False,
    )
    assert rc == 0
    assert order.index("exec:h1") < order.index("wait:h1:29511") < order.index("exec:h2")
    rank0, _ = _parse_env_command(captured[0])
    rank1, _ = _parse_env_command(captured[1])
    assert rank0["RANK"] == "0"
    assert rank1["RANK"] == "1"
    assert rank0["MASTER"] == FABRIC_HEAD
    assert rank1["MASTER"] == FABRIC_HEAD
    assert rank0["MASTER_PORT"] == "29511"
    sources = runtime.log_sources("cid", ["h1", "h2"], scope=SCOPE_ALL)
    assert [source.rank for source in sources] == [0, 1]
    assert {source.mode for source in sources} == {MODE_FILE}
    assert [source.container for source in sources] == ["cid_node_0", "cid_node_1"]


DSV41_RECIPE = Path(__file__).resolve().parents[1] / "bertholomus-tensorfold-dsv41-tp2/dsv41-1017-exl3-tp2-bth-tf-6366d2c-libertai-1m.yaml"
DSV41_TARGET = "LibertAIDAI/DeepSeek-V4.1-Flash-1017-EXL3-2.9bpw"
DSV41_SERVED = "DeepSeek-V4.1-Flash-1017"


def _dsv41_bundle(tmp_path):
    recipe = Recipe(yaml.safe_load(DSV41_RECIPE.read_text()), source_path=str(DSV41_RECIPE))
    source = DSV41_RECIPE.parent / recipe.mods[0]
    bundle = tmp_path / source.name
    shutil.copytree(source, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    modules = {}
    for name in ("serve", "readiness"):
        spec = importlib.util.spec_from_file_location(f"dsv41_{name}", bundle / f"{name}.py")
        modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modules[name])
    return recipe, bundle, modules["serve"], modules["readiness"]


def test_dsv41_recipe_maps_native_rank_wiring_to_both_engine_binaries(runtime, tmp_path):
    recipe, bundle, serve, _ = _dsv41_bundle(tmp_path)
    assert recipe.name == recipe.metadata["profile"] == DSV41_RECIPE.stem
    assert not [issue for issue in runtime.validate_recipe(recipe) if getattr(issue, "severity", None) == ERROR]
    lock = json.loads((bundle / "upstream.lock.json").read_text())
    assert lock["image_id"] == recipe.container and lock["image"] == recipe.metadata["image"]
    assert lock["engine_revision"] == recipe.metadata["engine_revision"]
    assert recipe.model_revision == recipe.metadata["target_model_revision"]
    distributed = {(e.name, e.revision) for e in recipe.distribution_config.models.entries}
    assert (lock["kit"]["repository"], lock["kit"]["revision"]) in distributed
    assert (DSV41_TARGET, recipe.model_revision) in distributed
    subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], cwd=tmp_path, check=True)

    cache = tmp_path / "runtime"
    (cache / "tf-ds-rank").mkdir(parents=True)
    (cache / "dsv41_token_map.json").write_text("{}")
    fabric = {"NCCL_IB_HCA": "=rocep1s0f0:1,roceP2p1s0f0:1", "NCCL_IB_GID_INDEX": "3",
              "TF_DS_RANK_CACHE": str(cache / "tf-ds-rank"), "TF_DS_TOKEN_MAP": str(cache / "dsv41_token_map.json")}
    model, kit, engram = Path("/v/model"), Path("/v/kit"), Path("/v/engram")
    for rank in (0, 1):
        env, argv = _parse_env_command(_node_command(runtime, recipe, rank, init_port=29511))
        assert argv == ["/usr/local/bin/dsv41-tf-entrypoint"]
        assert env["MODEL_PATH"] == hf_snapshot_path(DSV41_TARGET, recipe.model_revision)
        args, child = serve.command({**recipe.env, **env, **fabric}, model, kit, engram)
        assert child["TF_TP_MASTER"] == "198.19.240.11" and child["TF_TP_PORT"] == "29511"
        assert child["TF_RDMA_DEVICES"] == "rocep1s0f0,roceP2p1s0f0" and child["TF_RDMA_GID_INDEX"] == "3"
        assert child["TF_DS_REPLAY_FLOOR"] == "1"
        if rank == 0:
            assert args[:3] == ["tensorfold-native", "serve", str(model)]
            for flag, expected in (("--name", DSV41_SERVED), ("--port", "8888"), ("--context", "1048576"),
                                   ("--parallel", "4"), ("--max-tokens", "32768"), ("--temperature", "1.0"),
                                   ("--top-p", "0.95"), ("--top-k", "20"), ("--backend", "cuda")):
                assert args[args.index(flag) + 1] == expected
            assert child["TF_DS_KIT"] == str(kit) and child["TENSORFOLD_CUDA_KERNELS"] == str(kit / "aot")
            assert child["TF_DS_ENGRAM"] == str(engram) and "--thinking" in args
        else:
            assert args[:8] == ["tf-dsv41-lanes", str(model), fabric["TF_DS_RANK_CACHE"], "1", "2", "198.19.240.11",
                                "29511", str(kit)]
            for flag, expected in (("--engram", str(engram)), ("--token-map", fabric["TF_DS_TOKEN_MAP"]),
                                   ("--pool", "1048576"), ("--rdma", "rocep1s0f0,roceP2p1s0f0"), ("--graphs", "1"),
                                   ("--side", "1"), ("--prefetch", "1"), ("--aio", "1")):
                assert args[args.index(flag) + 1] == expected
    missing = {**recipe.env, **_parse_env_command(_node_command(runtime, recipe, 0))[0], **fabric,
               "TF_DS_RANK_CACHE": str(tmp_path / "absent")}
    with pytest.raises(SystemExit, match="rank inputs"):
        serve.command(missing, model, kit, engram)


def test_dsv41_views_link_read_only_snapshots_and_check_the_image_bias(tmp_path, monkeypatch):
    recipe, bundle, serve, _ = _dsv41_bundle(tmp_path)
    lock = json.loads((bundle / "upstream.lock.json").read_text())
    hub, views = tmp_path / "hub", tmp_path / "views"
    monkeypatch.setattr(serve, "HUB", hub)
    monkeypatch.setattr(serve, "VIEWS", views)
    monkeypatch.setattr(serve, "SITE", bundle)
    weights = serve.snapshot(DSV41_TARGET, recipe.model_revision)
    weights.mkdir(parents=True)
    for name in ("config.json", "model-00001-of-00043.safetensors", "chat_template.jinja"):
        (weights / name).write_text(name)
    kit = serve.snapshot(lock["kit"]["repository"], lock["kit"]["revision"]) / "kit"
    for name in ("aot/aot.json", "engram.json", "vision/torch_fmha_sm120.cubin", "vision/gate_bias_vl.safetensors"):
        (kit / name).parent.mkdir(parents=True, exist_ok=True)
        (kit / name).write_text(name)
    (kit / "tools").mkdir()
    bias = b"bias of the 1017 weights"
    (kit / "tools/make_bias_vl.py").write_text(f"import sys\nopen(sys.argv[2], 'wb').write({bias!r})\n")
    engram = serve.snapshot(lock["engram"]["repository"], lock["engram"]["revision"])
    engram.mkdir(parents=True)
    blobs = engram.parents[1] / "blobs"
    blobs.mkdir()
    for name, blob in lock["engram"]["files"].items():
        (blobs / blob).write_text(name)
        (engram / name).symlink_to(f"../../blobs/{blob}")
    lock["kit"]["bias_vl_sha256"] = hashlib.sha256(bias).hexdigest()

    model_view, kit_view, engram_view = serve.views(lock, weights)
    assert (model_view / "chat_template.jinja").resolve() == (bundle / "chat_template.jinja").resolve()
    assert (model_view / "config.json").resolve() == (weights / "config.json").resolve()
    assert (kit_view / "vision/gate_bias_vl.safetensors").read_bytes() == bias
    assert (kit_view / "vision/torch_fmha_sm120.cubin").resolve() == (kit / "vision/torch_fmha_sm120.cubin").resolve()
    assert (kit_view / "aot").resolve() == (kit / "aot").resolve()
    assert sorted(p.name for p in engram_view.iterdir()) == sorted([*lock["engram"]["files"], "model.safetensors.index.json"])
    assert (engram_view / "model.safetensors.index.json").resolve() == (bundle / "engram.index.json").resolve()
    # A second start in the same container rebuilds the views.
    serve.views(lock, weights)
    lock["kit"]["bias_vl_sha256"] = "0" * 64
    with pytest.raises(SystemExit, match="image routing bias"):
        serve.views(lock, weights)
    lock["kit"]["bias_vl_sha256"] = hashlib.sha256(bias).hexdigest()
    shard = engram / "model-00048-of-00048.safetensors"
    shard.unlink()
    shard.symlink_to(f"../../blobs/{lock['engram']['files']['model-00047-of-00048.safetensors']}")
    with pytest.raises(SystemExit, match="locked blob"):
        serve.views(lock, weights)
    shard.unlink()
    with pytest.raises(SystemExit, match="Engram"):
        serve.views(lock, weights)


@pytest.mark.parametrize("change", ["edit", "extra"])
def test_dsv41_frozen_bundle_rejects_changed_or_added_files(tmp_path, change):
    _, bundle, _, _ = _dsv41_bundle(tmp_path)
    if change == "edit":
        (bundle / "serve.py").write_text((bundle / "serve.py").read_text() + "\n# Unreviewed change\n")
    else:
        (bundle / "patches/0004-extra.patch").write_text("")
    result = subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], capture_output=True, text=True)
    assert result.returncode != 0


def test_dsv41_readiness_requires_lanes_window_and_reasoning(tmp_path):
    recipe, _, _, ready = _dsv41_bundle(tmp_path)
    hook = shlex.split(recipe.post_exec[0])
    assert hook[hook.index("--model") + 1] == DSV41_SERVED and hook[hook.index("--context") + 1] == "1048576"
    health = {"status": "ok", "warming": False, "model": DSV41_SERVED, "max_batch_size": 4}
    ready.check_health(health, DSV41_SERVED, 4)
    for change in ({"status": "error"}, {"warming": True}, {"model": "other"}, {"max_batch_size": 2}):
        with pytest.raises(RuntimeError):
            ready.check_health({**health, **change}, DSV41_SERVED, 4)
    entry = {"id": DSV41_SERVED, "context_length": 1048576, "reasoning": {"supported_efforts": ["max", "high", "none"]}}
    ready.check_models({"data": [entry]}, DSV41_SERVED, 1048576)
    for body in ({"data": []}, {"data": [{**entry, "context_length": 262144}]}, {"data": [{**entry, "reasoning": {}}]}):
        with pytest.raises(RuntimeError):
            ready.check_models(body, DSV41_SERVED, 1048576)


def test_dsv41_rank_inputs_follow_the_weights_in_a_root_of_their_own(runtime):
    recipe = Recipe.from_dict(yaml.safe_load(DSV41_RECIPE.read_text()))
    settings = resolve_runtime_cache_settings(runtime=runtime, recipe=recipe)
    assert settings.key_by_image is False
    root = resolve_runtime_cache_root(settings, "/home/kelchm/.cache/sparkrun")
    assert root == "/opt/spark-cache/runtime-inputs"
    mounts = build_runtime_cache_mounts(runtime=runtime, recipe=recipe, settings=settings, root=root, image=recipe.container)
    assert mounts.leaf == f"{root}/tensorfold/LibertAIDAI__DeepSeek-V4.1-Flash-1017-EXL3-2.9bp-55410efc"
    # Another TensorFold recipe's sweep ages siblings under the shared root only.
    other = Recipe.from_dict(yaml.safe_load((DSV41_RECIPE.parents[1] / "tensorfold-glm53-exl3.yaml").read_text()))
    shared = resolve_runtime_cache_root(resolve_runtime_cache_settings(runtime=runtime, recipe=other), "/home/kelchm/.cache/sparkrun")
    assert not mounts.leaf.startswith(shared + "/")
    assert recipe.env["TF_DS_RANK_CACHE"].startswith(RUNTIME_CACHE_CONTAINER_PATH + "/")
    assert recipe.env["TF_DS_TOKEN_MAP"].startswith(RUNTIME_CACHE_CONTAINER_PATH + "/")


def test_dsv41_rdma_devices_take_exact_names_on_port_1(tmp_path):
    _, _, serve, _ = _dsv41_bundle(tmp_path)
    devices = tmp_path / "infiniband"
    for name in ("rocep1s0f0", "roceP2p1s0f0"):
        (devices / name).mkdir(parents=True)
    hca = {"NCCL_IB_HCA": "=rocep1s0f0:1,roceP2p1s0f0"}
    assert serve.rdma_devices(hca, devices) == "rocep1s0f0,roceP2p1s0f0"
    assert serve.rdma_devices({**hca, "TF_RDMA_DEVICES": "rocep1s0f0"}, devices) == "rocep1s0f0"
    for selector, message in (("^rocep1s0f1", "exclusion"), ("rocep1s0f0:2", "port 1"), ("rocep", "in full")):
        with pytest.raises(SystemExit, match=message):
            serve.rdma_devices({"NCCL_IB_HCA": selector}, devices)


UPSTREAM_QWEN_DIR = Path(__file__).resolve().parents[1] / "ashhart-tensorfold-qwen38fn-tp2"
UPSTREAM_QWEN_PROFILES = sorted(UPSTREAM_QWEN_DIR.glob("*.yaml"))
UPSTREAM_IMAGE_LOCK = Path(__file__).resolve().parents[1] / "images/ashhart-tensorfold-v1.1.2/image.lock.json"
UPSTREAM_QWEN_TARGET = "nvidia/Qwen3.8-Flash-Next-FP4-Local"
UPSTREAM_QWEN_FABRIC = {"NCCL_IB_HCA": "=rocep1s0f0:1,roceP2p1s0f0:1"}


def _upstream_qwen_bundle(tmp_path, path):
    recipe = Recipe(yaml.safe_load(path.read_text()), source_path=str(path))
    source = path.parent / recipe.mods[-1]
    bundle = tmp_path / source.name
    shutil.copytree(source, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("upstream_qwen_serve", bundle / "serve.py")
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return recipe, bundle, serve


def _hf_snapshot(hub, files, shared):
    """An HF cache snapshot and the digest the HF API's file list gives. Each file links to its repository blob, which
    holds the bytes (SparkRun's copy on a worker) or, when `shared`, links on to the cache-wide store under its Xet
    hash (a head with huggingface_hub 1.32 or later)."""
    repo = hub / "models--nvidia--x"
    snapshot = repo / "snapshots/rev"
    for name, blob in files.items():
        (repo / "blobs").mkdir(parents=True, exist_ok=True)
        if shared:
            store = hub / "blobs" / "xet" / ("f" * 63 + str(len(name)))
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_text(name)
            (repo / "blobs" / blob).symlink_to(Path("../../blobs/xet") / store.name)
        else:
            (repo / "blobs" / blob).write_text(name)
        (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
        (snapshot / name).symlink_to(Path("../" * name.count("/")) / "../../blobs" / blob)
    listed = sorted(f"{name} {blob}\n" for name, blob in files.items())
    return snapshot, hashlib.sha256("".join(listed).encode()).hexdigest()


def test_upstream_qwen_profiles_are_one_workload_on_the_locked_image(runtime):
    image = json.loads(UPSTREAM_IMAGE_LOCK.read_text())
    assert [p.stem.rsplit("-", 1)[1] for p in UPSTREAM_QWEN_PROFILES] == ["cache", "mtp"]
    recipes = [Recipe(yaml.safe_load(p.read_text()), source_path=str(p)) for p in UPSTREAM_QWEN_PROFILES]
    for path, recipe in zip(UPSTREAM_QWEN_PROFILES, recipes):
        assert recipe.name == recipe.metadata["profile"] == path.stem
        assert not [issue for issue in runtime.validate_recipe(recipe) if getattr(issue, "severity", None) == ERROR]
        assert recipe.container == image["image_id"] and recipe.metadata["image"] == image["image"]
        assert recipe.metadata["engine_revision"] == image["tensorfold"]["revision"]
        assert recipe.model_revision == recipe.metadata["target_model_revision"]
        mod = json.loads((path.parent / recipe.mods[-1] / "upstream.lock.json").read_text())
        assert (mod["image"], mod["image_id"]) == (image["image"], image["image_id"])
        assert mod["engine_revision"] == image["tensorfold"]["revision"]
        assert (mod["weights"]["repository"], mod["weights"]["revision"]) == (recipe.model, recipe.model_revision)
        assert mod["weights"]["checkpoint_sha256"] == recipe.env["TENSORFOLD_CHECKPOINT_SHA256"]
        assert (UPSTREAM_QWEN_TARGET, recipe.model_revision) in {
            (e.name, e.revision) for e in recipe.distribution_config.models.entries}
    # Everything SparkRun finds a workload by is shared, so stop and logs work with either file.
    keys = [(r.runtime, r.model, r.container, r.defaults["port"], r.defaults["served_model_name"],
             r.defaults["tensor_parallel"]) for r in recipes]
    assert keys[0] == keys[1]
    assert recipes[0].env == recipes[1].env
    # Fabric addresses come from SparkRun's detection or the cluster's env, so no lab value is in the tree.
    mod = UPSTREAM_QWEN_DIR / recipes[0].mods[-1]
    for text in [p.read_text() for p in UPSTREAM_QWEN_PROFILES] + [p.read_text() for p in mod.rglob("*") if p.is_file() and "__pycache__" not in p.parts]:
        assert not re.search(r"\b(?!(127\.0\.0\.1|0\.0\.0\.0)\b)\d{1,3}(\.\d{1,3}){3}\b|/home/|/opt/spark|NCCL_IB_(HCA|GID_INDEX)\s*:", text)


@pytest.mark.parametrize("path", UPSTREAM_QWEN_PROFILES, ids=lambda p: p.stem)
def test_upstream_qwen_rank0_listens_on_the_port_sparkrun_gates(runtime, tmp_path, path):
    recipe, bundle, serve = _upstream_qwen_bundle(tmp_path, path)
    subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], cwd=tmp_path, check=True)
    ctx = ClusterContext.build(runtime, [FABRIC_HEAD, FABRIC_WORKER], recipe.container, "tf-profile-test",
                               recipe.env, None, None, dry_run=True, recipe=recipe)
    script = shlex.split(DockerExecutor().generate_launch_script(
        image=recipe.container, container_name="tf-profile-test", command="sleep infinity",
        env=ctx.all_env, volumes=ctx.volumes))
    for setting in ("NCCL_RUNTIME_CONNECT=0", "NCCL_CUMEM_ENABLE=0",
                    "NCCL_CUMEM_HOST_ENABLE=0", "TENSORFOLD_CHECKPOINT_SHA256=" + recipe.env["TENSORFOLD_CHECKPOINT_SHA256"]):
        assert setting in script
    assert runtime.native_rendezvous_port(recipe, {}, num_nodes=2, init_port=29511) == 29511
    drafts = "no_drafts" not in recipe.defaults
    for rank in (0, 1):
        env, argv = _parse_env_command(_node_command(runtime, recipe, rank, init_port=29511))
        assert argv == ["/usr/local/bin/qwen38fn-tf-entrypoint"]
        assert env["MODEL_PATH"] == hf_snapshot_path(UPSTREAM_QWEN_TARGET, recipe.model_revision)
        child = {**recipe.env, **env, **UPSTREAM_QWEN_FABRIC}
        with mock.patch.object(serve, "checkpoint_digest", return_value=recipe.env["TENSORFOLD_CHECKPOINT_SHA256"]):
            args = serve.command(child)
        assert args[:3] == ["tensorfold-native", "serve", env["MODEL_PATH"]]
        for flag, expected in (("--backend", "cuda"), ("--name", "Qwen3.8-Flash-Next"), ("--port", "8888"),
                               ("--context", "262144"), ("--max-tokens", "32768"), ("--tp", "2"),
                               ("--rank", str(rank)), ("--master", FABRIC_HEAD), ("--master-port", "29511")):
            assert args[args.index(flag) + 1] == expected
        # With the head's CX7 address from SparkRun, the adapter passes it and the control link uses it.
        runtime._fabric_master = "192.0.2.11"
        try:
            env, _ = _parse_env_command(_node_command(runtime, recipe, rank, init_port=29511))
        finally:
            runtime._fabric_master = None
        with mock.patch.object(serve, "checkpoint_digest", return_value=recipe.env["TENSORFOLD_CHECKPOINT_SHA256"]):
            fabric = serve.command({**recipe.env, **env, **UPSTREAM_QWEN_FABRIC})
        assert fabric[fabric.index("--master") + 1] == "192.0.2.11"
        assert fabric[fabric.index("--master-port") + 1] == "29511"
        assert "--thinking" in args and ("--no-drafts" in args) is not drafts
        cache = [args[i + 1] for i, a in enumerate(args) if a == "--prompt-cache-gib"]
        assert cache == (["0"] if drafts else ["16"])
        assert args[args.index("--parallel") + 1] == "4"


def test_upstream_qwen_ranks_check_the_snapshot_and_both_rails(tmp_path):
    recipe, _, serve = _upstream_qwen_bundle(tmp_path, UPSTREAM_QWEN_PROFILES[0])
    files = {"config.json": "1efa9baa6f332ef821a9468eddd0414ee9bfefb0",
             "tensors/model-00001-of-00002.safetensors": "ebf3648c" + "0" * 56,
             "tensors/model-00002-of-00002.safetensors": "79a643fc" + "0" * 56}
    # The head's shared-store layout and the worker's copied one give the same digest as the API's list.
    head, digest = _hf_snapshot(tmp_path / "head", files, shared=True)
    worker, same = _hf_snapshot(tmp_path / "worker", files, shared=False)
    assert serve.checkpoint_digest(head) == serve.checkpoint_digest(worker) == digest == same
    base = {"RANK": "0", "MASTER": FABRIC_HEAD, "MASTER_PORT": "25000", "MODEL_PATH": str(head), "HOST": "0.0.0.0",
            "PORT": "8888", "SERVED_NAME": "Qwen3.8-Flash-Next", "CONTEXT": "262144", "MAX_TOKENS": "32768",
            "TENSORFOLD_CHECKPOINT_SHA256": digest, **UPSTREAM_QWEN_FABRIC}
    assert serve.command(base)[serve.command(base).index("--master") + 1] == FABRIC_HEAD
    with pytest.raises(SystemExit, match="TENSORFOLD_CHECKPOINT_SHA256"):
        serve.command({**base, "TENSORFOLD_CHECKPOINT_SHA256": "0" * 64})
    config = worker / "config.json"
    config.unlink()
    config.symlink_to("../../blobs/79a643fc" + "0" * 56)
    with pytest.raises(SystemExit, match="hashes to"):
        serve.command({**base, "MODEL_PATH": str(worker)})
    # A missing blob, a plain file and a link out of the repository's blobs are not an HF cache snapshot.
    shard = head / "tensors/model-00002-of-00002.safetensors"
    (head.parents[1] / "blobs" / os.readlink(shard).rsplit("/", 1)[1]).unlink()
    with pytest.raises(SystemExit, match="present blob"):
        serve.checkpoint_digest(head)
    shard.unlink()
    shard.write_text("copied")
    with pytest.raises(SystemExit, match="not an HF cache snapshot"):
        serve.checkpoint_digest(head)
    shard.unlink()
    shard.symlink_to(tmp_path / "head/blobs/xet" / ("f" * 63 + "11"))
    with pytest.raises(SystemExit, match="present blob"):
        serve.checkpoint_digest(head)
    for hca in ("rocep1s0f0", "^rocep1s0f1,roceP2p1s0f1", ""):
        with pytest.raises(SystemExit, match="two CX7 rails"):
            serve.command({**base, "NCCL_IB_HCA": hca})


def test_upstream_qwen_drafts_keep_the_prompt_cache_at_zero(tmp_path):
    _, _, serve = _upstream_qwen_bundle(tmp_path, UPSTREAM_QWEN_PROFILES[0])
    base = {"RANK": "1", "MASTER": FABRIC_HEAD, "MASTER_PORT": "25000", "MODEL_PATH": "/m", "HOST": "0.0.0.0",
            "PORT": "8888", "SERVED_NAME": "Qwen3.8-Flash-Next", "CONTEXT": "262144", "MAX_TOKENS": "32768",
            "TENSORFOLD_CHECKPOINT_SHA256": "a" * 64, **UPSTREAM_QWEN_FABRIC}
    with mock.patch.object(serve, "checkpoint_digest", return_value="a" * 64):
        drafted = serve.command(base)
        assert drafted.count("--prompt-cache-gib") == 1 and drafted[drafted.index("--prompt-cache-gib") + 1] == "0"
        # A size given with drafts on reaches the engine, which refuses it at TP2; it is not doubled or replaced.
        asked = serve.command({**base, "EXTRA_ARGS": "--prompt-cache-gib 8"})
        assert asked.count("--prompt-cache-gib") == 1 and asked[-1] == "8"
        plain = serve.command({**base, "NO_DRAFTS": "1"})
        assert "--no-drafts" in plain and "--prompt-cache-gib" not in plain


def test_upstream_qwen_profiles_use_the_shared_gate_with_their_own_shape():
    for path in UPSTREAM_QWEN_PROFILES:
        recipe = Recipe(yaml.safe_load(path.read_text()), source_path=str(path))
        assert [Path(m).name for m in recipe.mods] == ["tensorfold-readiness", "ashhart-tensorfold-qwen38fn-v1.1.2"]
        assert len(recipe.post_exec) == 1
        hook = shlex.split(recipe.post_exec[0])
        assert hook[:2] == ["python3", "/opt/tensorfold-readiness/readiness.py"]
        options = dict(zip(hook[2::2], hook[3::2]))
        lanes = shlex.split(recipe.defaults["extra_args"])
        assert options == {"--model": recipe.defaults["served_model_name"], "--context": str(recipe.defaults["context"]),
                           "--lanes": lanes[lanes.index("--parallel") + 1], "--efforts": "xhigh,medium,low,none"}
