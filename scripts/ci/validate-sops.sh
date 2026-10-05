#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Dotenv is flat: SOPS serializes metadata as sops_* assignments.
# Match the pinned SOPS dotenv parser (comments, first '=', literal \n escapes),
# without sourcing the file or ever passing values to a shell.
function validate_dotenv() {
    local file="$1"
    local rules
    rules="$(yq -o=json '.creation_rules' "${ROOT_DIR}/.sops.yaml")"
    python3 - "${ROOT_DIR}/${file}" "${file}" "${rules}" <<'PYTHON'
import json
import re
import sys

path, relative, rules_json = sys.argv[1:]

def fail(message):
    sys.exit(f"{relative}: {message}")

rule = next((r for r in json.loads(rules_json)
             if re.search(r.get("path_regex", ""), relative)), None)
if rule is None or not rule.get("age"):
    fail("no matching age creation rule")
# Fail explicitly if the rule grows beyond the age-only policy checked here.
if set(rule) - {"path_regex", "age"}:
    fail("unsupported dotenv creation rule; expected an age-only rule")
expected = sorted(r.strip() for r in rule["age"].split(",") if r.strip())
fields = {}
with open(path, encoding="utf-8") as stream:
    for line in stream.read().split("\n"):
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in fields:
            fail("invalid or duplicate dotenv assignment")
        fields[key] = value.replace("\\n", "\n")

ciphertext = re.compile(
    r"ENC\[AES256_GCM,data:[A-Za-z0-9+/=]*,iv:[A-Za-z0-9+/=]+,"
    r"tag:[A-Za-z0-9+/=]+,type:str\]")
recipients = []
for key, value in fields.items():
    if not key.startswith("sops_"):
        if not ciphertext.fullmatch(value):
            fail("dotenv value is not encrypted")
        continue
    match = re.fullmatch(r"sops_age__list_(\d+)__map_(recipient|enc)", key)
    if match:
        if match[2] == "recipient":
            recipients.append(value)
            envelope = fields.get(f"sops_age__list_{match[1]}__map_enc", "")
            if not (envelope.startswith("-----BEGIN AGE ENCRYPTED FILE-----\n")
                    and envelope.endswith("\n-----END AGE ENCRYPTED FILE-----\n")):
                fail("missing age encrypted-key envelope")
        elif f"sops_age__list_{match[1]}__map_recipient" not in fields:
            fail("age encrypted-key envelope has no recipient")
        continue
    if key not in {"sops_lastmodified", "sops_mac", "sops_unencrypted_suffix", "sops_version"}:
        fail("unsupported dotenv SOPS metadata")
if not ciphertext.fullmatch(fields.get("sops_mac", "")):
    fail("missing encrypted SOPS MAC")
if sorted(recipients) != expected:
    fail("age recipients do not match the creation rule")
PYTHON
}

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

        if [[ "${file}" == *.sops.env ]]; then
            validate_dotenv "${file}"
        elif [[ "${decrypt_available}" == true ]]; then
            sops decrypt "${ROOT_DIR}/${file}" >/dev/null
        fi
    done < <(git -C "${ROOT_DIR}" ls-files -z -- '*.sops.yaml' '*.sops.yml' '*.sops.env')

    if [[ "${decrypt_available}" == true ]]; then
        echo "All tracked SOPS files pass encryption checks; YAML files decrypt successfully. Dotenv files were checked without decryption."
    else
        echo "All tracked SOPS files are encrypted. Decryption was not attempted because no age key is available."
    fi
}

main "$@"
