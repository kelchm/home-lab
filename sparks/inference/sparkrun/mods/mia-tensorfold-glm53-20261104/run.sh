#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
install -d /opt/glm53-20261104
install -m 0644 chat_template.jinja /opt/glm53-20261104/chat_template.jinja
install -m 0755 capacity.py /opt/glm53-20261104/capacity.py
install -m 0755 serve.py /usr/local/bin/tensorfold-entrypoint
