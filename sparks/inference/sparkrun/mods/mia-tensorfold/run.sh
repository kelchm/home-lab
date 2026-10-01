#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
python3 site_fixes.py
install -m 0755 serve.py /usr/local/bin/glm53-mia-tf-entrypoint
mkdir -p /opt/glm53-mia-tensorfold
install -m 0644 upstream/readiness.py /opt/glm53-mia-tensorfold/readiness.py
