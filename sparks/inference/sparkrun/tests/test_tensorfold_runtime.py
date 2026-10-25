"""TensorFold SparkRun runtime adapter — discovery, topology, rendezvous, cache, health."""

from __future__ import annotations

import hashlib
import json
import importlib.util
import shlex
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path
from unittest import mock

import pytest
import yaml

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
if str(PLUGINS) not in sys.path:
    sys.path.insert(0, str(PLUGINS))

from tensorfold import _FABRIC_MASTER, REQUIRED_DEFAULTS, TensorFoldRuntime, hf_snapshot_path  # noqa: E402

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
from sparkrun.orchestration.job_metadata import derive_recipe_fingerprint  # noqa: E402
from sparkrun.runtimes import _cluster_ops  # noqa: E402
from sparkrun.runtimes._cluster_ops import ClusterContext  # noqa: E402

TARGET = "Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw"
TARGET_REV = "25a44fdbf16862a46b7cc9921142c6c81350af2f"
DRAFT = "incoai/GLM-5.3-Flash-DFlash2"
DRAFT_REV = "dc77ff1c99eeb2df044ee3d4f0094eb033fee410"
ROOT = Path(__file__).resolve().parents[1]
RECIPES = sorted(path for path in ROOT.glob("*/*.yaml") if path.parent.name != "benchmarks")
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
    # A two-rank recipe placed on one node, and a one-node recipe given ranks.
    with pytest.raises(ValueError, match="1-node launch of a recipe with min_nodes=2"):
        runtime.generate_command(_recipe(), {}, is_cluster=False)
    with pytest.raises(ValueError, match="ranked launch"):
        runtime.generate_node_command(_one_node_recipe(), {}, head_ip=FABRIC_HEAD, num_nodes=1, node_rank=0)

    for nodes, tp in (((1, 1), 2), ((2, 2), 1), ((1, 2), 1), ((3, 3), 3), ((1, None), 2)):
        issues = runtime.validate_recipe(_recipe(min_nodes=nodes[0], max_nodes=nodes[1], defaults={"tensor_parallel": tp}))
        errors = [issue for issue in issues if getattr(issue, "severity", None) == ERROR]
        assert errors, (nodes, tp)
        assert any("one node with tensor_parallel=1, or two nodes" in issue.message for issue in errors)

    # A tensor_parallel override cannot move a recipe to the other shape.
    with pytest.raises(ValueError, match="1-node launch of a recipe with min_nodes=2"):
        runtime.generate_command(_recipe(), {"tensor_parallel": 1}, is_cluster=False)
    loose = _recipe(min_nodes=1, max_nodes=None, defaults={"tensor_parallel": 1})
    with pytest.raises(ValueError, match="2-node launch of a recipe with min_nodes=1"):
        runtime.generate_node_command(loose, {"tensor_parallel": 2}, head_ip=FABRIC_HEAD, num_nodes=2, node_rank=0)


def _one_node_recipe(defaults=None):
    return _recipe(min_nodes=1, max_nodes=1, defaults={"tensor_parallel": 1, **(defaults or {})})


def test_one_node_launch_gets_the_engine_env_and_no_rendezvous(runtime):
    recipe = _one_node_recipe()
    assert recipe.mode == "solo"
    assert not [issue for issue in runtime.validate_recipe(recipe) if getattr(issue, "severity", None) == ERROR]
    env, argv = _parse_env_command(runtime.generate_command(recipe, {}, is_cluster=False, num_nodes=1))
    assert argv == [ENTRYPOINT]
    assert env == {
        "MODEL_PATH": hf_snapshot_path(TARGET, TARGET_REV), "DRAFTER": hf_snapshot_path(DRAFT, DRAFT_REV),
        "CONTEXT": CONTEXT, "SERVED_NAME": SERVED, "MAX_TOKENS": "65536", "HOST": "0.0.0.0", "PORT": "8888",
    }
    # A launch in progress elsewhere leaves its fabric address out of a one-node command.
    token = _FABRIC_MASTER.set("198.18.0.1")
    try:
        env, _ = _parse_env_command(runtime.generate_command(recipe, {"port": 9999}, is_cluster=False))
    finally:
        _FABRIC_MASTER.reset(token)
    assert env["PORT"] == "9999" and not {"RANK", "MASTER", "MASTER_PORT", "FABRIC_MASTER"} & set(env)


