#!/usr/bin/env bash
# Prepare public, pinned client files locally. Does not install or contact Athena.
set -euo pipefail
umask 077

if [[ $# != 1 || -e $1 || -L $1 ]]; then
    echo "Usage: $0 <new-local-staging-directory>" >&2
    exit 64
fi

stage=$1
mkdir -p "$stage/bin"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    'https://github.com/go-acme/lego/releases/download/v5.6.0/lego_v5.6.0_linux_amd64.tar.gz' \
    --output "$stage/lego.tar.gz"
python3 - "$stage" <<'PY'
import hashlib
import pathlib
import sys

stage = pathlib.Path(sys.argv[1])
digest = "f02fc11b6f56b97e387fa7caa0268f86f5a336953b22458e6430e4651fea439a"
if hashlib.sha256((stage / "lego.tar.gz").read_bytes()).hexdigest() != digest:
    raise SystemExit("Pinned Lego archive checksum mismatch")
PY
tar -xzf "$stage/lego.tar.gz" -C "$stage/bin" lego
chmod 0700 "$stage/bin/lego"
rm "$stage/lego.tar.gz"
source_dir=$(dirname "${BASH_SOURCE[0]}")
cp "$source_dir/deploy-athena-dsm.py" "$stage/"
cp "$source_dir/athena-lego.yml" "$stage/lego.yml"
chmod 0600 "$stage/deploy-athena-dsm.py" "$stage/lego.yml"
echo "Prepared stock Lego 5.6.0 for Athena (linux/amd64), its configuration and the local DSM import hook."
