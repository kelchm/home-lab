#!/usr/bin/env python3
"""Version the ready-to-load Aurora module; no compiler or dependency install."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "kubernetes/apps/observability/grafana/app/aurora"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    digest = hashlib.sha256((PLUGIN / "module.js").read_bytes()).hexdigest()[:12]
    manifest = {
        "type": "app",
        "id": "homelab-aurora-app",
        "name": "Homelab Aurora theme",
        "preload": True,
        "includes": [],
        "info": {
            "author": {"name": "Homelab"},
            "description": "Optional Aurora adapter for stock Grafana; unqualified releases keep native styling.",
            "version": "0.1.0-" + digest,
            "updated": "2026-11-03",
            "logos": {"small": "", "large": ""},
            "screenshots": [],
            "keywords": ["theme"],
        },
        # Allow discovery after upgrades; the module's exact-version guard is stricter.
        "dependencies": {"grafanaDependency": ">=13.0.0", "plugins": []},
    }
    expected = json.dumps(manifest, indent=2) + "\n"
    target = PLUGIN / "plugin.json"
    if args.check:
        if not target.exists() or target.read_text() != expected:
            raise SystemExit("Aurora manifest is stale; run python3 scripts/grafana/render-aurora.py")
        print("Aurora module and cache version agree")
    else:
        target.write_text(expected)
        print("Updated " + str(target.relative_to(ROOT)))


if __name__ == "__main__":
    main()
