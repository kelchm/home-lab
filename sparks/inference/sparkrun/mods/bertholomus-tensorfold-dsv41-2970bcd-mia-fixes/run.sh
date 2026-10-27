#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
install -m 0755 serve.py /usr/local/bin/tensorfold-entrypoint
install -d /opt/tensorfold-mod
install -m 0644 chat_template.jinja engram.index.json upstream.lock.json /opt/tensorfold-mod/
