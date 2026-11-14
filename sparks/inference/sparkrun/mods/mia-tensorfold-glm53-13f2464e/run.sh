#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
install -d /opt/tensorfold-mod
install -m 0644 chat_template.jinja /opt/tensorfold-mod/chat_template.jinja
install -m 0755 capacity.py /opt/tensorfold-mod/capacity.py
install -m 0755 serve.py /usr/local/bin/tensorfold-entrypoint