def test_one_node_launch_takes_sparkruns_solo_path(runtime, monkeypatch):
    recipe = _one_node_recipe()
    seen = {}
    monkeypatch.setattr(runtime, "_run_solo", lambda **kwargs: seen.update(kwargs) or 0)
    monkeypatch.setattr(runtime, "_run_native_cluster", lambda **kwargs: pytest.fail("ranked launch"))
    command = runtime.generate_command(recipe, {}, is_cluster=False, num_nodes=1)
    assert runtime.run(["h1"], "img", command, recipe, {}) == 0
    assert seen["host"] == "h1" and seen["serve_command"] == command
    assert runtime.get_head_container_name("cid", is_solo=True) == "cid_solo"
    sources = runtime.log_sources("cid", ["h1"], is_solo=True, scope=SCOPE_ALL)
    assert [source.container for source in sources] == ["cid_solo"]



def test_one_node_recipe_lands_on_the_clusters_first_host(runtime):
    from sparkrun.api._hosts import resolve_effective_hosts

    hosts, solo, _, placement = resolve_effective_hosts(["h1", "h2"], _one_node_recipe(), {}, runtime=runtime)
    assert (hosts, solo, placement) == (["h1"], True, None)
    # SparkRun places a two-node recipe on one host when tensor_parallel is overridden to 1; the adapter refuses it.
    hosts, solo, _, _ = resolve_effective_hosts(["h1", "h2"], _recipe(), {"tensor_parallel": 1}, runtime=runtime)
    assert (hosts, solo) == (["h1"], True)
    with pytest.raises(ValueError, match="min_nodes=2"):
        runtime.generate_command(_recipe(), {"tensor_parallel": 1}, is_cluster=not solo, num_nodes=len(hosts))

def test_prepare_distributes_declared_drafter(runtime):
    recipe = _recipe()
    before = {entry.name for entry in recipe.distribution_config.models.entries}
    runtime.prepare(recipe, [FABRIC_HEAD, FABRIC_WORKER])
    names = {entry.name: entry.revision for entry in recipe.distribution_config.models.entries}
    assert DRAFT in names
    assert names[DRAFT] == DRAFT_REV
    assert DRAFT not in before


def test_recipes_have_separate_native_cache_namespaces(runtime):
    leaves = []
    for path in RECIPES:
        recipe = Recipe.from_dict(yaml.safe_load(path.read_text()))
        mounts = build_runtime_cache_mounts(
            runtime=runtime, recipe=recipe,
            settings=resolve_runtime_cache_settings(runtime=runtime, recipe=recipe),
            root="/opt/spark-cache/huggingface/runtime-cache", image=recipe.container,
        )
        leaves.append(mounts.leaf)
    assert len(set(leaves)) == len(RECIPES)


@pytest.mark.parametrize("path", RECIPES, ids=lambda path: path.stem)
def test_native_mod_transfer_is_self_contained(tmp_path, path):
    recipe = Recipe(yaml.safe_load(path.read_text()), source_path=str(path))
    fingerprint = derive_recipe_fingerprint(recipe)
    registry = mock.Mock()
    resolve_and_inject_mods(recipe, registry)
    assert registry.mock_calls == []
    assert derive_recipe_fingerprint(recipe) == fingerprint
    assert len(recipe.pre_exec) == 2 * len(recipe.mods)
    for mod, copy, command in zip(recipe.mods, recipe.pre_exec[::2], recipe.pre_exec[1::2]):
        assert copy["dest"] == "/workspace/mods/" + Path(mod).name
        assert copy["dest"] in command
        assert Path(copy["copy"]) == (path.parent / mod).resolve()
        bundle = tmp_path / Path(mod).name
        shutil.copytree(copy["copy"], bundle, ignore=shutil.ignore_patterns("__pycache__", "out"))
        assert not (bundle / "out").exists()
        lock = json.loads((bundle / "upstream.lock.json").read_text())
        if "image" in lock:
            assert recipe.container in (lock["image"], lock.get("image_id"))
        subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], cwd=tmp_path, check=True)


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


