"""Qwen3.8-Flash-Next on Mia's v1.1 kit with the discovery patches: recipe, mod, gate and discovery, without GPU weights."""

from __future__ import annotations

import hashlib
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
from sparkrun.orchestration.executors.docker import DockerExecutor  # noqa: E402
from sparkrun.runtimes._cluster_ops import ClusterContext  # noqa: E402

RECIPES = ROOT / "mia-tensorfold-qwen38fn-tp2"
RECIPE = RECIPES / "qwen38fn-nvfp4-tp2-mia-tf-v1.1-discovery-nvidia-480k.yaml"
MOD = ROOT / "mods/mia-tensorfold-qwen38fn-v1.1-discovery"
SERVED = "Qwen3.8-Flash-Next"
WINDOW = 491520
# The shortest prompt the kit's issue #3 reports stalling the two ranks' control link.
STALL = 503359
EFFORTS = ["xhigh", "medium", "low", "none"]
ENTRY = {
    "id": SERVED, "object": "model", "created": 0, "owned_by": "tensorfold",
    "context_length": WINDOW, "max_model_len": WINDOW,
    "reasoning": {"default_enabled": True, "supported_efforts": EFFORTS},
}
HEAD, WORKER = "172.16.21.31", "172.16.21.32"
FABRIC_HEAD = "198.18.0.1"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recipe(path=RECIPE):
    return Recipe(yaml.safe_load(path.read_text()), source_path=str(path))


def _env(command):
    tokens = shlex.split(command)
    env = {}
    for token in tokens[1:]:
        if "=" not in token or token.startswith("/"):
            break
        key, _, value = token.partition("=")
        env[key] = value
    return env, tokens[len(env) + 1:]


def _bundle(tmp_path, binary=None):
    bundle = tmp_path / MOD.name
    shutil.copytree(MOD, bundle, ignore=shutil.ignore_patterns("__pycache__", "out"))
    if binary is not None:
        (bundle / "out").mkdir()
        (bundle / "out/tensorfold-native").write_bytes(binary)
        lock = json.loads((bundle / "upstream.lock.json").read_text())
        lock["binary_sha256"] = hashlib.sha256(binary).hexdigest()
        (bundle / "upstream.lock.json").write_text(json.dumps(lock))
    return bundle


def test_recipe_pins_the_kit_and_weights_with_its_launcher_settings():
    recipe, lock = _recipe(), json.loads((MOD / "upstream.lock.json").read_text())
    assert recipe.name == recipe.metadata["profile"] == RECIPE.stem
    assert recipe.container == lock["image"]
    assert {"repository": recipe.model, "revision": recipe.model_revision} == lock["weights"]
    for field in ("kit_revision", "engine_revision", "upstream_patch_count", "upstream_patch_hash"):
        assert recipe.metadata[field] == lock[field], field
    assert recipe.metadata["server_patch_count"] == len(lock["server_patches"]) + len(lock["site_patches"]) == 6
    assert recipe.env == {
        "NCCL_MIN_NCHANNELS": "4", "NCCL_MAX_NCHANNELS": "4", "TENSORFOLD_NO_UPDATE_CHECK": "1",
        "TENSORFOLD_MEMORY_RESERVE_GIB": "10", "TENSORFOLD_VISION_WORKSPACE_MIB": "2048",
        "TF_FLASHNEXT_YARN": "4", "TF_FLASHNEXT_ROCE": "1", "TF_FLASHNEXT_L2PF": "1", "TF_FLASHNEXT_DEPTH": "15",
        "TENSORFOLD_MAX_IMAGES": "50", "TENSORFOLD_IMAGE_TOKENS": "16384", "TENSORFOLD_VIDEO_TOKENS": "16384",
    }
    assert not [issue for issue in TensorFoldRuntime().validate_recipe(recipe) if getattr(issue, "severity", None) == ERROR]
    assert recipe.defaults["context"] == WINDOW < STALL
    assert recipe.defaults["entrypoint"] == "/usr/local/bin/tensorfold-entrypoint"


def test_recipe_uses_the_shared_gate_with_qwens_levels():
    recipe = _recipe()
    assert recipe.mods == ["../mods/tensorfold-readiness", f"../mods/{MOD.name}"]
    hook = shlex.split(recipe.post_exec[0])
    assert hook[:2] == ["python3", "/opt/tensorfold-readiness/readiness.py"]
    for flag, value in (("--model", SERVED), ("--context", str(WINDOW)), ("--lanes", "16"),
                        ("--efforts", ",".join(EFFORTS))):
        assert hook[hook.index(flag) + 1] == value, flag
    # The kit's server maps each other level name as the gate expects: the October 13 echoes.
    ready = _load("qwen_gate", ROOT / "mods/tensorfold-readiness/readiness.py")
    levels = EFFORTS[:-1]
    assert {name: ready.nearest(name, levels) for name in ("minimal", "high", "max")} == {
        "minimal": "low", "high": "xhigh", "max": "xhigh"}


