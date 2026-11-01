"""GLM-5.3-Flash on Mia's kit at main bf5f9452: recipe, mod, site guards and discovery, without GPU weights."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
if str(PLUGINS) not in sys.path:
    sys.path.insert(0, str(PLUGINS))

from tensorfold import TensorFoldRuntime, hf_snapshot_path  # noqa: E402

from sparkrun.core.recipe import Recipe  # noqa: E402
from sparkrun.core.validation import ERROR  # noqa: E402
from sparkrun.orchestration.executors.docker import DockerExecutor  # noqa: E402
from sparkrun.runtimes._cluster_ops import ClusterContext  # noqa: E402

RECIPE = ROOT / "mia-tensorfold-glm53-exl3-tp2/glm53-exl3-tp2-mia-tf-bf5f9452-mia-1m.yaml"
EXPANDABLE = RECIPE.with_name("glm53-exl3-tp2-mia-tf-bf5f9452-mia-1m-expandable.yaml")
MOD = ROOT / "mods/mia-tensorfold-glm53-bf5f9452"
SERVED = "GLM-5.3-Flash"
HEAD = "172.16.21.31"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recipe():
    return Recipe(yaml.safe_load(RECIPE.read_text()), source_path=str(RECIPE))


def _env(command):
    tokens = shlex.split(command)
    env = {}
    for token in tokens[1:]:
        if "=" not in token or token.startswith("/"):
            break
        key, _, value = token.partition("=")
        env[key] = value
    return env, tokens[len(env) + 1:]


def test_recipe_pins_the_kit_and_states_its_launcher_defaults():
    recipe, lock = _recipe(), json.loads((MOD / "upstream.lock.json").read_text())
    assert recipe.name == recipe.metadata["profile"] == RECIPE.stem
    assert recipe.container == lock["image"]
    for field in ("kit_revision", "engine_revision", "upstream_patch_count", "upstream_patch_hash"):
        assert recipe.metadata[field] == lock[field], field
    # Every variable the pinned scripts/config.sh exports, with the same value.
    assert len(lock["launcher_env"]) == 45 and lock["launcher_env"]["TF_GLM_FILL_ROWS"] == "2048"
    assert (lock["launcher_env"]["TENSORFOLD_GLM_REQUEST_IMAGE_TOKENS"], lock["launcher_env"]["TENSORFOLD_VISION_WORKSPACE_MIB"]) == \
        ("102400", "1440")
    assert {key: recipe.env.get(key) for key in lock["launcher_env"]} == lock["launcher_env"]
    engine_only = {key for key in set(recipe.env) - set(lock["launcher_env"]) if key.startswith(("TENSORFOLD_", "TF_"))}
    assert engine_only == {
        "TF_GLM_LOOP_GUARD", "TF_GLM_KEEP_REASONING", "TF_GLM_KEEP_REASONING_MB", "TENSORFOLD_GLM_IMAGE_TOKENS",
        "TENSORFOLD_GLM_VIDEO_TOKENS", "TENSORFOLD_GLM_VIDEO_FRAMES", "TENSORFOLD_GLM_MAX_IMAGES",
        "TENSORFOLD_GLM_MAX_VIDEOS", "TENSORFOLD_GLM_REQUEST_VIDEO_TOKENS"}
    assert recipe.env["TENSORFOLD_NUCLEUS_UNION"] == "1"
    for key, off in (("TF_GLM_EFFORT_TAIL", "0"), ("TF_GLM_CACHE_SHARE_PCT", "0"), ("TF_GLM_LOOP_GUARD", "0"),
                     ("TF_GLM_KEPT_BYTES_GIB", "0"), ("TF_GLM_KEEP_PER_CHAT", "0"), ("TF_GLM_EXL3_DEC_ORDER", "0"),
                     ("TF_GLM_DISPLAY_KV_MIB", "0"), ("TF_GLM_DISPLAY_KV_BACKEND", "drm"), ("TF_GLM_MAX_QUEUED", ""),
                     ("TF_GLM_MTP", "auto"), ("TF_GLM_SPILL_QUOTA", "0"), ("TF_GLM_PREFIX_WAIT", "0")):
        assert recipe.env[key] == off, key
    assert not [key for key in recipe.env if "KV_SHARD" in key or key == "TF_GLM_EXIT_ON_BROKEN"]
    assert (recipe.env["TENSORFOLD_MEMORY_RESERVE_GIB"], recipe.env["TF_GLM_CACHE_GIB"]) == ("14.5", "12.5")
    assert recipe.metadata["site_backports"] == "exact-tool-turn-reasoning, strict-concurrent-health, models-discovery"


def test_expandable_profile_changes_only_the_allocator_mode():
    base, expandable = yaml.safe_load(RECIPE.read_text()), yaml.safe_load(EXPANDABLE.read_text())
    assert expandable["env"].pop("PYTORCH_CUDA_ALLOC_CONF") == "expandable_segments:True"
    for key in ("profile", "serving_profile", "description"):
        expandable["metadata"].pop(key), base["metadata"].pop(key)
    assert expandable == base


def test_expandable_allocator_mode_reaches_the_rank_container():
    recipe = Recipe(yaml.safe_load(EXPANDABLE.read_text()), source_path=str(EXPANDABLE))
    ctx = ClusterContext.build(TensorFoldRuntime(), [HEAD, "172.16.21.32"], recipe.container, "tf-profile-test",
                               recipe.env, None, None, dry_run=True, recipe=recipe)
    script = DockerExecutor().generate_launch_script(image=recipe.container, container_name="tf-profile-test",
                                                     command="sleep infinity", env=ctx.all_env, volumes=ctx.volumes)
    assert "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True" in shlex.split(script)


def test_recipe_uses_the_shared_gate_with_glms_own_levels():
    recipe = _recipe()
    assert recipe.mods == ["../mods/tensorfold-readiness", "../mods/mia-tensorfold-glm53-bf5f9452"]
    hook = shlex.split(recipe.post_exec[0])
    assert hook[:2] == ["python3", "/opt/tensorfold-readiness/readiness.py"]
    for flag, value in (("--model", SERVED), ("--context", "1048576"), ("--lanes", "4"),
                        ("--efforts", "max,high,low,none"), ("--min-pool-tokens", "1200000")):
        assert hook[hook.index(flag) + 1] == value, flag


def test_rank_commands_reach_mias_cli():
    runtime, recipe = TensorFoldRuntime(), _recipe()
    assert not [issue for issue in runtime.validate_recipe(recipe) if getattr(issue, "severity", None) == ERROR]
    ctx = ClusterContext.build(
        runtime, [HEAD, "172.16.21.32"], recipe.container, "tf-profile-test",
        recipe.env, None, None, dry_run=True, recipe=recipe,
    )
    script = DockerExecutor().generate_launch_script(
        image=recipe.container, container_name="tf-profile-test", command="sleep infinity",
        env=ctx.all_env, volumes=ctx.volumes,
    )
    assert "NCCL_CUMEM_ENABLE" not in script
    for setting in ("TENSORFOLD_NUCLEUS_UNION=1", "TF_ROCE_WAIT_S=300", "TF_GLM_MAX_QUEUED=", "TF_GLM_DISPLAY_KV_MIB=0"):
        assert setting in shlex.split(script)
    assert recipe.runtime_cache["key_by_image"] is True
    serve = _load("glm_serve", MOD / "serve.py")
    for rank in (0, 1):
        env, argv = _env(runtime.generate_node_command(
            recipe, {}, head_ip=HEAD, num_nodes=2, node_rank=rank, init_port=29511, hosts=[HEAD, "172.16.21.32"]))
        assert argv == ["/usr/local/bin/tensorfold-entrypoint"]
        assert env["MODEL_PATH"] == hf_snapshot_path(recipe.model, recipe.model_revision)
        assert env["DRAFTER"] == hf_snapshot_path(recipe.defaults["drafter"], recipe.defaults["drafter_revision"])
        args = serve.command(env)
        assert args[:3] == ["tensorfold", "serve", env["MODEL_PATH"]]
        for flag, expected in (("--tp", "2"), ("--rank", str(rank)), ("--master", HEAD), ("--master-port", "29511"),
                               ("--context", "1048576"), ("--parallel", "4"), ("--max-tokens", "32768")):
            assert args[args.index(flag) + 1] == expected
        assert not [arg for arg in args if arg.startswith(("--spill", "--snapshot-dir", "--mtp", "--no-drafts"))]
        assert ("--name" in args) is (rank == 0) and "--thinking" in args and "--vision" in args
        if rank == 0:
            assert args[args.index("--name") + 1] == recipe.defaults["served_model_name"] == "GLM-5.3-Flash"
    source = (MOD / "serve.py").read_text()
    assert source.index('"/proc/self/oom_score_adj"') < source.index("os.execvp")


def test_ranks_meet_on_the_fabric_address_when_sparkrun_supplies_it():
    serve = _load("glm_serve", MOD / "serve.py")
    env = {"MASTER": HEAD, "MASTER_PORT": "29511", "MODEL_PATH": "/m", "DRAFTER": "/d", "CONTEXT": "1048576",
           "MAX_TOKENS": "32768", "SERVED_NAME": "GLM-5.3-Flash", "HOST": "0.0.0.0", "PORT": "8888"}
    for rank in ("0", "1"):
        for extra, master in (({}, HEAD), ({"FABRIC_MASTER": "172.16.240.11"}, "172.16.240.11")):
            args = serve.command({**env, "RANK": rank, **extra})
            assert args[args.index("--master") + 1] == master
            assert args[args.index("--master-port") + 1] == "29511"


@pytest.mark.parametrize("change", ("content", "symlink"))
def test_mod_is_frozen_and_self_contained(tmp_path, change):
    bundle = tmp_path / MOD.name
    shutil.copytree(MOD, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    check = [sys.executable, str(bundle / "prepare.py"), "--check"]
    subprocess.run(check, check=True, capture_output=True)
    helper = bundle / "serve.py"
    if change == "content":
        helper.write_text(helper.read_text() + "\n# Unreviewed change\n")
    else:
        outside = tmp_path / "shared-serve.py"
        helper.rename(outside)
        helper.symlink_to(outside)
    result = subprocess.run(check, capture_output=True, text=True)
    assert result.returncode != 0 and "Missing or changed frozen mod file: serve.py" in result.stderr


def test_bundle_has_no_sibling_or_linked_fallback(tmp_path):
    for name in (MOD.name, "mia-tensorfold-sibling"):
        shutil.copytree(MOD, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    bundle = tmp_path / MOD.name
    check = [sys.executable, str(bundle / "prepare.py"), "--check"]
    subprocess.run(check, check=True, capture_output=True)
    for name in ("serve.py", "site_fixes.py"):
        (bundle / name).rename(bundle / (name + ".moved"))
        result = subprocess.run(check, capture_output=True, text=True)
        assert result.returncode != 0 and f"Missing or changed frozen mod file: {name}" in result.stderr
        (bundle / (name + ".moved")).rename(bundle / name)
    manifest = bundle / "upstream.lock.json"
    manifest.rename(tmp_path / "shared.lock.json")
    manifest.symlink_to(tmp_path / "shared.lock.json")
    result = subprocess.run(check, capture_output=True, text=True)
    assert result.returncode != 0 and "Missing or linked Mia TensorFold mod manifest" in result.stderr


def test_a_linked_prepare_checks_the_bundle_it_runs_from(tmp_path):
    pristine, bundle = tmp_path / "pristine", tmp_path / MOD.name
    for target in (pristine, bundle):
        shutil.copytree(MOD, target, ignore=shutil.ignore_patterns("__pycache__"))
    (bundle / "serve.py").write_text("# Unreviewed\n")
    (bundle / "prepare.py").unlink()
    (bundle / "prepare.py").symlink_to(pristine / "prepare.py")
    result = subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], capture_output=True, text=True)
    assert result.returncode != 0


def _stand_in_tree(tmp_path):
    """Small sources carrying the real anchors; the real pins accept only the pinned image's files."""
    bundle = tmp_path / MOD.name
    shutil.copytree(MOD, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    fixes = _load("glm_site_fixes", bundle / "site_fixes.py")
    sources = {
        "families/glm5_next/cuda/kept_reasoning.py": (fixes.reasoning, '"""Stand-in.\n\n'
            'ids). Here the signature covers the whole conversation before the calls, not only the last user message, '
            'so a reply\nis only ever put back into the conversation it was written for.\n"""\n'
            'def _keys(history, calls):\n    return _ids(calls) + [signature(history, calls)]\n'),
        "cuda/health.py": (fixes.health, "def snapshot(app, scheduler=None, decoder=None):\n    if app:\n"
                                         "        body = {}\n        return body\n"),
        "cuda/http.py": (fixes.http,
            '_TOKENIZER_ROUTES = ("/tokenize", "/v1/tokenize", "/detokenize", "/v1/detokenize")\n\n\n'
            "def get(self, app, health):\n    if app:\n"
            '        if self.path.rstrip("/") in ("/v1/models", "/models"):\n'
            '                self._json(200, {"object": "list", "data": [{"id": model_id, "object": "model", "owned_by": "tensorfold"}\n'
            "                                                            for model_id in app.model_ids]})\n"
            "        elif health:\n"
            "                self._json(200, health.of(app).snapshot(app))\n"),
    }
    root = tmp_path / "tensorfold"
    lock = json.loads((bundle / "upstream.lock.json").read_text())
    for name, (transform, text) in sources.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
        lock["installed_files"][name] = {"upstream_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                         "site_sha256": hashlib.sha256(transform(text).encode()).hexdigest()}
    (bundle / "upstream.lock.json").write_text(json.dumps(lock))
    return fixes, root


def test_site_guards_refuse_other_sources_and_apply_once(tmp_path):
    fixes, root = _stand_in_tree(tmp_path)
    upstream = {str(path.relative_to(root)): path.read_text() for path in root.rglob("*.py")}
    read = lambda: {name: (root / name).read_text() for name in upstream}
    result = subprocess.run([sys.executable, str(MOD / "site_fixes.py"), "--root", str(root)], capture_output=True, text=True)
    assert result.returncode != 0 and "installed source drifted" in result.stderr
    assert read() == upstream
    last = "cuda/http.py"
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
    guarded = {name: (root / name).read_text()
               for name in ("cuda/http.py", "cuda/health.py", "families/glm5_next/cuda/kept_reasoning.py")}
    assert all(guarded[name] != upstream[name] for name in upstream)
    compile(guarded["cuda/health.py"], "health.py", "exec")
    reasoning = guarded["families/glm5_next/cuda/kept_reasoning.py"]
    assert "    return _ids(calls)\n" in reasoning and "[signature(history, calls)]" not in reasoning
    assert "_model_entry(app, model_id) for model_id in app.model_ids" in guarded["cuda/http.py"]
    assert '200 if report.get("ok") is True else 503' in guarded["cuda/http.py"]
    compile(guarded["cuda/http.py"], "http.py", "exec")
    fixes.apply(root)
    assert read() == guarded
    (root / last).write_text(upstream[last])
    fixes.apply(root)
    assert read() == guarded


def test_discovery_reports_the_window_and_the_templates_levels():
    fixes = _load("glm_site_fixes_discovery", MOD / "site_fixes.py")
    ns = {}
    exec(fixes.DISCOVERY, ns)

    def app(efforts, window=1048576, thinking=True):
        return types.SimpleNamespace(template=types.SimpleNamespace(efforts=frozenset(efforts)),
                                     default_thinking=thinking, effective_context_window=window)

    assert ns["_model_entry"](app({"low", "high", "max"}), SERVED) == {
        "id": SERVED, "object": "model", "owned_by": "tensorfold", "context_length": 1048576, "max_model_len": 1048576,
        "reasoning": {"default_enabled": True, "supported_efforts": ["max", "high", "low", "none"]}}
    assert ns["_model_entry"](app(set(), window=None), SERVED) == {"id": SERVED, "object": "model", "owned_by": "tensorfold"}