def test_a_nested_launch_leaves_the_outer_launchs_fabric_master(runtime, monkeypatch):
    recipe = _recipe()
    seen = []

    def native(**kwargs):
        seen.append(_parse_env_command(_node_command(runtime, recipe, 0))[0].get("FABRIC_MASTER"))
        if len(seen) == 1:
            runtime._run_cluster(["10.0.1.1", "10.0.1.2"], "img", recipe=recipe, ib_ip_map={"10.0.1.1": "198.18.1.1"})
        seen.append(_parse_env_command(_node_command(runtime, recipe, 1))[0].get("FABRIC_MASTER"))
        return 0

    monkeypatch.setattr(runtime, "_run_native_cluster", native)
    runtime._run_cluster(["10.0.0.1", "10.0.0.2"], "img", recipe=recipe, ib_ip_map={"10.0.0.1": "198.18.0.1"})
    assert seen == ["198.18.0.1", "198.18.1.1", "198.18.1.1", "198.18.0.1"]


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


DSV41_RECIPES = sorted((Path(__file__).resolve().parents[1] / "bertholomus-tensorfold-dsv41-tp2").glob("*.yaml"))
DSV41_TARGET = "LibertAIDAI/DeepSeek-V4.1-Flash-1017-EXL3-2.9bpw"
DSV41_SERVED = "DeepSeek-V4.1-Flash-1017"


@pytest.fixture(params=DSV41_RECIPES, ids=lambda path: path.stem)
def dsv41_recipe(request):
    return request.param


