"""Exercise the supported DeepSeek build entrypoint with fake Docker and Git CLIs, without network or a daemon."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build-deepseek-image.sh"
MOD = ROOT / "mods/bertholomus-tensorfold-dsv41-2970bcd-mia-fixes"
LOCK = json.loads((MOD / "upstream.lock.json").read_text())
DRIFT_ID = "sha256:" + "f" * 64

DOCKER = r'''
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
state_path = Path(os.environ["MOCK_STATE"])
state = json.loads(state_path.read_text())
with open(os.environ["MOCK_CALLS"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
mode = os.environ.get("MOCK_FAILURE", "")
if args[0] == "build":
    assert args[1] == "-t" and len(args) == 4, args
    assert args[2].startswith("home-lab-deepseek-candidate:"), args
    assert args[2] != os.environ["QUALIFIED_TAG"], args
    context = Path(args[3])
    assert (context / "tf-zig").is_dir() and (context / "tf-py").is_dir()
    assert (context / "Dockerfile").read_bytes() == Path(os.environ["DOCKERFILE"]).read_bytes()
    if mode == "build":
        sys.exit(10)
    state[args[2]] = os.environ["MOCK_IMAGE_ID"]
elif args[:4] == ["image", "inspect", "--format", "{{.Id}}"]:
    if mode == "inspect":
        sys.exit(11)
    print(state[args[4]])
    if mode == "retag-after-inspect":
        state[args[4]] = os.environ["DRIFT_ID"]
elif args[:2] == ["image", "tag"]:
    assert args[2] == os.environ["EXPECTED_ID"], args
    assert args[3] == os.environ["QUALIFIED_TAG"], args
    if mode == "tag":
        sys.exit(12)
    state[args[3]] = args[2]
elif args[:2] == ["image", "rm"]:
    assert args[2].startswith("home-lab-deepseek-candidate:"), args
    assert len(args) == 3 and args[2] != os.environ["QUALIFIED_TAG"], args
    if mode == "cleanup":
        sys.exit(13)
    state.pop(args[2], None)
else:
    raise AssertionError(args)
state_path.write_text(json.dumps(state))
'''

GIT = r'''
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["MOCK_GIT_CALLS"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
if os.environ.get("MOCK_FAILURE") == "fetch":
    sys.exit(14)
if args[0] == "clone":
    Path(args[-1]).mkdir()
elif args[0] == "-C" and args[2] in ("checkout", "apply"):
    assert Path(args[1]).is_dir(), args
else:
    raise AssertionError(args)
'''


class DeepSeekImageBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        # Include a space to exercise quoting in paths passed to both mock CLIs.
        self.bin = self.work / "mock bin"
        self.bin.mkdir()
        for name, source in (("docker", DOCKER), ("git", GIT)):
            executable = self.bin / name
            executable.write_text(f"#!{sys.executable}\n" + source)
            executable.chmod(0o755)
        self.state = self.work / "state.json"
        self.calls = self.work / "docker.jsonl"
        self.git_calls = self.work / "git.jsonl"
        self.state.write_text(json.dumps({LOCK["image"]: LOCK["image_id"]}))
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "MOCK_STATE": str(self.state), "MOCK_CALLS": str(self.calls),
            "MOCK_GIT_CALLS": str(self.git_calls), "MOCK_FAILURE": "",
            "MOCK_IMAGE_ID": LOCK["image_id"], "EXPECTED_ID": LOCK["image_id"],
            "QUALIFIED_TAG": LOCK["image"], "DRIFT_ID": DRIFT_ID,
            "DOCKERFILE": str(MOD / "build/Dockerfile"),
        }

    def run_build(self, *args):
        return subprocess.run(["bash", str(BUILD), *map(str, args)], cwd=self.work,
                              env=self.env, text=True, capture_output=True, timeout=30)

    def docker_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def assert_qualified_untouched(self):
        self.assertEqual(json.loads(self.state.read_text())[LOCK["image"]], LOCK["image_id"])
        self.assertFalse(any(call[:2] == ["image", "tag"] for call in self.docker_calls()))

    def test_matching_candidate_is_inspected_before_tagging_by_immutable_id(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.docker_calls()
        candidate = calls[0][2]
        self.assertNotEqual(candidate, LOCK["image"])
        self.assertEqual(calls, [calls[0], ["image", "inspect", "--format", "{{.Id}}", candidate],
                                 ["image", "tag", LOCK["image_id"], LOCK["image"]], ["image", "rm", candidate]])
        self.assertIn(LOCK["image_id"], result.stdout)
        self.assertEqual(json.loads(self.state.read_text()), {LOCK["image"]: LOCK["image_id"]})
        self.assertFalse(Path(calls[0][-1]).exists(), "build context must be removed")
        git = [json.loads(line) for line in self.git_calls.read_text().splitlines()]
        self.assertEqual([call[-1] for call in git if call[0] == "-C" and call[2] == "checkout"],
                         [LOCK["engine_revision"], LOCK["inputs_revision"]])
        self.assertEqual([call[-2] for call in git if call[0] == "clone"],
                         [f"https://github.com/{LOCK['engine_repository']}.git"] * 2)
        self.assertEqual([Path(call[-1]).name for call in git if call[0] == "-C" and call[2] == "apply"],
                         [Path(patch).name for patch in sorted(LOCK["patches"])])

    def test_drift_empty_or_invalid_inspection_leaves_qualified_tag_untouched(self):
        for actual in (DRIFT_ID, "", "not-an-image-id"):
            with self.subTest(actual=actual):
                self.env["MOCK_IMAGE_ID"] = actual
                result = self.run_build()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"expected: {LOCK['image_id']}", result.stderr)
                self.assertIn(f"actual:   {actual}", result.stderr)
                self.assertIn("new versioned mod and profile", result.stderr)
                self.assertIn("was not changed", result.stderr)
                self.assert_qualified_untouched()
                self.assertEqual(json.loads(self.state.read_text()), {LOCK["image"]: LOCK["image_id"]})

    def test_build_inspect_and_fetch_failures_do_not_publish(self):
        for failure, diagnostic in (("build", "Candidate build failed"), ("inspect", "Cannot inspect candidate"),
                                    ("fetch", "")):
            with self.subTest(failure=failure):
                self.env["MOCK_FAILURE"] = failure
                result = self.run_build()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(diagnostic, result.stderr)
                self.assert_qualified_untouched()
                self.assertEqual(json.loads(self.state.read_text()), {LOCK["image"]: LOCK["image_id"]})

    def test_candidate_retag_after_inspection_cannot_publish_drift(self):
        self.env["MOCK_FAILURE"] = "retag-after-inspect"
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.state.read_text()), {LOCK["image"]: LOCK["image_id"]})

    def test_publish_failure_is_reported_and_cleans_only_candidate(self):
        self.env["MOCK_FAILURE"] = "tag"
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("publishing qualified tag", result.stderr)
        self.assertEqual(json.loads(self.state.read_text()), {LOCK["image"]: LOCK["image_id"]})
        self.assertEqual(self.docker_calls()[-1][:2], ["image", "rm"])

    def test_cleanup_failure_does_not_hide_drift(self):
        self.env.update(MOCK_FAILURE="cleanup", MOCK_IMAGE_ID=DRIFT_ID)
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DeepSeek rebuild refused", result.stderr)
        self.assertIn("Could not remove temporary tag", result.stderr)
        self.assert_qualified_untouched()

    def test_changed_frozen_input_fails_before_docker(self):
        mod = self.work / "changed mod"
        shutil.copytree(MOD, mod, ignore=shutil.ignore_patterns("__pycache__"))
        (mod / "build/Dockerfile").write_text("changed\n")
        result = self.run_build(mod)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing or changed frozen mod file", result.stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_explicit_mod_directory_uses_its_frozen_inputs(self):
        mod = self.work / "versioned mod"
        shutil.copytree(MOD, mod, ignore=shutil.ignore_patterns("__pycache__"))
        result = self.run_build(mod)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["image", "tag", LOCK["image_id"], LOCK["image"]], self.docker_calls())

    def test_invalid_expected_id_fails_before_docker(self):
        mod = self.work / "invalid lock"
        shutil.copytree(MOD, mod, ignore=shutil.ignore_patterns("__pycache__"))
        lock = {**LOCK, "image_id": ""}
        (mod / "upstream.lock.json").write_text(json.dumps(lock))
        result = self.run_build(mod)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid qualified image ID", result.stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_no_record_bypass_or_extra_build_options(self):
        for args in (("--record",), (str(MOD), "--record")):
            with self.subTest(args=args):
                result = self.run_build(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("no --record or image-ID override", result.stderr)
                self.assertEqual(self.docker_calls(), [])


if __name__ == "__main__":
    unittest.main()
