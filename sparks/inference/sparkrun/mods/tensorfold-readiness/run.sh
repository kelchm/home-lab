#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check
install -d /opt/tensorfold-readiness
install -m 0644 readiness.py /opt/tensorfold-readiness/
