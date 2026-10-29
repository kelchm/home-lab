#!/usr/bin/env bash
# Prepare public, pinned client files locally. Does not install or contact Athena.
set -euo pipefail
umask 077

if [[ $# != 1 || -e $1 || -L $1 ]]; then
    echo "Usage: $0 <new-local-staging-directory>" >&2
    exit 64
fi

stage=$1
commit=807da6498377ee5e0cf43a78091f46f12dc59a89
mkdir -p "$stage/client/dnsapi"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    "https://raw.githubusercontent.com/acmesh-official/acme.sh/$commit/acme.sh" \
    --output "$stage/client/acme.sh"
cp "$(dirname "${BASH_SOURCE[0]}")/dns_athena.sh" "$stage/client/dnsapi/dns_athena.sh"
python3 - "$stage" <<'PY'
import hashlib
import pathlib
import sys

stage = pathlib.Path(sys.argv[1])
expected = {
    "client/acme.sh": "c7d68b021cfd6380ea83a82962abde5b484779fee0b97d38681dfa1396bbc8d7",
    "client/dnsapi/dns_athena.sh": "789ba78a2020ebe20ea4d8b8de6bd7fb09bda123acae29aaf7cef085d8fa6c5e",
}
for name, digest in expected.items():
    target = stage / name
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SystemExit("Pinned client checksum mismatch: " + name)
    target.chmod(0o600)
PY
echo "Prepared pinned acme.sh 3.1.6 core and native Athena DNS adapter."
