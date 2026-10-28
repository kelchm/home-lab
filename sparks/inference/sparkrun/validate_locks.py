#!/usr/bin/env python3
"""Validate reviewed SparkRun manifests and recipes, without changing frozen mods.

This checks declared pins and agreement, not source authenticity or reproducibility.
Unknown lock fields/shapes require extending this validator as part of their review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parent
COMMON = "engine_repository engine_revision image image_id mod_files"
KIT = "kit_repository kit_revision"
QWEN = "engine_branch weights server_patches build"
SHAPES = {
    "glm": f"{COMMON} {KIT} engine_version upstream_patch_count upstream_patch_hash launcher_env installed_files",
    "qwen-tp1": f"{COMMON} {KIT} {QWEN} kit_patch_count kit_patches_sha256",
    "qwen-tp2": f"{COMMON} {KIT} {QWEN} upstream_patch_count upstream_patch_hash site_patches binary_sha256",
    "deepseek": f"{COMMON} engine_branch inputs_branch inputs_revision patches kit engram",
    "readiness": "mod_files",
}
# Repository names, image names and package specs are the literal forms consumed
# by the current builders, not arbitrary URLs, shell expressions or pip options.
REPOSITORY = r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*"
IMAGE_NAME = r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
TAG = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
# Required keys in the supported GLM kit's recorded launcher contract. Values
# remain reviewed data; removing a key must not silently weaken the comparison.
GLM_LAUNCHER = """
TENSORFOLD_GLM_REQUEST_IMAGE_TOKENS TENSORFOLD_MEMORY_RESERVE_GIB TENSORFOLD_NO_UPDATE_CHECK
TENSORFOLD_NUCLEUS_UNION TENSORFOLD_SSE_KEEPALIVE_S TENSORFOLD_VISION_WORKSPACE_MIB
TF_GLM_CACHE_ENTRIES TF_GLM_CACHE_GIB TF_GLM_CACHE_SHARE_PCT TF_GLM_CLEAR_THINKING TF_GLM_COMM
TF_GLM_COPY_DRAFTS TF_GLM_COPY_MAX TF_GLM_COPY_REPLY_MATCH TF_GLM_DENSE TF_GLM_DFLASH_POLICY
TF_GLM_DISPLAY_KV_BACKEND TF_GLM_DISPLAY_KV_MIB TF_GLM_EFFORT_TAIL TF_GLM_EXL3_DEC_ORDER TF_GLM_EXL3_LOADS
TF_GLM_FILL_BUDGET_MS TF_GLM_FILL_DRAFTS TF_GLM_FILL_ROWS TF_GLM_HC_SPLIT TF_GLM_KDA_CHUNKED
TF_GLM_KEEP_PER_CHAT TF_GLM_KEPT_BYTES_GIB TF_GLM_KV TF_GLM_L2PF TF_GLM_MAX_QUEUED TF_GLM_MTP
TF_GLM_MULTI_LONE TF_GLM_MULTI_PREFILL TF_GLM_MULTI_WINDOW TF_GLM_PREFILL_OVERLAP TF_GLM_PREFIX_WAIT
TF_GLM_SHARED_PREFIX TF_GLM_SPILL_QUOTA TF_GLM_SPILL_TURNS TF_GLM_STREAM_SMOOTH TF_GLM_STREAM_SMOOTH_MS
TF_GLM_WIDE_GRAPHS TF_ROCE_MAX_KB TF_ROCE_WAIT_S
"""
GLM_INSTALLED = "families/glm5_next/cuda/kept_reasoning.py cuda/health.py cuda/http.py"


class InvalidManifest(ValueError):
    pass


def require(ok, where, message):
    if not ok:
        raise InvalidManifest(f"{where}: {message}")


def fields(value, names, where):
    require(isinstance(value, dict), where, "expected an object")
    expected = set(names.split())
    require(set(value) == expected, where,
            f"unsupported fields/shape; missing {sorted(expected - set(value))}, unexpected {sorted(set(value) - expected)}")


def text(value, where):
    require(isinstance(value, str) and bool(value.strip()), where, "expected nonempty text")


def pattern(value, regex, where, description):
    require(isinstance(value, str) and re.fullmatch(regex, value) is not None, where, description)


def hash_value(value, size, where):
    pattern(value, rf"[0-9a-f]{{{size}}}", where, f"expected a full {size}-digit lowercase hex pin")
    require(value != "0" * size, where, "zero is not a dependency pin")


def digest(value, where):
    pattern(value, r"sha256:[0-9a-f]{64}", where, "expected sha256:<64 lowercase hex digits>")
    hash_value(value[7:], 64, where)


def positive(value, where):
    require(type(value) is int and value > 0, where, "expected a positive integer")


def path_value(value, where):
    text(value, where)
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == value and value != ".",
            where, "expected a normalized relative file path")


def repository(value, where):
    pattern(value, REPOSITORY, where, "expected owner/repository")


def source(value, where, extra=""):
    fields(value, f"repository revision {extra}", where)
    repository(value["repository"], f"{where}.repository")
    hash_value(value["revision"], 40, f"{where}.revision")


def image(value, where):
    pattern(value, rf"{IMAGE_NAME}(?::{TAG})?@sha256:[0-9a-f]{{64}}", where,
            "expected an immutable named image reference with @sha256 digest")
    digest(value.rsplit("@", 1)[1], where)


def validate_dockerfile(mod, lock):
    """Only the current literal/BASE_IMAGE FROM forms; not a Dockerfile interpreter."""
    dockerfile = mod / "build/Dockerfile"
    if not dockerfile.exists():
        return
    bases = []
    for line in dockerfile.read_text().splitlines():
        tokens = line.split()
        if tokens and tokens[0].upper() == "FROM":
            require(len(tokens) == 2 or (len(tokens) == 4 and tokens[2].upper() == "AS"), "build/Dockerfile", "unsupported FROM shape")
            base = tokens[1]
            if base == "scratch":
                continue
            if base == "${BASE_IMAGE}":
                require("build" in lock, "build/Dockerfile", "BASE_IMAGE requires a declared build.base_image")
                base = lock["build"]["base_image"]
            image(base, "build/Dockerfile FROM")
            bases.append(base)
        elif tokens and tokens[0].upper() == "ARG" and len(tokens) > 1:
            if tokens[1].split("=", 1)[0] == "BASE_IMAGE":
                equal(tokens[1:], ["BASE_IMAGE"], "build/Dockerfile BASE_IMAGE (lock supplied, no override)")
            elif tokens[1].startswith("ZIG_SHA256="):
                hash_value(tokens[1].split("=", 1)[1], 64, "build/Dockerfile ZIG_SHA256")
    require(bool(bases), "build/Dockerfile", "missing immutable FROM base")
    if "build" in lock:
        require(all(base == lock["build"]["base_image"] for base in bases), "build/Dockerfile", "FROM base disagrees with build.base_image")


def shape(lock, mod):
    require(isinstance(lock, dict), "lock", "expected an object")
    if mod.name == "tensorfold-readiness":
        kind = "readiness"
    elif set(lock) & {"launcher_env", "installed_files", "engine_version"}:
        kind = "glm"
    elif set(lock) & {"kit", "engram", "inputs_revision", "inputs_branch"}:
        kind = "deepseek"
    elif set(lock) & {"binary_sha256", "site_patches"}:
        kind = "qwen-tp2"
    elif set(lock) & {"weights", "kit_patch_count", "kit_patches_sha256"}:
        kind = "qwen-tp1"
    else:
        raise InvalidManifest("unsupported lock shape; only the shared tensorfold-readiness mod is dependency-free")
    fields(lock, SHAPES[kind], kind)
    return kind


def validate_lock(lock, mod):
    kind = shape(lock, mod)
    files = lock["mod_files"]
    require(isinstance(files, dict) and bool(files), "mod_files", "expected file checksums")
    for name, pin in files.items():
        path_value(name, "mod_files")
        hash_value(pin, 64, f"mod_files.{name}")
    if kind == "readiness":
        return kind
    repository(lock["engine_repository"], "engine_repository")
    for key in ("engine_revision", "kit_revision", "inputs_revision"):
        if key in lock:
            hash_value(lock[key], 40, key)
    for key in ("engine_branch", "inputs_branch"):
        if key in lock:
            text(lock[key], key)  # Fetch hints; the full revisions above are the pins.
    if "kit_repository" in lock:
        repository(lock["kit_repository"], "kit_repository")
    digest(lock["image_id"], "image_id")  # Config ID, not the registry manifest digest.
    if kind in ("glm", "qwen-tp2"):
        image(lock["image"], "image")
        positive(lock["upstream_patch_count"], "upstream_patch_count")
        # These kits publish a 12-digit patch-set identifier, not a full sha256.
        hash_value(lock["upstream_patch_hash"], 12, "upstream_patch_hash")
    else:
        pattern(lock["image"], rf"{IMAGE_NAME}:{TAG}", "image", "expected a named local image tag paired with image_id")
    if kind == "glm":
        pattern(lock["engine_version"], r"[0-9]+\.[0-9]+\.[0-9]+", "engine_version", "expected a release version")
        env = lock["launcher_env"]
        fields(env, GLM_LAUNCHER, "launcher_env")
        for name, value in env.items():
            pattern(name, r"[A-Z_][A-Z0-9_]*", "launcher_env", "invalid environment variable name")
            require(isinstance(value, str), f"launcher_env.{name}", "expected a string (empty is allowed)")
        installed = lock["installed_files"]
        fields(installed, GLM_INSTALLED, "installed_files")
        for name, pins in installed.items():
            path_value(name, "installed_files")
            fields(pins, "upstream_sha256 site_sha256", f"installed_files.{name}")
            for key, pin in pins.items():
                hash_value(pin, 64, f"installed_files.{name}.{key}")
    elif kind.startswith("qwen"):
        source(lock["weights"], "weights")
        patches = lock["server_patches"]
        require(isinstance(patches, list) and bool(patches), "server_patches", "expected pinned server patches")
        for i, patch in enumerate(patches):
            where = f"server_patches[{i}]"
            fields(patch, "file repository base commit paths", where)
            path_value(patch["file"], where + ".file")
            require(patch["file"] in files, where, "patch is absent from mod_files")
            repository(patch["repository"], where + ".repository")
            for key in ("base", "commit"):
                hash_value(patch[key], 40, where + "." + key)
            require(isinstance(patch["paths"], list) and bool(patch["paths"]), where + ".paths", "expected source paths")
            for name in patch["paths"]:
                path_value(name, where + ".paths")
        build = lock["build"]
        common = "base_image zig_version zig_sha256 zig_cpu"
        extra = "kernel_source_mtime pip_packages" if kind == "qwen-tp1" else "kit_patches_sha256 fatbin_layer published_binary_sha256"
        fields(build, f"{common} {extra}", "build")
        image(build["base_image"], "build.base_image")
        hash_value(build["zig_sha256"], 64, "build.zig_sha256")
        pattern(build["zig_version"], r"[0-9]+\.[0-9]+\.[0-9]+", "build.zig_version", "expected a release version")
        text(build["zig_cpu"], "build.zig_cpu")
        if kind == "qwen-tp1":
            positive(lock["kit_patch_count"], "kit_patch_count")
            hash_value(lock["kit_patches_sha256"], 64, "kit_patches_sha256")
            pattern(build["kernel_source_mtime"], r"[1-9][0-9]*", "build.kernel_source_mtime", "expected a positive timestamp string")
            packages = build["pip_packages"]
            require(isinstance(packages, list) and bool(packages), "build.pip_packages", "expected exact package pins")
            for package in packages:
                pattern(package, r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?==[0-9]+(?:\.[0-9]+)*"
                        r"(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?(?:\+[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*)?",
                        "build.pip_packages", "expected supported name==exact-version, without wildcards, ranges, markers or pip options")
            # build.sh computes the suffix from kit + site patch bytes. The kit
            # bytes aren't local, so only its format and kit prefix are checkable here.
            tag = lock["image"].rsplit(":", 1)[1]
            pattern(tag, rf"{lock['kit_revision'][:7]}-[0-9a-f]{{12}}", "image", "local tag must agree with kit revision and patch identifier")
            hash_value(tag.rsplit("-", 1)[1], 12, "image patch identifier")
        else:
            for key in ("kit_patches_sha256", "published_binary_sha256"):
                hash_value(build[key], 64, f"build.{key}")
            digest(build["fatbin_layer"], "build.fatbin_layer")
            hash_value(lock["binary_sha256"], 64, "binary_sha256")
            require(isinstance(lock["site_patches"], list) and bool(lock["site_patches"]), "site_patches", "expected site patch records")
            for i, patch in enumerate(lock["site_patches"]):
                where = f"site_patches[{i}]"
                fields(patch, "file fixes source author license sha256", where)
                path_value(patch["file"], where + ".file")
                for key in ("fixes", "source", "author", "license"):
                    text(patch[key], where + "." + key)  # Narrative provenance, not structured commit pins.
                hash_value(patch["sha256"], 64, where + ".sha256")
                require(files.get(patch["file"]) == patch["sha256"], where, "patch hash disagrees with mod_files")
    else:
        source(lock["kit"], "kit", "bias_vl_sha256")
        hash_value(lock["kit"]["bias_vl_sha256"], 64, "kit.bias_vl_sha256")
        source(lock["engram"], "engram", "files")
        blobs = lock["engram"]["files"]
        index = read_json(mod / "engram.index.json")
        shards = set(index["weight_map"].values())
        require(isinstance(blobs, dict) and set(blobs) == {"config.json", *shards}, "engram.files", "must cover config.json and the embed-only index shards")
        for name, pin in blobs.items():
            path_value(name, "engram.files")
            # HF cache names: config.json is a Git blob (SHA-1), LFS shards are SHA-256.
            hash_value(pin, 40 if name == "config.json" else 64, f"engram.files.{name}")
        patches = lock["patches"]
        require(isinstance(patches, dict) and bool(patches), "patches", "expected patch provenance")
        require(set(patches) == {name for name in files if name.startswith("patches/")}, "patches", "must describe every frozen patch")
        for name, description in patches.items():
            text(description, f"patches.{name}")  # Includes local descriptions and abbreviated historical references.
        patch_hash = hashlib.sha256(b"".join((mod / name).read_bytes() for name in sorted(patches))).hexdigest()[:12]
        expected = f"{lock['engine_revision'][:7]}-{lock['inputs_revision'][:7]}-{patch_hash}"
        require(lock["image"].rsplit(":", 1)[1] == expected, "image", "local tag disagrees with engine/inputs revisions or frozen patch bytes")
    validate_dockerfile(mod, lock)
    return kind


def equal(actual, expected, where):
    require(actual == expected, where, f"disagrees with reviewed input: expected {expected!r}, got {actual!r}")


def validate_recipe(recipe, path, locks):
    require(isinstance(recipe, dict) and recipe.get("runtime") == "tensorfold" and recipe.get("recipe_version") == "2",
            "recipe", "unsupported recipe shape/runtime")
    mods = recipe.get("mods")
    require(isinstance(mods, list) and bool(mods), "mods", "expected local reviewed mods")
    owners = []
    for ref in mods:
        text(ref, "mods")
        mod = (path.parent / ref).resolve()
        require(mod in locks, "mods", f"no supported lock for {ref}")
        lock, kind = locks[mod]
        if kind != "readiness":
            owners.append((lock, kind))
    require(len(owners) == 1, "mods", "expected exactly one engine lock")
    require(any((path.parent / ref).name == "tensorfold-readiness" for ref in mods), "mods", "missing shared readiness mod")
    lock, kind = owners[0]
    metadata, defaults = recipe.get("metadata"), recipe.get("defaults")
    require(isinstance(metadata, dict) and isinstance(defaults, dict), "recipe", "metadata and defaults are required")
    equal(metadata.get("profile"), path.stem, "metadata.profile")
    expected_image = lock["image_id"] if kind in ("qwen-tp1", "deepseek") else lock["image"]
    equal(recipe.get("container"), expected_image, "container")
    for key in ("engine_revision", "kit_revision", "upstream_patch_count", "upstream_patch_hash"):
        if key in lock:
            equal(metadata.get(key), lock[key], f"metadata.{key}")
    if kind == "qwen-tp1":
        equal(metadata.get("upstream_patch_count"), lock["kit_patch_count"], "metadata.upstream_patch_count")
    if kind.startswith("qwen"):
        equal(metadata.get("server_patch_count"), len(lock["server_patches"]) + len(lock.get("site_patches", [])), "metadata.server_patch_count")
        equal({"repository": recipe.get("model"), "revision": recipe.get("model_revision")}, lock["weights"], "model/weights")
    if kind == "deepseek" or "image" in metadata:
        equal(metadata.get("image"), lock["image"], "metadata.image")
    repository(recipe.get("model"), "model")
    hash_value(recipe.get("model_revision"), 40, "model_revision")
    equal(metadata.get("target_model_revision"), recipe["model_revision"], "metadata.target_model_revision")
    expected_sources = {(recipe["model"], recipe["model_revision"])}
    if kind == "deepseek":
        expected_sources.add((lock["kit"]["repository"], lock["kit"]["revision"]))
    if kind == "glm" or "drafter" in defaults or "draft_model_revision" in metadata:
        repository(defaults.get("drafter"), "defaults.drafter")
        hash_value(defaults.get("drafter_revision"), 40, "defaults.drafter_revision")
        equal(metadata.get("draft_model_revision"), defaults["drafter_revision"], "metadata.draft_model_revision")
        expected_sources.add((defaults["drafter"], defaults["drafter_revision"]))
    distribution = recipe.get("distribution_config", {}).get("models", {}).get("entries")
    require(isinstance(distribution, list) and bool(distribution), "distribution_config.models.entries", "expected pinned sources")
    sources = set()
    for entry in distribution:
        fields(entry, "name revision target", "distribution entry")
        repository(entry["name"], "distribution entry.name")
        hash_value(entry["revision"], 40, "distribution entry.revision")
        sources.add((entry["name"], entry["revision"]))
    equal(sources, expected_sources, "distribution sources")
    env = recipe.get("env", {})
    require(isinstance(env, dict), "env", "expected environment variables")
    for name, value in lock.get("launcher_env", {}).items():
        equal(env.get(name), value, f"launcher_env.{name}")
    equal(defaults.get("entrypoint"), "/usr/local/bin/tensorfold-entrypoint", "defaults.entrypoint")
    positive(defaults.get("context"), "defaults.context")
    text(defaults.get("served_model_name"), "defaults.served_model_name")
    hooks = recipe.get("post_exec")
    require(isinstance(hooks, list) and bool(hooks), "post_exec", "expected the shared readiness hook")
    gates = [shlex.split(hook) for hook in hooks if isinstance(hook, str)]
    gates = [argv for argv in gates if argv[:2] == ["python3", "/opt/tensorfold-readiness/readiness.py"]]
    require(len(gates) == 1, "post_exec", "expected exactly one shared readiness hook")
    for flag, value in (("--context", str(defaults["context"])), ("--model", defaults["served_model_name"])):
        argv = gates[0]
        require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv), "post_exec", f"expected one {flag} value")
        equal(argv[argv.index(flag) + 1], value, f"post_exec {flag}")


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, str(path), f"duplicate JSON field {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def validate_repository(root=ROOT):
    root = root.resolve()
    require((root / "mods").is_dir(), str(root), "missing mods directory")
    locks, images, used = {}, {}, set()
    manifests = sorted((root / "mods").rglob("upstream.lock.json"))
    require(bool(manifests), str(root), "no supported locks found")
    for mod in (root / "mods").iterdir():
        if mod.is_dir() and mod.name != "__pycache__":
            require((mod / "upstream.lock.json").is_file(), str(mod), "missing required upstream.lock.json")
    for manifest in manifests:
        try:
            require(manifest.parent.parent == root / "mods", str(manifest), "unsupported nested lock location")
            lock = read_json(manifest)
            kind = validate_lock(lock, manifest.parent)
            locks[manifest.parent.resolve()] = (lock, kind)
            if kind != "readiness":
                reference, config_id = lock["image"], lock["image_id"]
                if reference in images:
                    equal(config_id, images[reference], "image/image_id relationship (same reference, different config IDs)")
                images[reference] = config_id  # Same ID under several aliases is valid.
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise InvalidManifest(f"{manifest.relative_to(root)}: {exc}") from exc
    recipes = sorted(path for path in root.glob("*/*.yaml") if path.parent.name != "benchmarks")
    require(bool(recipes), str(root), "no owning recipes found")
    for path in recipes:
        try:
            recipe = yaml.safe_load(path.read_text())
            validate_recipe(recipe, path, locks)
            used.update((path.parent / ref).resolve() for ref in recipe["mods"])
        except (ValueError, KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
            raise InvalidManifest(f"{path.relative_to(root)}: {exc}") from exc
    require(set(locks) <= used, "mods", f"locks without owning recipes: {sorted(str(path.relative_to(root)) for path in set(locks) - used)}")
    return len(locks), len(recipes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="SparkRun source root (also useful for temporary fixtures)")
    args = parser.parse_args()
    try:
        locks, recipes = validate_repository(args.root)
    except (InvalidManifest, OSError) as exc:
        parser.exit(1, f"Invalid SparkRun dependency inputs: {exc}\n")
    print(f"Verified dependency pins and agreement for {locks} locks and {recipes} recipes")


if __name__ == "__main__":
    main()
