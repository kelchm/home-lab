#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Check a SOPS file's ciphertext and recipients without its age key and
# without ever printing a value. YAML files mirror the pinned SOPS 3.13.3 tree
# walk; dotenv files match its dotenv parser (comments, first '=', literal \n
# escapes) without sourcing the file or passing values to a shell.
function validate_file() {
    local file="$1"
    local rules
    rules="$(yq -o=json '.creation_rules' "${ROOT_DIR}/.sops.yaml")"
    python3 - "${ROOT_DIR}/${file}" "${file}" "${rules}" <<'PYTHON'
import base64
import binascii
import json
import os
import re
import subprocess
import sys

path, relative, rules_json = sys.argv[1:]

def fail(message):
    sys.exit(f"{relative}: {message}")

rule = next((r for r in json.loads(rules_json)
             if re.search(r.get("path_regex", ""), relative)), None)
if rule is None or not rule.get("age"):
    fail("no matching age creation rule")
# SOPS accepts a comma-separated string or a list.
age = rule["age"] if isinstance(rule["age"], str) else ",".join(rule["age"])
expected = sorted(r.strip() for r in age.split(",") if r.strip())
ciphertext = re.compile(
    r"ENC\[AES256_GCM,data:([A-Za-z0-9+/=]+),iv:([A-Za-z0-9+/=]+),"
    r"tag:([A-Za-z0-9+/=]+),type:(str|int|float|bool|time|comment)\]")

def encrypted(value, datatypes=("str",)):
    match = ciphertext.fullmatch(value)
    if not match or match[4] not in datatypes:
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

def validate_dotenv():
    # Fail explicitly if the rule grows beyond the age-only policy checked here.
    if set(rule) - {"path_regex", "age"}:
        fail("unsupported dotenv creation rule; expected an age-only rule")
    fields = {}
    with open(path, encoding="utf-8", newline="") as stream:
        for line in stream.read().split("\n"):
            if not line:
                continue
            if line.startswith("#"):
                comment = line[1:].replace("\\n", "\n")
                if comment and not encrypted(comment, ("comment",)):
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

# One JSON line per YAML document. yq parses with the go-yaml lineage SOPS
# uses, so comments attach to the same nodes; values never reach the output.
YAML_NODES = """{
  "kind": kind, "tag": tag, "sops": (if kind == "map" then .sops else null end),
  "comments": [headComment, lineComment, footComment],
  "nodes": [.. | select(path | length > 0) | {
    "path": path, "kind": kind, "tag": tag, "anchor": anchor,
    "value": (if kind == "scalar" then . else null end),
    "comments": [headComment, lineComment, footComment],
    "parent": (parent | kind),
    "key": (if (parent | kind) == "map" then (key | {
      "tag": tag, "anchor": anchor, "comments": [headComment, lineComment, footComment]}) else null end)
  }]
}"""
SELECTORS = ("unencrypted_suffix", "encrypted_suffix", "unencrypted_regex", "encrypted_regex")
COMMENT_SELECTORS = ("unencrypted_comment_regex", "encrypted_comment_regex")
# SOPS writes an encrypted comment inside a sequence back as a list item.
VALUE_TYPES = ("str", "int", "float", "bool", "time", "comment")

def yq(expression, *args, stdin=None, env=None):
    result = subprocess.run(["yq", "-o=json", "-I0", expression, *args], input=stdin,
                            capture_output=True, text=True, env={**os.environ, **(env or {})})
    if result.returncode:
        # yq's diagnostics can quote file content, so they are not echoed.
        fail("yq could not evaluate the YAML file or its encryption selector")
    return [json.loads(line) for line in result.stdout.splitlines()]

# SOPS records one selector, defaulting to unencrypted_suffix: _unencrypted.
# Comment selectors depend on comment order, so they fail closed here.
def selector(settings, source):
    chosen = [(k, settings[k]) for k in SELECTORS + COMMENT_SELECTORS if settings.get(k)]
    if len(chosen) > 1:
        fail(f"{source} sets more than one encryption selector")
    if chosen and chosen[0][0] in COMMENT_SELECTORS:
        fail(f"{source} uses a comment selector, which this check does not support")
    return chosen[0] if chosen else ("unencrypted_suffix", "_unencrypted")

def validate_yaml():
    if set(rule) - {"path_regex", "age", "mac_only_encrypted", *SELECTORS, *COMMENT_SELECTORS}:
        fail("unsupported YAML creation rule; expected age recipients and an encryption selector")
    docs = yq(YAML_NODES, path)
    # SOPS reads metadata from the first document and drops every top-level sops key.
    metadata = docs[0]["sops"] if docs else None
    if not isinstance(metadata, dict):
        fail("missing SOPS metadata mapping in the first document")

    # Mirror the SOPS YAML store: comments on a key, or on a scalar value,
    # belong to the enclosing mapping; comments on a mapping or sequence, and
    # on any sequence item, belong to that node. Sequence indexes are not part
    # of a SOPS path, which holds mapping keys only.
    entries = []
    def add_comments(keys, texts):
        for text in texts:
            for line in text.split("\n"):
                # yq drops a leading "# ", SOPS only the "#".
                line = line[1:] if line.startswith("#") else line
                if line:
                    entries.append((keys, True, line))
    for doc in docs:
        if doc["kind"] != "map" and doc["tag"] != "!!null":
            fail("YAML document is not a mapping")
        add_comments((), doc["comments"])
        seen = set()
        for node in doc["nodes"]:
            nodepath = tuple(node["path"])
            if nodepath in seen:
                fail("duplicate YAML mapping key")
            seen.add(nodepath)
            if node["anchor"] or node["kind"] == "alias" or (node["key"] or {}).get("anchor"):
                fail("YAML anchors and aliases are not supported; SOPS never writes them")
            if node["key"] and node["key"]["tag"] != "!!str":
                fail("non-string YAML mapping key; SOPS supports string keys only")
            keys = tuple(p for p in nodepath if isinstance(p, str))
            container = keys[:-1] if node["parent"] == "map" else keys
            if node["key"]:
                add_comments(container, node["key"]["comments"])
            if nodepath[0] == "sops":
                continue
            if node["kind"] == "scalar":
                add_comments(container, node["comments"])
                entries.append((keys, False, node))
            else:
                add_comments(keys, node["comments"])

    kind, pattern = selector(metadata, "SOPS metadata")
    if (kind, pattern) != selector(rule, "creation rule"):
        fail("SOPS metadata encryption selector does not match the creation rule")
    if metadata.get("mac_only_encrypted", False) != rule.get("mac_only_encrypted", False):
        fail("SOPS metadata mac_only_encrypted does not match the creation rule")
    if kind.endswith("_regex"):
        # Evaluate with Go's regexp, as SOPS does, rather than Python's re.
        names = sorted({k for keys, _, _ in entries for k in keys})
        hits = yq('[.[] | test(strenv(SOPS_SELECTOR))]', "-p=json", "-",
                  stdin=json.dumps(names), env={"SOPS_SELECTOR": pattern}) if names else [[]]
        matches = dict(zip(names, hits[0]))
    def selected(keys):
        if kind.endswith("_suffix"):
            hit = any(k.endswith(pattern) for k in keys)
        else:
            hit = any(matches[k] for k in keys)
        return hit == kind.startswith("encrypted")

    for keys, is_comment, item in entries:
        if is_comment:
            ciphertext_found = encrypted(item, ("comment",))
        elif item["tag"] == "!!null" or (item["tag"] == "!!str" and item["value"] == ""):
            continue  # SOPS leaves null and empty values as they are.
        else:
            ciphertext_found = item["tag"] == "!!str" and encrypted(item["value"], VALUE_TYPES)
        what = f"{'comment in' if is_comment else 'value at'} {'.'.join(keys) or 'the document root'}"
        if selected(keys) and not ciphertext_found:
            fail(f"plaintext {what}; the encryption selector requires ciphertext")
        if not selected(keys) and ciphertext_found:
            fail(f"ciphertext {what}, outside the encryption selector")

    if set(metadata) - {"age", "lastmodified", "mac", "version", "mac_only_encrypted",
                        *SELECTORS, *COMMENT_SELECTORS}:
        fail("unsupported YAML SOPS metadata")
    recipients = metadata.get("age")
    if not isinstance(recipients, list) or not recipients:
        fail("missing age recipients")
    for entry in recipients:
        if (not isinstance(entry, dict) or set(entry) != {"recipient", "enc"}
                or not isinstance(entry["enc"], str) or not age_envelope(entry["enc"])):
            fail("invalid age encrypted-key envelope")
    if not isinstance(metadata.get("mac"), str) or not encrypted(metadata["mac"]):
        fail("missing encrypted SOPS MAC")
    if sorted(str(entry["recipient"]) for entry in recipients) != expected:
        fail("age recipients do not match the creation rule")

if relative.endswith(".env"):
    validate_dotenv()
else:
    validate_yaml()
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

        validate_file "${file}"
        if [[ "${file}" != *.sops.env && "${decrypt_available}" == true ]]; then
            sops decrypt "${ROOT_DIR}/${file}" >/dev/null
        fi
    done < <(git -C "${ROOT_DIR}" ls-files -z -- '*.sops.yaml' '*.sops.yml' '*.sops.env')

    if [[ "${decrypt_available}" == true ]]; then
        echo "All tracked SOPS files pass encryption and recipient checks; YAML files decrypt successfully. Dotenv files were checked without decryption."
    else
        echo "All tracked SOPS files pass encryption and recipient checks. Decryption was not attempted because no age key is available."
    fi
}

main "$@"
