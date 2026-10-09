#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
install -m 0755 serve.py /usr/local/bin/qwen38fn-mia-tf-entrypoint
mkdir -p /opt/qwen38fn-mia-tensorfold
install -m 0644 readiness.py /opt/qwen38fn-mia-tensorfold/readiness.py