def test_rank_commands_link_over_the_heads_fabric_address(monkeypatch):
    runtime, recipe = TensorFoldRuntime(), _recipe()
    ctx = ClusterContext.build(
        runtime, [HEAD, WORKER], recipe.container, "tf-profile-test",
        recipe.env, None, None, dry_run=True, recipe=recipe,
    )
    script = shlex.split(DockerExecutor().generate_launch_script(
        image=recipe.container, container_name="tf-profile-test", command="sleep infinity",
        env=ctx.all_env, volumes=ctx.volumes,
    ))
    for setting in ("TF_FLASHNEXT_YARN=4", "TF_FLASHNEXT_ROCE=1", "TENSORFOLD_MEMORY_RESERVE_GIB=10",
                    "NCCL_MIN_NCHANNELS=4", "NCCL_MAX_NCHANNELS=4"):
        assert setting in script
    serve = _load("qwen_serve", MOD / "serve.py")
    seen = {}

    def native(**kwargs):
        for rank in (0, 1):
            seen[rank] = _env(runtime.generate_node_command(
                recipe, {}, head_ip=HEAD, num_nodes=2, node_rank=rank, init_port=29511, hosts=[HEAD, WORKER]))
        return 0

    monkeypatch.setattr(runtime, "_run_native_cluster", native)
    assert runtime._run_cluster([HEAD, WORKER], recipe.container, recipe=recipe,
                                ib_ip_map={HEAD: FABRIC_HEAD, WORKER: "198.18.0.2"}) == 0
    for rank in (0, 1):
        env, argv = seen[rank]
        assert argv == ["/usr/local/bin/tensorfold-entrypoint"]
        assert "DRAFTER" not in env
        # The rank command inherits the recipe env from its container.
        args = serve.command({**recipe.env, **env})
        assert args[:3] == ["/opt/tensorfold-mod/bin/tensorfold-native", "serve",
                            hf_snapshot_path(recipe.model, recipe.model_revision)]
        for flag, expected in (("--name", SERVED), ("--port", "8888"), ("--context", str(WINDOW)),
                               ("--parallel", "16"), ("--max-tokens", "32768"), ("--kv-dtype", "fp8"),
                               ("--vision-max-images", "50"), ("--vision-max-videos", "4"),
                               ("--vision-image-tokens", "16384"), ("--tp", "2"), ("--rank", str(rank)),
                               ("--master", FABRIC_HEAD), ("--master-port", "29511")):
            assert args[args.index(flag) + 1] == expected, flag
        assert "--vision" in args and "--thinking" in args and "--no-drafts" not in args
        for missing in ({}, {"FABRIC_MASTER": " "}):
            bare = {key: value for key, value in {**recipe.env, **env}.items() if key != "FABRIC_MASTER"}
            with pytest.raises(SystemExit, match="FABRIC_MASTER"):
                serve.command({**bare, **missing})
    source = (MOD / "serve.py").read_text()
    assert source.index('"/proc/self/oom_score_adj"') < source.index("os.execv")


def test_run_installs_the_verified_binary_and_launcher_at_the_shared_paths():
    lines = [line for line in (MOD / "run.sh").read_text().splitlines() if line and not line.startswith("#")]
    assert lines[2:] == [
        "python3 prepare.py --check --require-binary",
        "install -D -m 0755 out/tensorfold-native /opt/tensorfold-mod/bin/tensorfold-native",
        "install -m 0755 serve.py /usr/local/bin/tensorfold-entrypoint",
    ]
    assert (MOD / ".gitignore").read_text() == "/out/\n"


def test_bundle_requires_the_locked_binary(tmp_path):
    bundle = _bundle(tmp_path, b"rebuilt")
    prepare = [sys.executable, str(bundle / "prepare.py")]
    subprocess.run([*prepare, "--check", "--require-binary"], check=True, capture_output=True)
    (bundle / "out/tensorfold-native").write_bytes(b"tampered")
    result = subprocess.run([*prepare, "--check"], capture_output=True, text=True)
    assert result.returncode != 0 and "Rebuilt binary does not match the lock" in result.stderr


@pytest.mark.parametrize("name", ("serve.py", "run.sh", "build/Dockerfile", "patches/0011-models-reasoning.patch",
                                  "patches/0012-record-notification-first.patch"))
def test_bundle_rejects_changed_files_and_a_missing_binary(tmp_path, name):
    bundle = _bundle(tmp_path)
    prepare = [sys.executable, str(bundle / "prepare.py")]
    subprocess.run([*prepare, "--check"], check=True, capture_output=True)
    result = subprocess.run([*prepare, "--check", "--require-binary"], capture_output=True, text=True)
    assert result.returncode != 0 and "Missing rebuilt binary" in result.stderr
    (bundle / name).write_text((bundle / name).read_text() + "\n# Unreviewed change\n")
    result = subprocess.run([*prepare, "--check"], capture_output=True, text=True)
    assert result.returncode != 0 and f"Missing or changed frozen mod file: {name}" in result.stderr


