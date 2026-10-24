"""Qwen3.8-Flash-Next on one Spark from Mia's one-Spark kit: recipe, mod, gate and launch, without a GPU or weights."""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import shutil
import subprocess
import sys
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

RECIPES = sorted((ROOT / "mia-tensorfold-qwen38fn-tp1").glob("*.yaml"))
MOD = ROOT / "mods/mia-tensorfold-qwen38fn-tp1-656879fd"
TWO_SPARK_MOD = ROOT / "mods/mia-tensorfold-qwen38fn-v1.1-discovery"
SERVED = "Qwen3.8-Flash-Next"
EFFORTS = ["xhigh", "medium", "low", "none"]
NATIVE = 262144
# The kit's scripts/config.sh exports at its defaults, apart from the YaRN factor, which follows the window, and the
# kit's opt-in system-memory cache, whose fences keep a request that fails after admission from poisoning the context.
KIT_ENV = {
    "TENSORFOLD_NO_UPDATE_CHECK": "1", "TENSORFOLD_MEMORY_RESERVE_GIB": "10", "TENSORFOLD_VISION_WORKSPACE_MIB": "2048",
    "TENSORFOLD_MAX_IMAGES": "50", "TENSORFOLD_IMAGE_TOKENS": "16384", "TENSORFOLD_VIDEO_TOKENS": "16384",
    "TF_FLASHNEXT_DEPTH": "6", "TF_FLASHNEXT_PRODUCT_STREAMS": "5", "TF_FLASHNEXT_PREFILL_TAIL": "512",
    "TF_FLASHNEXT_PROMPT_EXPERT_TILE": "2", "TF_FLASHNEXT_SYSTEM_CACHE": "1", "TF_FLASHNEXT_RING_CACHE": "1",
}


@pytest.fixture(params=RECIPES, ids=lambda path: path.stem)
def recipe_path(request):
    return request.param


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recipe(path):
    return Recipe(yaml.safe_load(path.read_text()), source_path=str(path))


def _lock():
    return json.loads((MOD / "upstream.lock.json").read_text())


def _env(command):
    tokens = shlex.split(command)
    assert tokens[0] == "env"
    env = {}
    for token in tokens[1:]:
        if "=" not in token or token.startswith("/"):
            break
        key, _, value = token.partition("=")
        env[key] = value
    return env, tokens[len(env) + 1:]


