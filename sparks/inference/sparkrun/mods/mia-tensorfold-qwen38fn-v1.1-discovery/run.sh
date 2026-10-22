#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 prepare.py --check --require-binary
install -D -m 0755 out/tensorfold-native /opt/tensorfold-mod/bin/tensorfold-native
install -m 0755 serve.py /usr/local/bin/tensorfold-entrypoint
