#!/usr/bin/env python3
"""Validate evaluation recipes against the deployed recipe-v1 contract and bundle sources."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import runpy
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--published-source", type=Path, required=True)
    parser.add_argument("--library-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recipe", help="Prepare only this recipe folder")
    parser.add_argument("--refresh", action="store_true", help="Update source identities after an intentional source edit")
    args = parser.parse_args()
    platform = args.published_source.resolve()
    for directory in (platform / "control/src", platform / "agent_protocol/src", platform / "src"):
        sys.path.insert(0, str(directory))
    from vonk_control.catalog_contract import catalog_content_sha256, validate_catalog_document
    from vonk_control.harnesses.comfyui import ComfyUiHarnessCompiler
    from vonk_control.harnesses.vllm import VllmHarnessCompiler
    from vonk_control.recipe_contract import recipe_content_sha256, recipe_references, validate_recipe
    from vonk_control.source_bundles import generate_source_bundle, inspect_source_bundle
    from vonk_control.source_policy import enforce_build_source_policy

    args.output.mkdir(parents=True, exist_ok=True)
    library = args.library_source.resolve()
    entities = {}
    for directory in ("models", "model-versions", "model-groups", "runtime-distributions", "patch-bundles", "execution-harnesses"):
        for path in (library / directory).glob("*.json"):
            entity = json.loads(path.read_text())
            identity = entity["identity"]
            entities[(entity["kind"], identity["publisher"], identity["slug"])] = entity

    reports = []
    for recipe_path in sorted(Path(__file__).parent.glob("*/recipe.json")):
        if args.recipe and recipe_path.parent.name != args.recipe:
            continue
        recipe = json.loads(recipe_path.read_text())
        for path in recipe_path.parent.glob("*.entity.json"):
            entity = json.loads(path.read_text())
            validate_catalog_document(entity)
            identity = entity["identity"]
            entities[(entity["kind"], identity["publisher"], identity["slug"])] = entity
        source = recipe_path.parent / "source"
        paths = sorted(source.rglob("*"))
        if any(path.is_symlink() or (not path.is_dir() and not path.is_file()) for path in paths):
            raise ValueError(f"Nonregular source entry in {source}")
        files = {path.relative_to(source).as_posix(): path.read_bytes() for path in paths if path.is_file()}
        if any("__pycache__" in name or name.endswith(".pyc") for name in files):
            raise ValueError("Python cache files must stay outside source bundles")
        bundle = generate_source_bundle(files)
        context = recipe["build"]["context"]
        local_patch_path = recipe_path.parent / "patch-bundle.json"
        local_patch = json.loads(local_patch_path.read_text()) if local_patch_path.exists() else None
        if args.refresh:
            context["sha256"] = bundle.sha256
            context["expected_bytes"] = len(bundle.archive)
            if local_patch is not None:
                local_patch["source_bundle"]["sha256"] = bundle.sha256
                local_patch["source_bundle"]["expected_bytes"] = len(bundle.archive)
                local_patch["sha256"] = bundle.sha256
                recipe["execution"]["patch_bundle"]["content_sha256"] = catalog_content_sha256(local_patch)
                local_patch_path.write_text(json.dumps(local_patch, indent=2) + "\n")
            recipe_path.write_text(json.dumps(recipe, indent=2) + "\n")
        if local_patch is not None:
            validate_catalog_document(local_patch)
            identity = local_patch["identity"]
            entities[("patch-bundle", identity["publisher"], identity["slug"])] = local_patch
            assert local_patch["source_bundle"]["sha256"] == bundle.sha256
        assert context["sha256"] == bundle.sha256, recipe_path
        assert context["expected_bytes"] == len(bundle.archive), recipe_path
        assert inspect_source_bundle(io.BytesIO(bundle.archive)).sha256 == bundle.sha256
        validate_recipe(recipe)
        enforce_build_source_policy(recipe, bundle)
        resolved = {}
        dependencies = []
        for reference in recipe_references(recipe):
            key = (reference.kind.value, reference.publisher, reference.slug)
            # Built-in harnesses are controller-owned; catalog sync already supplies them.
            if key not in entities and key[0] == "execution-harness":
                continue
            entity = entities[key]
            assert catalog_content_sha256(entity) == reference.content_sha256, key
            resolved[key[0]] = entity
            dependencies.append({"kind": key[0], "publisher": key[1], "slug": key[2], "content_sha256": reference.content_sha256})
        # Traverse nested catalog references too (model-version -> model -> group).
        checked = set()
        def verify_nested(value):
            if isinstance(value, dict):
                if set(value) == {"kind", "publisher", "slug", "content_sha256"}:
                    key = (value["kind"], value["publisher"], value["slug"])
                    if key[0] == "execution-harness" and key not in entities:
                        return
                    entity = entities[key]
                    assert catalog_content_sha256(entity) == value["content_sha256"], key
                    if key not in checked:
                        checked.add(key)
                        verify_nested(entity)
                else:
                    for child in value.values():
                        verify_nested(child)
            elif isinstance(value, list):
                for child in value:
                    verify_nested(child)
        verify_nested(recipe)
        distribution = resolved["runtime-distribution"]
        patch = resolved.get("patch-bundle")
        compiler = ComfyUiHarnessCompiler() if recipe["execution"]["harness"]["slug"] == "comfyui" else VllmHarnessCompiler()
        projections = []
        parameters = {parameter["name"]: parameter["default"] for parameter in recipe["parameters"]}
        for rank, role in enumerate(recipe["topology"]["roles"]):
            projection = compiler.compile(recipe, distribution, patch, parameters, recipe["topology"], role["name"], rank)
            if recipe_path.parent.name == "qwen38-next-mia-dual":
                validate_launch = runpy.run_path(str(source / "vllm-wrapper.py"))["validate"]
                profile = distribution["capabilities"]["distributed_vllm"]["launch"]["rank_profiles"][rank]
                environment = dict(profile["environment"], VONK_LOCAL_ADDR=f"198.19.240.{11 + rank}", VONK_MASTER_ADDR="198.19.240.11", VONK_MASTER_PORT="25000")
                validate_launch(list(projection.command)[1:], environment)
            projections.append({"rank": rank, "role": role["name"], "argv": list(projection.command)})
            exact_bytes = sum(artifact["installed_bytes"] for artifact in recipe["artifacts"] if role["name"] in artifact["roles"])
            assert role["resources"]["disk"]["artifact_bytes"] == exact_bytes
        archive_path = args.output / f"{recipe_path.parent.name}.tar"
        archive_path.write_bytes(bundle.archive)
        report = {
            "recipe": str(recipe_path.resolve()),
            "recipe_sha256": recipe_content_sha256(recipe),
            "source_manifest_sha256": bundle.sha256,
            "source_archive_sha256": hashlib.sha256(bundle.archive).hexdigest(),
            "source_archive_bytes": len(bundle.archive),
            "source_archive": str(archive_path.resolve()),
            "dependencies": dependencies,
            "projections": projections,
            "recipe_v1_valid": True,
            "controller_source_policy_passed": True,
        }
        reports.append(report)
    (args.output / "prepared.json").write_text(json.dumps(reports, indent=2) + "\n")
    print(json.dumps([{key: value for key, value in report.items() if key not in {"dependencies", "projections"}} for report in reports], indent=2))


if __name__ == "__main__":
    main()
