#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
install -d /opt/glm53-1104-r2
install -m 0644 chat_template.jinja /opt/glm53-1104-r2/chat_template.jinja
install -m 0755 capacity.py /opt/glm53-1104-r2/capacity.py
install -m 0755 serve.py /usr/local/bin/tensorfold-entrypoint
