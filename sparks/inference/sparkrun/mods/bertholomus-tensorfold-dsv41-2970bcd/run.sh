#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
install -m 0755 serve.py /usr/local/bin/dsv41-tf-entrypoint
install -d /opt/dsv41-tensorfold
install -m 0644 chat_template.jinja engram.index.json upstream.lock.json /opt/dsv41-tensorfold/
