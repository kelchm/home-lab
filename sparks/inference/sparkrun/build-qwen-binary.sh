#!/usr/bin/env bash
# Run the historical binary builder in a disposable copy, then atomically publish its verified binary.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
if [[ "${1:-}" == --help ]]; then
  echo "Usage: $0 [MOD_DIRECTORY]"
  exit 0
fi
if (( $# > 1 )) || [[ "${1:-}" == -* ]]; then
  echo "Usage: $0 [MOD_DIRECTORY] (no --record or checksum override)" >&2
  exit 2
fi
mod=$(cd "${1:-$here/mods/mia-tensorfold-qwen38fn-v1.1-discovery}" && pwd)
python3 "$mod/prepare.py" --check
# Sibling staging keeps the final rename on the same filesystem. Frozen inputs are
# copied byte for byte; the historical builder can remove only its disposable out/.
work=$(mktemp -d "$(dirname "$mod")/.qwen-binary-candidate.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 - "$mod" "$work/mod" <<'PY'
from pathlib import Path
import shutil
import sys
source, target = map(Path, sys.argv[1:])
shutil.copytree(source, target, ignore=shutil.ignore_patterns("out", "__pycache__"))
PY
bash "$work/mod/build/build.sh"
python3 "$work/mod/prepare.py" --check --require-binary
# Recheck the destination before publishing; do not follow a linked out/ or binary.
python3 "$mod/prepare.py" --check
python3 - "$work/mod/out/tensorfold-native" "$mod/out" <<'PY'
import os
from pathlib import Path
import sys
binary, output = map(Path, sys.argv[1:])
output.mkdir(exist_ok=True)
os.replace(binary, output / "tensorfold-native")
PY
echo "Verified and published $mod/out/tensorfold-native"
