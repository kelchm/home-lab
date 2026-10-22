#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
python3 site_fixes.py
install -m 0755 serve.py /usr/local/bin/tensorfold-entrypoint
