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
import base64
import binascii
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
ciphertext = re.compile(
    r"ENC\[AES256_GCM,data:([A-Za-z0-9+/=]+),iv:([A-Za-z0-9+/=]+),"
    r"tag:([A-Za-z0-9+/=]+),type:(str|comment)\]")

def encrypted(value, datatype="str"):
    match = ciphertext.fullmatch(value)
    if not match or match[4] != datatype:
        return False
    try:
        data, iv, tag = (base64.b64decode(v, validate=True) for v in match.groups()[:3])
        return bool(data) and len(iv) == 32 and len(tag) == 16
    except binascii.Error:
        return False

# Every age metadata entry wraps one SOPS data key for one X25519 recipient.
# Validate the public framing only: no private key or decryption is involved.
def age_envelope(value):
    lines = value.split("\n")
    if (len(lines) < 4 or lines[0] != "-----BEGIN AGE ENCRYPTED FILE-----"
            or lines[-2:] != ["-----END AGE ENCRYPTED FILE-----", ""]):
        return False
    armor = lines[1:-2]
    if (not armor or any(len(line) != 64 for line in armor[:-1])
            or not 1 <= len(armor[-1]) <= 64):
        return False
    try:
        encoded = "".join(armor).encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
        if base64.b64encode(decoded) != encoded:
            return False
        intro, stanza, body, mac, payload = decoded.split(b"\n", 4)
        if intro != b"age-encryption.org/v1" or len(payload) != 64:
            return False
        if not stanza.startswith(b"-> X25519 ") or not mac.startswith(b"--- "):
            return False
        # Ephemeral public key, wrapped file key, and header MAC are each 32 bytes.
        for token in (stanza[len(b"-> X25519 "):], body, mac[len(b"--- "):]):
            if not re.fullmatch(rb"[A-Za-z0-9+/]{43}", token):
                return False
            raw = base64.b64decode(token + b"=", validate=True)
            if len(raw) != 32 or base64.b64encode(raw).rstrip(b"=") != token:
                return False
        return True
    except (ValueError, UnicodeError):
        return False

fields = {}
with open(path, encoding="utf-8", newline="") as stream:
    for line in stream.read().split("\n"):
        if not line:
            continue
        if line.startswith("#"):
            comment = line[1:].replace("\\n", "\n")
            if comment and not encrypted(comment, "comment"):
                fail("dotenv comment is not encrypted")
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in fields:
            fail("invalid or duplicate dotenv assignment")
        fields[key] = value.replace("\\n", "\n")

recipients = []
for key, value in fields.items():
    if not key.startswith("sops_"):
        if not value:
            fail("empty dotenv values are not supported; SOPS leaves them unencrypted")
        if not encrypted(value):
            fail("dotenv value is not encrypted")
        continue
    match = re.fullmatch(r"sops_age__list_(\d+)__map_(recipient|enc)", key)
    if match:
        if match[2] == "recipient":
            recipients.append(value)
            envelope = fields.get(f"sops_age__list_{match[1]}__map_enc", "")
            if not age_envelope(envelope):
                fail("invalid age encrypted-key envelope")
        elif f"sops_age__list_{match[1]}__map_recipient" not in fields:
            fail("age encrypted-key envelope has no recipient")
        continue
    if key not in {"sops_lastmodified", "sops_mac", "sops_unencrypted_suffix", "sops_version"}:
        fail("unsupported dotenv SOPS metadata")
if not encrypted(fields.get("sops_mac", "")):
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