def _dsv41_bundle(tmp_path, path):
    recipe = Recipe(yaml.safe_load(path.read_text()), source_path=str(path))
    source = next(path.parent / mod for mod in recipe.mods if (path.parent / mod / "serve.py").exists())
    bundle = tmp_path / source.name
    shutil.copytree(source, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("dsv41_serve", bundle / "serve.py")
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return recipe, bundle, serve


def test_dsv41_recipe_maps_native_rank_wiring_to_both_engine_binaries(runtime, tmp_path, monkeypatch, dsv41_recipe):
    recipe, bundle, serve = _dsv41_bundle(tmp_path, dsv41_recipe)
    # Exercise device validation against the Spark's inventory, not the test host's sysfs.
    devices = tmp_path / "infiniband"
    for name in ("rocep1s0f0", "roceP2p1s0f0"):
        (devices / name).mkdir(parents=True)
    monkeypatch.setattr(serve, "rdma_devices", partial(serve.rdma_devices, devices=devices))
    context = str(recipe.defaults["context"])
    assert recipe.name == recipe.metadata["profile"] == dsv41_recipe.stem
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
    fabric = {"NCCL_IB_HCA": "=rocep1s0f0:1,roceP2p1s0f0:1", "NCCL_IB_GID_INDEX": "3", "FABRIC_MASTER": "198.19.240.11",
              "TF_DS_RANK_CACHE": str(cache / "tf-ds-rank"), "TF_DS_TOKEN_MAP": str(cache / "dsv41_token_map.json")}
    model, kit, engram = Path("/v/model"), Path("/v/kit"), Path("/v/engram")
    for rank in (0, 1):
        env, argv = _parse_env_command(_node_command(runtime, recipe, rank, init_port=29511))
        assert argv == [recipe.defaults["entrypoint"]]
        assert f"serve.py {argv[0]}" in (bundle / "run.sh").read_text()
        assert env["MODEL_PATH"] == hf_snapshot_path(DSV41_TARGET, recipe.model_revision)
        args, child = serve.command({**recipe.env, **env, **fabric}, model, kit, engram)
        assert child["TF_TP_MASTER"] == "198.19.240.11" and child["TF_TP_PORT"] == "29511"
        assert child["TF_RDMA_DEVICES"] == "rocep1s0f0,roceP2p1s0f0" and child["TF_RDMA_GID_INDEX"] == "3"
        assert child["TF_DS_REPLAY_FLOOR"] == "1"
        if rank == 0:
            assert args[:3] == ["tensorfold-native", "serve", str(model)]
            for flag, expected in (("--name", DSV41_SERVED), ("--port", "8888"), ("--context", context),
                                   ("--parallel", "4"), ("--max-tokens", "32768"), ("--temperature", "1.0"),
                                   ("--top-p", "0.95"), ("--top-k", "20"), ("--backend", "cuda")):
                assert args[args.index(flag) + 1] == expected
            assert child["TF_DS_KIT"] == str(kit) and child["TENSORFOLD_CUDA_KERNELS"] == str(kit / "aot")
            assert child["TF_DS_ENGRAM"] == str(engram) and "--thinking" in args
        else:
            assert args[:8] == ["tf-dsv41-lanes", str(model), fabric["TF_DS_RANK_CACHE"], "1", "2", "198.19.240.11",
                                "29511", str(kit)]
            for flag, expected in (("--engram", str(engram)), ("--token-map", fabric["TF_DS_TOKEN_MAP"]),
                                   ("--pool", context), ("--rdma", "rocep1s0f0,roceP2p1s0f0"), ("--graphs", "1"),
                                   ("--side", "1"), ("--prefetch", "1"), ("--aio", "1")):
                assert args[args.index(flag) + 1] == expected
    missing = {**recipe.env, **_parse_env_command(_node_command(runtime, recipe, 0))[0], **fabric,
               "TF_DS_RANK_CACHE": str(tmp_path / "absent")}
    with pytest.raises(SystemExit, match="rank inputs"):
        serve.command(missing, model, kit, engram)
    # The ranks link only over the fabric: no address in the recipe, and none at all fails closed.
    assert not any(key.endswith("TP_MASTER") for key in recipe.env)
    node = {**recipe.env, **_parse_env_command(_node_command(runtime, recipe, 0))[0], **fabric}
    for unset in ({}, {"FABRIC_MASTER": ""}):
        env = {key: value for key, value in node.items() if key != "FABRIC_MASTER"} | unset
        with pytest.raises(SystemExit, match="FABRIC_MASTER"):
            serve.command(env, model, kit, engram)


def test_dsv41_views_link_read_only_snapshots_and_check_the_image_bias(tmp_path, monkeypatch, dsv41_recipe):
    recipe, bundle, serve = _dsv41_bundle(tmp_path, dsv41_recipe)
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
def test_dsv41_frozen_bundle_rejects_changed_or_added_files(tmp_path, change, dsv41_recipe):
    _, bundle, _ = _dsv41_bundle(tmp_path, dsv41_recipe)
    if change == "edit":
        (bundle / "serve.py").write_text((bundle / "serve.py").read_text() + "\n# Unreviewed change\n")
    else:
        (bundle / "patches/0099-extra.patch").write_text("")
    result = subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], capture_output=True, text=True)
    assert result.returncode != 0


