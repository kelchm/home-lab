#!/usr/bin/env bash
# Adapt talhelper 3.1.17's new-cluster encryption defaults for this existing cluster.
# Only provider metadata changes; key material stays in the generated file.
set -Eeuo pipefail

(( $# > 0 )) || { echo 'usage: preserve-encryption.sh <generated-config> [...]' >&2; exit 2; }

for config in "$@"; do
    encryption_kind="$(yq 'select(.kind == "KubeEtcdEncryptionConfig") | .kind' "$config")"
    [[ -n "$encryption_kind" ]] || continue
    [[ "$encryption_kind" == KubeEtcdEncryptionConfig ]] || { echo 'expected exactly one encryption document' >&2; exit 1; }
    if ! yq -o=json 'select(.kind == "KubeEtcdEncryptionConfig")' "$config" | jq --exit-status '.config.resources |
        length == 1 and .[0].resources == ["secrets"] and
        (.[0].providers | length == 1 or length == 2) and
        (.[0].providers[0] | keys == ["secretbox"]) and
        (.[0].providers[0].secretbox.keys | length == 1) and
        (.[0].providers[0].secretbox.keys[0] | (.name == "key1" or .name == "key2") and (.secret | type == "string" and length > 0)) and
        ((.[0].providers | length == 1) or .[0].providers[1] == {"identity": {}})' >/dev/null; then
        echo "${config##*/}: unexpected encryption providers; refusing to rewrite them" >&2
        exit 1
    fi
    yq -i '(select(.kind == "KubeEtcdEncryptionConfig") | .config.resources[0].providers) |=
        [((.[0] | .secretbox.keys[0].name = "key2")), {"identity": {}}]' "$config"
done
