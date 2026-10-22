#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
install -m 0755 serve.py /usr/local/bin/qwen38fn-tf-entrypoint
