"""Bounded adapter regressions; these do not qualify GPU execution."""

from __future__ import annotations

import json
import hashlib
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


class FluxAdapterTests(unittest.TestCase):
    folder = "flux2-klein-nvfp4"

    def test_controller_can_execute_script_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "comfyui-job"
            shutil.copyfile(ROOT / self.folder / "source/comfyui_job.py", executable)
            executable.chmod(0o555)
            result = subprocess.run([str(executable), "--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--workflow-sha256", result.stdout)

    def test_http_artifact_directory_mounts_resolve_to_regular_model_files(self) -> None:
        namespace = runpy.run_path(str(ROOT / self.folder / "source/comfyui_job.py"))
        link = namespace["link_models"]
        document = json.loads((ROOT / self.folder / "source/workflows/flux-2-klein-4b.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for model in document["models"]:
                mount = base / "mounts" / model["artifact_id"]
                mount.mkdir(parents=True)
                (mount / model["source_filename"]).write_bytes(b"model test fixture")
            with patch.dict(link.__globals__, {"MODEL_MOUNT_ROOT": base / "mounts"}):
                link(document, base / "linked")
                for model in document["models"]:
                    target = base / "linked" / model["category"] / model["filename"]
                    self.assertTrue(target.is_file())
                    self.assertEqual(target.read_bytes(), b"model test fixture")
                (base / "mounts/target" / document["models"][0]["source_filename"]).unlink()
                with self.assertRaises(FileNotFoundError):
                    link(document, base / "missing")

    def test_comfyui_state_directories_exist_before_unprivileged_launch(self) -> None:
        namespace = runpy.run_path(str(ROOT / self.folder / "source/comfyui_job.py"))
        main = namespace["main"]
        globals_ = main.__globals__
        process = SimpleNamespace(terminate=lambda: None, wait=lambda timeout: 0)

        def launch(command, **kwargs):
            if self.folder.endswith("-offline"):
                for name in ("HF_HOME", "PIP_CACHE_DIR", "CCACHE_DIR"):
                    self.assertTrue(Path(kwargs["env"][name]).is_dir())
                    self.assertNotIn("/workspace", kwargs["env"][name])
                    self.assertNotIn("/root", kwargs["env"][name])
                for option in ("--disable-all-custom-nodes", "--disable-api-nodes", "--use-pytorch-cross-attention", "--disable-pinned-memory"):
                    self.assertIn(option, command)
                database = command[command.index("--database-url") + 1]
                self.assertTrue(database.startswith("sqlite:////"))
                database_path = Path(database.removeprefix("sqlite:///"))
                self.assertTrue(database_path.parent.is_dir())
                self.assertNotIn("/opt", str(database_path))
            for name in ("HOME", "XDG_CACHE_HOME", "TRITON_CACHE_DIR", "TORCH_HOME", "TORCH_EXTENSIONS_DIR", "TORCHINDUCTOR_CACHE_DIR", "CUDA_CACHE_PATH"):
                directory = Path(kwargs["env"][name])
                self.assertTrue(directory.is_dir())
                self.assertNotIn("/outputs", str(directory))
            for option in ("--user-directory", "--temp-directory"):
                directory = Path(command[command.index(option) + 1])
                self.assertTrue(directory.is_dir())
                probe = directory / "write-probe"
                probe.write_text("writable")
                self.assertNotIn("/opt/comfyui", str(directory))
            return process

        replacements = {
            "parse_args": lambda: SimpleNamespace(workflow="unused", workflow_sha256="unused", seed=0, output_dir="/outputs", output_mime="image/png"),
            "load_workflow": lambda *args: {"prompt": {}},
            "load_input_manifest": lambda: None,
            "input_names": lambda *args: [],
            "workflow_replacements": lambda *args: {},
            "link_models": lambda *args: None,
            "wait_for_server": lambda *args: None,
            "request_json": lambda *args, **kwargs: {"prompt_id": "test"},
            "wait_for_prompt": lambda *args: None,
            "copy_output": lambda *args: Path("/outputs/result.png"),
        }
        with patch.dict(globals_, replacements), patch("subprocess.Popen", side_effect=launch):
            self.assertEqual(main(), 0)


class OfflineFluxAdapterTests(FluxAdapterTests):
    folder = "flux2-klein-nvfp4-offline"


class GlmAdapterTests(unittest.TestCase):
    def argv(self, context="65536", sequences="2"):
        recipe = json.loads((ROOT / "glm53-mia-exl3-dual/recipe.json").read_text())
        arguments = ["vllm", "serve", "/models/target"]
        parameters = {"max_model_len": context, "max_num_seqs": sequences}
        for argument in recipe["runtime"]["arguments"]:
            value = parameters[argument["parameter"]] if "parameter" in argument else argument["value"]
            arguments.append("--" + argument["name"])
            if value is not True:
                arguments.append(str(value))
        return arguments + ["--nnodes", "2", "--node-rank", "0", "--distributed-executor-backend", "mp"]

    def run_wrapper(self, context="65536", sequences="2"):
        environment = {
            "VONK_LOCAL_ADDR": "198.19.240.11", "VONK_MASTER_ADDR": "198.19.240.11", "VONK_MASTER_PORT": "25000",
            "NCCL_SOCKET_IFNAME": "fabric0", "NCCL_IB_HCA": "roce0", "NCCL_IB_GID_INDEX": "3", "TP_SOCKET_IFNAME": "fabric0", "GLOO_SOCKET_IFNAME": "fabric0",
        }
        with patch.object(sys, "argv", self.argv(context, sequences)), patch.dict(os.environ, environment), patch.object(Path, "is_file", return_value=True), patch("os.access", return_value=True), patch("os.execv") as execute:
            runpy.run_path(str(ROOT / "glm53-mia-exl3-dual/source/vllm-wrapper.py"))
        return execute.call_args

    def test_initial_bounded_profile_retains_exact_quantization_and_rendezvous(self):
        call = self.run_wrapper()
        arguments = call.args[1]
        self.assertEqual(arguments[arguments.index("--max-model-len") + 1], "65536")
        self.assertEqual(arguments[arguments.index("--gpu-memory-utilization") + 1], "0.80")
        self.assertEqual(arguments[arguments.index("--quantization") + 1], "exl3")
        self.assertEqual(arguments[arguments.index("--master-addr") + 1], "198.19.240.11")

    def test_invalid_context_cannot_reach_vllm(self):
        for context in ("4095", "1000001", "65536.0", "-1", "foo"):
            with self.subTest(context=context), self.assertRaises(SystemExit):
                self.run_wrapper(context=context)

    def test_invalid_sequence_count_cannot_reach_vllm(self):
        for count in ("0", "5", "2.0"):
            with self.subTest(count=count), self.assertRaises(SystemExit):
                self.run_wrapper(sequences=count)


class QwenAdapterTests(unittest.TestCase):
    def wrapper(self):
        return runpy.run_path(str(ROOT / "qwen38-next-mia-dual/source/vllm-wrapper.py"))

    def environment(self):
        return {"VONK_LOCAL_ADDR": "198.19.240.11", "VONK_MASTER_ADDR": "198.19.240.11", "VONK_MASTER_PORT": "25000", "NCCL_SOCKET_IFNAME": "=enp1s0f0np0", "NCCL_IB_HCA": "=rocep1s0f0:1", "NCCL_IB_GID_INDEX": "3", "TP_SOCKET_IFNAME": "enp1s0f0np0", "GLOO_SOCKET_IFNAME": "enp1s0f0np0"}

    def argv(self, rank=0):
        recipe = json.loads((ROOT / "qwen38-next-mia-dual/recipe.json").read_text())
        arguments = ["serve", "/models"]
        parameters = {p["name"]: p["default"] for p in recipe["parameters"]}
        for argument in recipe["runtime"]["arguments"]:
            val = parameters[argument["parameter"]] if "parameter" in argument else argument["value"]
            arguments.append("--" + argument["name"])
            if val is not True:
                arguments.append(str(val))
        # These are compiler-owned, as verified separately by prepare.py.
        arguments += ["--tensor-parallel-size", "2", "--pipeline-parallel-size", "1", "--distributed-executor-backend", "mp", "--nnodes", "2", "--node-rank", str(rank)]
        return arguments + (["--headless"] if rank else [])

    def test_rank_profiles_and_reserved_arguments(self):
        validate = self.wrapper()["validate"]
        for rank in (0, 1):
            self.assertEqual(validate(self.argv(rank), self.environment())[2], "25000")
        for extra in (["--hf-overrides", "{}"], ["--node-rank", "1"], ["--headless"]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate(self.argv() + extra, self.environment())
        for option, bad in (("--kv-cache-dtype", "fp8"), ("--max-model-len", "1000000"), ("--gpu-memory-utilization", "0.835")):
            arguments = self.argv()
            arguments[arguments.index(option)+1] = bad
            with self.subTest(option=option), self.assertRaises(ValueError):
                validate(arguments, self.environment())

    def test_repeated_launches_patch_both_configs_without_modifying_the_model(self):
        namespace = self.wrapper()
        main = namespace["main"]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            model, outputs, source = (base / name for name in ("models", "outputs", "source"))
            model.mkdir(); outputs.mkdir(); (source / "upstream/files").mkdir(parents=True)
            quant = {"quantized_layers": {"mtp.layers.0.mlp.experts": {"quant_algo": "FP8_BLOCK_SCALES", "group_size": 128}}, "config_groups": {"group_1": {"targets": ["mtp.layers.0.mlp.experts"]}}}
            (model / "config.json").write_text(json.dumps({"text_config": {"num_hidden_layers": 48}, "quantization_config": quant}))
            (model / "hf_quant_config.json").write_text(json.dumps({"quantization": quant}))
            (model / "shard.safetensors").write_bytes(b"fixture weights")
            before = {p.name: p.read_bytes() for p in model.iterdir()}
            expected = {name: hashlib.sha256(before[name]).hexdigest() for name in ("config.json", "hf_quant_config.json")}
            (source / "checkpoint-config-sha256.json").write_text(json.dumps(expected))
            shutil.copyfile(ROOT / "qwen38-next-mia-dual/source/upstream/files/patch_checkpoint_config.py", source / "upstream/files/patch_checkpoint_config.py")
            executable = base / "vllm"; executable.write_text("fixture")
            original_is_file = Path.is_file
            def is_file(path):
                return str(path) == "/usr/local/bin/vllm" or original_is_file(path)
            with patch.dict(main.__globals__, {"MODEL": model, "OUTPUTS": outputs, "SOURCE": source}), patch.dict(os.environ, self.environment()), patch.object(Path, "is_file", is_file), patch("os.execv") as execute:
                for _ in range(2):
                    with patch.object(sys, "argv", ["wrapper", *self.argv()]):
                        main()
                views = [Path(call.args[1][2]) for call in execute.call_args_list]
            self.assertEqual(len(set(views)), 2)
            for view in views:
                config = json.loads((view / "config.json").read_text())
                sidecar = json.loads((view / "hf_quant_config.json").read_text())
                self.assertIn("mtp.layers.48.mlp.experts", config["quantization_config"]["quantized_layers"])
                self.assertIn("mtp.layers.48.mlp.experts", sidecar["quantization"]["quantized_layers"])
                self.assertEqual(config["text_config"]["ple_embedding_dtype"], "float8_e4m3fn")
                self.assertTrue((view / "shard.safetensors").is_symlink())
            self.assertEqual({p.name: p.read_bytes() for p in model.iterdir()}, before)
            (model / "config.json").write_text("{}")
            with self.assertRaises(ValueError):
                namespace["checkpoint_view"](model, base / "corrupt-view", source)


if __name__ == "__main__":
    unittest.main()
