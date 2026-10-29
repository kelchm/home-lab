#!/bin/bash
# Approved destination session only. Derived from 1099's destination provisioning checks.
# No VM mutation/start. Caller supplies reviewed source, filename and SHA-256.
set -euo pipefail
umask 077
if [[ $# != 3 || ! $3 =~ ^[0-9a-f]{64}$ ]]; then
    echo 'usage: provision-snippet.sh SOURCE netbird-router-2.yaml SHA256' >&2
    exit 64
fi
source_file=$1
snippet_name=$2
snippet_sha=$3
[[ "$snippet_name" == netbird-router-2.yaml ]]
[[ -f "$source_file" && ! -L "$source_file" ]]
provision=$(mktemp -d /root/netbird-snippet-provision.XXXXXX)
printf 'Provision evidence: %s\n' "$provision"
printf '%s  %s\n' "$snippet_sha" "$source_file" | sha256sum --check --status
pvesh get /storage/local --output-format json > "$provision/storage.before.json"
python3 - "$provision" <<'PYPROVISION'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
s = json.loads((root / 'storage.before.json').read_text())
assert s['type'] == 'dir' and s['path'] == '/var/lib/vz'
assert not s.get('content-dirs') and not s.get('shared') and not s.get('disable')
content = s['content'].split(',')
assert content and len(content) == len(set(content))
if 'snippets' not in content:
    (root / 'content.after').write_text(s['content'] + ',snippets')
    (root / 'storage.digest').write_text(s['digest'])
PYPROVISION
if test -f "$provision/content.after"; then
    pvesm set local --digest "$(cat "$provision/storage.digest")" --content "$(cat "$provision/content.after")"
fi
# Refuse even dangling symlinks before creating anything at the destination.
test ! -L /var/lib/vz/snippets
if test ! -e /var/lib/vz/snippets; then
    install -d -o root -g root -m 0755 /var/lib/vz/snippets
fi
test -d /var/lib/vz/snippets
destination=/var/lib/vz/snippets/$snippet_name
test ! -L "$destination"
if test -e "$destination"; then
    test -f "$destination"
else
    # Stage privately, then publish exclusively: a concurrent existing file is never overwritten.
    staged=$(mktemp /var/lib/vz/snippets/.netbird-vendor.XXXXXX)
    trap 'rm -f -- "$staged"' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    install -o root -g root -m 0644 "$source_file" "$staged"
    printf '%s  %s\n' "$snippet_sha" "$staged" | sha256sum --check --status
    ln -T -- "$staged" "$destination"
    rm -f -- "$staged"
fi
cmp "$source_file" "$destination"
resolved=$(pvesm path "local:snippets/$snippet_name")
test "$resolved" = "$destination"
printf '%s  %s\n' "$snippet_sha" "$resolved" | sha256sum --check --status
test "$(stat -c '%U:%G %a' "$resolved")" = 'root:root 644'
pvesh get /storage/local --output-format json > "$provision/storage.after.json"
python3 - "$provision" <<'PYPROVISIONCHECK'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
before, after = [json.loads((root / f'storage.{p}.json').read_text()) for p in ('before','after')]
assert not [k for k in before.keys() | after.keys() if k not in {'digest','content'} and before.get(k) != after.get(k)]
expected = before['content'].split(',')
if 'snippets' not in expected:
    expected.append('snippets')
assert sorted(after['content'].split(',')) == sorted(expected)
PYPROVISIONCHECK
