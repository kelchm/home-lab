#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

function main() {
    local decrypt_available=false
    local file
    local require_decrypt=false
    local status

    if [[ "${1:-}" == '--require-decrypt' ]]; then
        require_decrypt=true
    elif (($# != 0)); then
        echo "Usage: ${0##*/} [--require-decrypt]" >&2
        return 2
    fi

    if [[ -n "${SOPS_AGE_KEY:-}" ]] || [[ -f "${SOPS_AGE_KEY_FILE:-/nonexistent}" ]]; then
        decrypt_available=true
    fi

    if [[ "${require_decrypt}" == true && "${decrypt_available}" != true ]]; then
        echo "SOPS decryption is required, but no age key is available." >&2
        return 1
    fi

    while IFS= read -r -d '' file; do
        [[ "${file}" != '.sops.yaml' ]] || continue

        status="$(sops filestatus "${ROOT_DIR}/${file}")"
        if [[ "${status}" != *'"encrypted":true'* ]]; then
            echo "SOPS file is not encrypted: ${file}" >&2
            return 1
        fi

        if [[ "${decrypt_available}" == true ]]; then
            sops decrypt "${ROOT_DIR}/${file}" >/dev/null
        fi
    done < <(git -C "${ROOT_DIR}" ls-files -z -- '*.sops.yaml' '*.sops.yml')

    if [[ "${decrypt_available}" == true ]]; then
        echo "All tracked SOPS files are encrypted and decrypt successfully."
    else
        echo "All tracked SOPS files are encrypted. Decryption was not attempted because no age key is available."
    fi
}

main "$@"