def test_site_patches_are_the_recorded_copies_of_their_sources(tmp_path):
    lock = json.loads((MOD / "upstream.lock.json").read_text())
    assert [patch["fixes"].rsplit("#", 1)[1] for patch in lock["site_patches"]] == ["3", "6", "9", "8"]
    # Applied in file order, after the discovery patches.
    files = [patch["file"] for patch in lock["server_patches"] + lock["site_patches"]]
    assert files == sorted(files) == sorted(str(p.relative_to(MOD)) for p in (MOD / "patches").iterdir())
    for patch in lock["site_patches"]:
        assert patch["source"] and patch["author"] and patch["license"] == "Apache-2.0"
        assert patch["sha256"] == lock["mod_files"][patch["file"]] == hashlib.sha256((MOD / patch["file"]).read_bytes()).hexdigest()
    bundle = _bundle(tmp_path)
    changed = json.loads((bundle / "upstream.lock.json").read_text())
    changed["site_patches"][0]["sha256"] = "0" * 64
    (bundle / "upstream.lock.json").write_text(json.dumps(changed))
    result = subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], capture_output=True, text=True)
    assert result.returncode != 0 and "is not the recorded copy of" in result.stderr


def test_record_binary_writes_the_lock_only_after_every_check(tmp_path):
    bundle = _bundle(tmp_path, b"rebuilt")
    before = (bundle / "upstream.lock.json").read_text()
    (bundle / "serve.py").write_text("# Unreviewed\n")
    (bundle / "out/tensorfold-native").write_bytes(b"rebuilt again")
    result = subprocess.run([sys.executable, str(bundle / "prepare.py"), "--record-binary"], capture_output=True, text=True)
    assert result.returncode != 0 and "serve.py" in result.stderr
    assert (bundle / "upstream.lock.json").read_text() == before


@pytest.mark.parametrize("directory", ("patches", "build", "out"))
def test_bundle_refuses_a_linked_directory(tmp_path, directory):
    bundle = _bundle(tmp_path, b"rebuilt")
    elsewhere = tmp_path / f"{directory}-elsewhere"
    (bundle / directory).rename(elsewhere)
    (bundle / directory).symlink_to(elsewhere)
    result = subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], capture_output=True, text=True)
    assert result.returncode != 0 and f"Linked {directory}/" in result.stderr


def test_a_linked_prepare_checks_the_bundle_it_runs_from(tmp_path):
    pristine, bundle = _bundle(tmp_path / "pristine"), _bundle(tmp_path)
    (bundle / "serve.py").write_text("# Unreviewed\n")
    (bundle / "prepare.py").unlink()
    (bundle / "prepare.py").symlink_to(pristine / "prepare.py")
    result = subprocess.run([sys.executable, str(bundle / "prepare.py"), "--check"], capture_output=True, text=True)
    assert result.returncode != 0


def test_recipe_and_mod_name_no_lab_values():
    lab = re.compile(r"\b(?!(127\.0\.0\.1|0\.0\.0\.0)\b)\d{1,3}(\.\d{1,3}){3}\b|/home/|/opt/spark|"
                     r"NCCL_IB_(HCA|GID_INDEX)\s*:")
    files = [RECIPE] + [path for path in MOD.rglob("*") if path.is_file() and "out" not in path.relative_to(MOD).parts
                        and "__pycache__" not in path.parts and path.suffix != ".patch"]
    assert [path.name for path in files if lab.search(path.read_text())] == []


def test_models_entry_passes_the_shared_gate_and_the_live_check():
    ready = _load("qwen_gate", ROOT / "mods/tensorfold-readiness/readiness.py")
    live = _load("qwen_live", Path(__file__).with_name("qwen_model_discovery.py"))
    served = {"object": "list", "data": [ENTRY]}
    ready.check_models(served, SERVED, WINDOW, EFFORTS)
    # The published v1.1 server lists the model with no window, which is what a missing overlay would serve.
    published = {"object": "list", "data": [{key: ENTRY[key] for key in ("id", "object", "created", "owned_by")}]}
    for body, context, efforts in ((published, WINDOW, EFFORTS), (served, 1048576, EFFORTS),
                                   (served, WINDOW, ["xhigh", "low", "none"]), ({"object": "list", "data": []}, WINDOW, EFFORTS)):
        with pytest.raises(RuntimeError):
            ready.check_models(body, SERVED, context, efforts)

    reasoning = live.check_entry(ENTRY, WINDOW)
    for change in ({"context_length": 524288}, {"default_max_tokens": 32768}, {"reasoning": None},
                   {"reasoning": {**reasoning, "supported_efforts": ["xhigh", "low", "low"]}},
                   {"reasoning": {**reasoning, "supported_efforts": ["xhigh", "turbo"]}},
                   {"reasoning": {**reasoning, "supported_efforts": []}},
                   {"reasoning": {**reasoning, "supported_efforts": ["xhigh", "low", "none"]}},
                   {"reasoning": {**reasoning, "supported_efforts": ["medium", "xhigh", "low", "none"]}},
                   {"reasoning": {**reasoning, "default_effort": "high"}}):
        with pytest.raises(RuntimeError):
            live.check_entry({**ENTRY, **change}, WINDOW)
    with pytest.raises(RuntimeError):
        live.check_entry(ENTRY, 1048576)
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