def _bundle(tmp_path):
    bundle = tmp_path / MOD.name
    shutil.copytree(MOD, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    return bundle


def test_recipe_pins_the_kit_image_and_weights_with_the_kits_settings(recipe_path):
    recipe, lock = _recipe(recipe_path), _lock()
    assert recipe.name == recipe.metadata["profile"] == recipe_path.stem
    assert recipe.container == lock["image_id"] and recipe.container.startswith("sha256:")
    assert {"repository": recipe.model, "revision": recipe.model_revision} == lock["weights"]
    assert recipe.model_revision == recipe.metadata["target_model_revision"]
    for field in ("kit_revision", "engine_revision"):
        assert recipe.metadata[field] == lock[field], field
    assert recipe.metadata["upstream_patch_count"] == lock["kit_patch_count"] == 15
    assert recipe.metadata["server_patch_count"] == len(lock["server_patches"]) == 2
    assert (recipe.min_nodes, recipe.max_nodes, recipe.defaults["tensor_parallel"]) == (1, 1, 1)
    assert not [issue for issue in TensorFoldRuntime().validate_recipe(recipe) if getattr(issue, "severity", None) == ERROR]
    window = recipe.defaults["context"]
    # Qwen's YaRN factor of 4 only past the trained window, as the kit's config.sh sets it.
    assert recipe.env == {**KIT_ENV, "TF_FLASHNEXT_YARN": "0" if window <= NATIVE else "4"}
    assert window <= 4 * NATIVE
    assert recipe.defaults["entrypoint"] == "/usr/local/bin/tensorfold-entrypoint"
    assert {"memlock=-1:-1", "stack=67108864"} <= set(recipe.executor_config["ulimit"])


def test_recipe_uses_the_shared_gate_with_qwens_levels(recipe_path):
    recipe = _recipe(recipe_path)
    assert recipe.mods == ["../mods/tensorfold-readiness", f"../mods/{MOD.name}"]
    hook = shlex.split(recipe.post_exec[0])
    assert hook[:2] == ["python3", "/opt/tensorfold-readiness/readiness.py"]
    for flag, value in (("--model", SERVED), ("--context", str(recipe.defaults["context"])), ("--lanes", "5"),
                        ("--efforts", ",".join(EFFORTS))):
        assert hook[hook.index(flag) + 1] == value, flag


def test_one_node_launch_runs_the_kits_serve_command(recipe_path, monkeypatch):
    runtime, recipe = TensorFoldRuntime(), _recipe(recipe_path)
    serve = _load("qwen1_serve", MOD / "serve.py")
    env, argv = _env(runtime.generate_command(recipe, {}, is_cluster=False, num_nodes=1))
    assert argv == ["/usr/local/bin/tensorfold-entrypoint"]
    # The serve command inherits the recipe env from its container.
    args = serve.command({**recipe.env, **env})
    assert args[:3] == ["/opt/tensorfold/native/bin/tensorfold-native", "serve",
                        hf_snapshot_path(recipe.model, recipe.model_revision)]
    for flag, expected in (("--name", SERVED), ("--host", "0.0.0.0"), ("--port", "8888"),
                           ("--context", str(recipe.defaults["context"])), ("--parallel", "5"),
                           ("--max-tokens", "32768"), ("--temperature", "1.0"), ("--top-p", "0.95"), ("--top-k", "20"),
                           ("--kv-dtype", "fp8"), ("--vision-max-images", "50"), ("--vision-max-videos", "4"),
                           ("--vision-image-tokens", "16384")):
        assert args[args.index(flag) + 1] == expected, flag
    assert "--thinking" in args and "--vision" in args
    assert not {"--no-drafts", "--tp", "--rank", "--master", "--master-port"} & set(args)
    # A recipe's own flags come last, so they win over the kit's.
    assert serve.command({**recipe.env, **env, "EXTRA_ARGS": "--parallel 2"})[-2:] == ["--parallel", "2"]
    assert serve.command({**recipe.env, **env, "NO_DRAFTS": "1"})[-1] == "--no-drafts"
    for ranked in ({"RANK": "0"}, {"MASTER": "198.18.0.1"}):
        with pytest.raises(SystemExit, match="one rank"):
            serve.command({**recipe.env, **env, **ranked})
    # The adapter passes these when a recipe or override sets them; this engine has no flag for either.
    for setting in ({"MTP_DRAFTS": "3"}, {"DRAFTER": "/cache/huggingface/hub/drafter"}):
        with pytest.raises(SystemExit, match="TF_FLASHNEXT_DEPTH"):
            serve.command({**recipe.env, **env, **setting})
    env, _ = _env(runtime.generate_command(recipe, {"mtp_drafts": 3}, is_cluster=False, num_nodes=1))
    with pytest.raises(SystemExit, match="MTP_DRAFTS"):
        serve.command({**recipe.env, **env})
    source = (MOD / "serve.py").read_text()
    assert source.index('"/proc/self/oom_score_adj"') < source.index("os.execv")


def test_run_installs_the_launcher_at_the_shared_path():
    lines = [line for line in (MOD / "run.sh").read_text().splitlines() if line and not line.startswith("#")]
    assert lines[2:] == ["python3 prepare.py --check", "install -m 0755 serve.py /usr/local/bin/tensorfold-entrypoint"]


def test_discovery_patches_are_the_two_spark_profiles_backports():
    lock, two_spark = _lock(), json.loads((TWO_SPARK_MOD / "upstream.lock.json").read_text())
    assert [patch["file"] for patch in lock["server_patches"]] == sorted(
        str(path.relative_to(MOD)) for path in (MOD / "patches").iterdir())
    for mine, theirs in zip(lock["server_patches"], two_spark["server_patches"], strict=True):
        assert {k: v for k, v in mine.items() if k != "file"} == {k: v for k, v in theirs.items() if k != "file"}
        assert (MOD / mine["file"]).read_bytes() == (TWO_SPARK_MOD / theirs["file"]).read_bytes()


def test_build_takes_the_kit_and_engine_at_their_pins():
    lock = _lock()
    assert lock["engine_revision"] == json.loads((TWO_SPARK_MOD / "upstream.lock.json").read_text())["engine_revision"]
    assert re.fullmatch(r"[0-9a-f]{40}", lock["kit_revision"]) and re.fullmatch(r"[0-9a-f]{64}", lock["kit_patches_sha256"])
    assert lock["build"]["base_image"].startswith("nvcr.io/nvidia/pytorch@sha256:")
    assert all(re.fullmatch(r"[a-z0-9_.-]+==[0-9.]+", package) for package in lock["build"]["pip_packages"])
    build = (MOD / "build/build.sh").read_text()
    assert 'test "$(cat "$work"/kit/patches/*.patch | shasum -a 256 | cut -d\' \' -f1)" = "$(lock kit_patches_sha256)"' in build
    assert '"$work"/kit/patches/*.patch "$mod"/patches/*.patch' in build


@pytest.mark.parametrize("change", ["edit", "extra", "link", "linked-patches", "linked-build"])
def test_bundle_rejects_changed_added_and_linked_files(tmp_path, change):
    bundle = _bundle(tmp_path)
    check = [sys.executable, str(bundle / "prepare.py"), "--check"]
    subprocess.run(check, check=True, capture_output=True)
    if change == "edit":
        (bundle / "serve.py").write_text((bundle / "serve.py").read_text() + "\n# Unreviewed change\n")
    elif change == "extra":
        (bundle / "patches/0018-extra.patch").write_text("")
    elif change == "link":
        (bundle / "patches/0017-models-reasoning.patch").unlink()
        (bundle / "patches/0017-models-reasoning.patch").symlink_to(MOD / "patches/0017-models-reasoning.patch")
    else:
        # A directory linked to an identical copy outside the bundle is refused too.
        directory = change.removeprefix("linked-")
        elsewhere = tmp_path / f"{directory}-elsewhere"
        (bundle / directory).rename(elsewhere)
        (bundle / directory).symlink_to(elsewhere)
    assert subprocess.run(check, capture_output=True).returncode != 0


def test_bundle_refuses_a_linked_lock(tmp_path):
    bundle = _bundle(tmp_path)
    lock = bundle / "upstream.lock.json"
    (tmp_path / "lock.json").write_text(lock.read_text())
    lock.unlink()
    lock.symlink_to(tmp_path / "lock.json")
    result = subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], capture_output=True, text=True)
    assert result.returncode != 0 and "Missing or linked mod lock" in result.stderr