def test_dsv41_frozen_bundle_refuses_linked_and_dangling_files(tmp_path):
    source = DSV41_RECIPES[0].parents[1] / "mods/bertholomus-tensorfold-dsv41-2970bcd"
    bundle = tmp_path / source.name
    shutil.copytree(source, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    check = [sys.executable, str(bundle / "prepare.py"), "--check"]
    subprocess.run(check, check=True, capture_output=True)
    (bundle / "patches/0099-dangling.patch").symlink_to(tmp_path / "absent")
    assert subprocess.run(check, capture_output=True).returncode != 0
    record = subprocess.run([sys.executable, str(bundle / "prepare.py"), "--record"], capture_output=True, text=True)
    assert record.returncode != 0 and "linked files" in record.stderr
    (bundle / "patches/0099-dangling.patch").unlink()
    lock = bundle / "upstream.lock.json"
    (tmp_path / "lock.json").write_text(lock.read_text())
    lock.unlink()
    lock.symlink_to(tmp_path / "lock.json")
    result = subprocess.run(check, capture_output=True, text=True)
    assert result.returncode != 0 and "linked dsv41 mod lock" in result.stderr


def test_dsv41_readiness_requires_lanes_window_and_reasoning(dsv41_recipe):
    recipe = Recipe(yaml.safe_load(dsv41_recipe.read_text()), source_path=str(dsv41_recipe))
    context = recipe.defaults["context"]
    hook = shlex.split(recipe.post_exec[0])
    assert hook[hook.index("--model") + 1] == DSV41_SERVED and hook[hook.index("--context") + 1] == str(context)
    assert hook[1] == "/opt/tensorfold-readiness/readiness.py"
    assert recipe.mods[0] == "../mods/tensorfold-readiness"
    assert hook[hook.index("--lanes") + 1] == "4"
    assert hook[hook.index("--efforts") + 1] == "max,high,low,none"


def test_dsv41_rank_inputs_follow_the_weights_in_a_root_of_their_own(runtime, dsv41_recipe):
    recipe = Recipe.from_dict(yaml.safe_load(dsv41_recipe.read_text()))
    settings = resolve_runtime_cache_settings(runtime=runtime, recipe=recipe)
    assert settings.key_by_image is False
    root = resolve_runtime_cache_root(settings, "/home/kelchm/.cache/sparkrun")
    assert root == "/opt/spark-cache/runtime-inputs"
    mounts = build_runtime_cache_mounts(runtime=runtime, recipe=recipe, settings=settings, root=root, image=recipe.container)
    assert mounts.leaf == f"{root}/tensorfold/LibertAIDAI__DeepSeek-V4.1-Flash-1017-EXL3-2.9bp-55410efc"
    # Another TensorFold recipe's sweep ages siblings under the shared root only.
    other = _recipe()
    shared = resolve_runtime_cache_root(resolve_runtime_cache_settings(runtime=runtime, recipe=other), "/home/kelchm/.cache/sparkrun")
    assert not mounts.leaf.startswith(shared + "/")
    assert recipe.env["TF_DS_RANK_CACHE"].startswith(RUNTIME_CACHE_CONTAINER_PATH + "/")
    assert recipe.env["TF_DS_TOKEN_MAP"].startswith(RUNTIME_CACHE_CONTAINER_PATH + "/")


def test_dsv41_rdma_devices_take_exact_names_on_port_1(tmp_path, dsv41_recipe):
    _, _, serve = _dsv41_bundle(tmp_path, dsv41_recipe)
    devices = tmp_path / "infiniband"
    for name in ("rocep1s0f0", "roceP2p1s0f0"):
        (devices / name).mkdir(parents=True)
    hca = {"NCCL_IB_HCA": "=rocep1s0f0:1,roceP2p1s0f0"}
    assert serve.rdma_devices(hca, devices) == "rocep1s0f0,roceP2p1s0f0"
    assert serve.rdma_devices({**hca, "TF_RDMA_DEVICES": "rocep1s0f0"}, devices) == "rocep1s0f0"
    for selector, message in (("^rocep1s0f1", "exclusion"), ("rocep1s0f0:2", "port 1"), ("rocep", "in full")):
        with pytest.raises(SystemExit, match=message):
            serve.rdma_devices({"NCCL_IB_HCA": selector}, devices)
