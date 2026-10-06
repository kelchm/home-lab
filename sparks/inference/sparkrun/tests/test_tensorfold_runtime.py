"""TensorFold SparkRun runtime adapter — discovery, topology, rendezvous, cache, health."""

from __future__ import annotations

import json
import importlib.util
import shlex
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import pytest
import yaml

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
if str(PLUGINS) not in sys.path:
    sys.path.insert(0, str(PLUGINS))

from tensorfold import (  # noqa: E402
    DEFAULT_CONTEXT,
    DEFAULT_SERVED_NAME,
    ENTRYPOINT,
    SPARKRUN_HEALTH_SEAM,
    TensorFoldRuntime,
    hf_snapshot_path,
    interpret_health,
    probe_health,
)

from sparkrun.core.bootstrap import get_runtime, init_sparkrun  # noqa: E402
from sparkrun.core.external_plugins import load_external_plugins  # noqa: E402
from sparkrun.core.log_source import MODE_FILE, SCOPE_ALL  # noqa: E402
from sparkrun.core.mods import resolve_and_inject_mods  # noqa: E402
from sparkrun.core.recipe import Recipe  # noqa: E402
from sparkrun.core.runtime_cache import (  # noqa: E402
    RUNTIME_CACHE_CONTAINER_PATH,
    build_runtime_cache_mounts,
    resolve_runtime_cache_settings,
)
from sparkrun.core.validation import ERROR  # noqa: E402
from sparkrun.orchestration.job_metadata import derive_recipe_fingerprint  # noqa: E402
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
]
FABRIC_HEAD = "172.16.21.31"
FABRIC_WORKER = "172.16.21.32"


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
            "served_model_name": DEFAULT_SERVED_NAME,
            "context": 850000,
            "drafter": DRAFT,
            "drafter_revision": DRAFT_REV,
            "max_tokens": 65536,
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
    assert rank0_env["CONTEXT"] == DEFAULT_CONTEXT
    assert rank0_env["SERVED_NAME"] == DEFAULT_SERVED_NAME
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
        _node_command(runtime, recipe, 0, overrides={"port": 9999, "served_model_name": DEFAULT_SERVED_NAME})
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
    if profile == "glm53-exl3-tp2-mia-tf-v1.8-mia-1m":
        assert "NCCL_CUMEM_ENABLE" not in script
    else:
        assert "NCCL_CUMEM_ENABLE=0" in shlex.split(script)
    if recipe.metadata["upstream_release"] == "v1.8":
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
    assert recipe.runtime_cache["key_by_image"] is True


def test_mia_profiles_have_separate_native_cache_namespaces(runtime):
    root = Path(__file__).resolve().parents[1] / MIA_RECIPE_DIR
    leaves = []
    for profile, _, _, _ in MIA_PROFILES:
        recipe = Recipe.from_dict(yaml.safe_load((root / f"{profile}.yaml").read_text()))
        mounts = build_runtime_cache_mounts(
            runtime=runtime, recipe=recipe,
            settings=resolve_runtime_cache_settings(runtime=runtime, recipe=recipe),
            root="/opt/spark-cache/huggingface/runtime-cache", image=recipe.container,
        )
        leaves.append(mounts.leaf)
    assert len(set(leaves)) == len(MIA_PROFILES)


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


@pytest.mark.parametrize("version", ("v1.2", "v1.5", "v1.8"))
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
        image="glm53-tensorfold:dev",
        fingerprint="abc",
    )
    assert mounts is not None
    for key in (
        "CUDA_CACHE_PATH",
        "TRITON_CACHE_DIR",
        "TORCH_EXTENSIONS_DIR",
        "GLM53_TF_PREPARED",
        "GLM53_TF_SESSION_DISK",
    ):
        assert mounts.env[key].startswith(RUNTIME_CACHE_CONTAINER_PATH + "/")
        assert "/cache/huggingface" not in mounts.env[key]
    extra = runtime.get_extra_env()
    assert extra["HF_HOME"] == "/cache/huggingface"
    assert extra["HF_HUB_CACHE"] == "/cache/huggingface/hub"
    assert extra["GLM53_TF_PREPARED_WRITE"] == "1"
    assert extra["GLM53_TF_HEALTH"] == "strict"
    assert extra["HF_HOME"] != mounts.env["GLM53_TF_PREPARED"]
    assert extra["HF_HUB_CACHE"] != mounts.env["GLM53_TF_SESSION_DISK"]


def test_health_fails_closed_on_ok_false(runtime):
    assert SPARKRUN_HEALTH_SEAM == "missing"
    assert interpret_health(200, {"ok": True}) is True
    assert runtime.interpret_health(200, {"ok": False, "fatal": "NCCL"}) is False
    assert interpret_health(200, {"ok": "true"}) is False
    assert interpret_health(200, {"status": "ok"}) is False
    assert interpret_health(200, "not-json") is False
    assert interpret_health(503, {"ok": False}) is False
    assert interpret_health(200, b'{"ok": true}') is True

    class Handler(BaseHTTPRequestHandler):
        payload = {"ok": True}
        code = 200

        def do_GET(self):
            body = json.dumps(type(self).payload).encode()
            self.send_response(type(self).code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            del fmt, args

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:%d/health" % server.server_address[1]
        assert probe_health(url) is True
        Handler.payload = {"ok": False, "fatal": "stalled"}
        Handler.code = 200
        assert probe_health(url) is False
        Handler.payload = {"ok": False}
        Handler.code = 503
        assert probe_health(url) is False
        Handler.payload = {"ok": True}
        Handler.code = 200
        assert probe_health(url) is True
    finally:
        server.shutdown()
        server.server_close()


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