def test_recipe_and_mod_name_no_lab_values(recipe_path):
    lab = re.compile(r"\b(?!(127\.0\.0\.1|0\.0\.0\.0)\b)\d{1,3}(\.\d{1,3}){3}\b|/home/|/opt/spark|"
                     r"NCCL_IB_(HCA|GID_INDEX)\s*:")
    files = [recipe_path] + [path for path in MOD.rglob("*") if path.is_file() and "__pycache__" not in path.parts
                             and path.suffix != ".patch"]
    assert [path.name for path in files if lab.search(path.read_text())] == []


def test_models_entry_passes_the_shared_gate(recipe_path):
    window = _recipe(recipe_path).defaults["context"]
    ready = _load("qwen1_gate", ROOT / "mods/tensorfold-readiness/readiness.py")
    entry = {"id": SERVED, "object": "model", "created": 0, "owned_by": "tensorfold", "context_length": window,
             "max_model_len": window, "reasoning": {"default_enabled": True, "supported_efforts": EFFORTS}}
    ready.check_models({"object": "list", "data": [entry]}, SERVED, window, EFFORTS)
    # The kit's own server lists the model with no window, which is what a build without the backports would serve.
    bare = {key: entry[key] for key in ("id", "object", "created", "owned_by")}
    with pytest.raises(RuntimeError):
        ready.check_models({"object": "list", "data": [bare]}, SERVED, window, EFFORTS)
    assert {name: ready.nearest(name, EFFORTS[:-1]) for name in ("minimal", "high", "max")} == {
        "minimal": "low", "high": "xhigh", "max": "xhigh"}
