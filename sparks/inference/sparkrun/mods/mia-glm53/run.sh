#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
# SparkRun copies this mod to every rank. Only checksum-locked runtime files
# are installed; a stale file from an older preparation cannot join the bundle.
python3 - <<'PATCHES'
import json
from pathlib import Path
import shutil
import subprocess
lock = json.loads(Path("upstream.lock.json").read_text())
root = Path("/opt/glm53")
root.mkdir(parents=True, exist_ok=True)
for name in lock["files"]:
    if name.startswith(("overlay/", "files/", "scripts/")):
        shutil.copyfile(Path("upstream") / name, root / Path(name).name)
    elif name.startswith("tests/"):
        target = root / Path(name).relative_to("tests")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path("upstream") / name, target)
for patch in lock["patches"]:
    subprocess.run(["python3", str(root / Path(patch).name)], check=True)
subprocess.run(["python3", str(root / "test_scheduler_decode_floor.py")], check=True)
PATCHES
python3 site_fixes.py
