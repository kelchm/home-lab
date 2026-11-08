#!/usr/bin/env bash
# Exercise encryption checks with throwaway recipients and no decryption keys.

set -o errexit
set -o nounset
set -o pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
umask 077
readonly TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

unset SOPS_AGE_KEY
export SOPS_AGE_KEY_FILE="${TEST_DIR}/nonexistent"
"${SCRIPT_DIR}/validate-sops.sh"

mkdir -p "${TEST_DIR}/scripts/ci" "${TEST_DIR}/synology/app" "${TEST_DIR}/kubernetes/app" \
    "${TEST_DIR}/talos" "${TEST_DIR}/selectors/suffix" "${TEST_DIR}/selectors/regex"
cp "${SCRIPT_DIR}/validate-sops.sh" "${TEST_DIR}/scripts/ci/"
git -C "${TEST_DIR}" init --quiet
recipient="$(age-keygen -o "${TEST_DIR}/fixture.key" 2>/dev/null && age-keygen -y "${TEST_DIR}/fixture.key")"
wrong_recipient="$(age-keygen -o "${TEST_DIR}/wrong.key" 2>/dev/null && age-keygen -y "${TEST_DIR}/wrong.key")"
# The private fixture keys are never supplied to SOPS.
rm "${TEST_DIR}/fixture.key" "${TEST_DIR}/wrong.key"
# The repository's two YAML selector forms, plus the other path selectors the
# validator supports, so each is checked against the pinned SOPS binary.
cat > "${TEST_DIR}/.sops.yaml" <<EOF_CONFIG
creation_rules:
  - path_regex: talos/.*\\.sops\\.ya?ml
    mac_only_encrypted: true
    age: "${recipient}"
  - path_regex: kubernetes/.*\\.sops\\.ya?ml
    encrypted_regex: "^(data|stringData)$"
    mac_only_encrypted: true
    age: "${recipient}"
  - path_regex: selectors/suffix/.*\\.sops\\.yaml
    encrypted_suffix: _secret
    age: "${recipient}"
  - path_regex: selectors/regex/.*\\.sops\\.yaml
    unencrypted_regex: "^(apiVersion|kind|metadata)$"
    age: ["${recipient}"]
  - path_regex: synology/.*\\.sops\\.env
    age: ${recipient}
stores:
  yaml:
    indent: 2
EOF_CONFIG
export SOPS_CONFIG="${TEST_DIR}/.sops.yaml"
printf '# Fixture values only\nTOKEN=throwaway\nPASSWORD=another=value\n' > "${TEST_DIR}/plain.env"
sops encrypt --output "${TEST_DIR}/valid.env" --filename-override "${TEST_DIR}/synology/app/secrets.sops.env" "${TEST_DIR}/plain.env"
sops encrypt --filename-override "${TEST_DIR}/synology/app/secrets.sops.env" --age "${wrong_recipient}" --output "${TEST_DIR}/wrong.env" "${TEST_DIR}/plain.env"
readonly FIXTURE="${TEST_DIR}/synology/app/secrets.sops.env"
cp "${TEST_DIR}/valid.env" "${FIXTURE}"
git -C "${TEST_DIR}" add .
"${TEST_DIR}/scripts/ci/validate-sops.sh"
echo 'ok: encrypted dotenv with matching recipient accepted'

function expect_failure() {
    local name="$1" diagnostic="$2"
    if "${TEST_DIR}/scripts/ci/validate-sops.sh" > "${TEST_DIR}/output.log" 2>&1; then
        echo "FAIL: ${name} unexpectedly passed" >&2
        exit 1
    fi
    if ! grep -Fq "${diagnostic}" "${TEST_DIR}/output.log"; then
        echo "FAIL: ${name} failed for an unexpected reason" >&2
        cat "${TEST_DIR}/output.log" >&2
        exit 1
    fi
    echo "ok: ${name} rejected (${diagnostic})"
}

cp "${TEST_DIR}/plain.env" "${FIXTURE}"
expect_failure 'plaintext dotenv' 'SOPS file is not encrypted'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf 'PLAINTEXT=oops\n' >> "${FIXTURE}"
expect_failure 'partially encrypted dotenv' 'dotenv value is not encrypted'

cp "${TEST_DIR}/wrong.env" "${FIXTURE}"
expect_failure 'wrong recipient' 'age recipients do not match the creation rule'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf 'sops_fake=oops\n' >> "${FIXTURE}"
expect_failure 'unknown metadata prefix' 'unsupported dotenv SOPS metadata'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf 'TOKEN=oops\n' >> "${FIXTURE}"
expect_failure 'duplicate assignment' 'invalid or duplicate dotenv assignment'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf 'MALFORMED=ENC[AES256_GCM,data:YQ==,iv:YQ==,tag:YQ==,type:str]\n' >> "${FIXTURE}"
expect_failure 'malformed ciphertext' 'dotenv value is not encrypted'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf '#OLD_PASSWORD=throwaway-plaintext\n' >> "${FIXTURE}"
expect_failure 'plaintext comment' 'dotenv comment is not encrypted'

cp "${TEST_DIR}/valid.env" "${FIXTURE}"
printf '#ENC[AES256_GCM,data:YQ==,iv:YQ==,tag:YQ==,type:comment]\n' >> "${FIXTURE}"
expect_failure 'malformed encrypted comment' 'dotenv comment is not encrypted'

printf 'TOKEN=throwaway\nEMPTY=\n' > "${TEST_DIR}/empty.env"
sops encrypt --filename-override "${FIXTURE}" --output "${FIXTURE}" "${TEST_DIR}/empty.env"
expect_failure 'SOPS empty value' 'empty dotenv values are not supported'

# A real age file with a second recipient must not fit one metadata entry.
printf '%032d' 0 | age --encrypt --armor --recipient "${recipient}" --recipient "${wrong_recipient}" > "${TEST_DIR}/extra-recipient.age"

function edit_envelope() {
    python3 - "${TEST_DIR}" "$1" <<'PYTHON'
import base64
from pathlib import Path
import sys

root, mode = Path(sys.argv[1]), sys.argv[2]
lines = (root / "valid.env").read_text().split("\n")
for index, line in enumerate(lines):
    if not line.startswith("sops_age__list_0__map_enc="):
        continue
    envelope = line.partition("=")[2].replace("\\n", "\n")
    armor = envelope.split("\n")[1:-2]
    raw = base64.b64decode("".join(armor))
    if mode == "plaintext":
        body = "throwaway plaintext"
    elif mode == "empty":
        body = ""
    else:
        if mode == "base64-plaintext":
            raw = b"throwaway plaintext"
        elif mode == "wrong-stanza":
            raw = raw.replace(b"-> X25519 ", b"-> ssh-ed25519 ", 1)
        elif mode == "truncated-payload":
            raw = raw[:-1]
        elif mode == "extra-recipient":
            envelope = (root / "extra-recipient.age").read_text()
            lines[index] = "sops_age__list_0__map_enc=" + envelope.replace("\n", "\\n")
            break
        encoded = base64.b64encode(raw).decode()
        body = "\n".join(encoded[i:i+64] for i in range(0, len(encoded), 64))
    envelope = "-----BEGIN AGE ENCRYPTED FILE-----\n" + body + "\n-----END AGE ENCRYPTED FILE-----\n"
    lines[index] = "sops_age__list_0__map_enc=" + envelope.replace("\n", "\\n")
    break
(root / "synology/app/secrets.sops.env").write_text("\n".join(lines))
PYTHON
}

for mode in plaintext empty base64-plaintext wrong-stanza truncated-payload extra-recipient; do
    edit_envelope "${mode}"
    expect_failure "age envelope ${mode}" 'invalid age encrypted-key envelope'
done

cp "${TEST_DIR}/valid.env" "${FIXTURE}"

# Every value is a fake fixture. Comments sit at each boundary where SOPS's
# YAML store decides between the enclosing mapping and the node itself.
cat > "${TEST_DIR}/plain.yaml" <<'EOF_YAML'
# fixture: top of file
apiVersion: v1 # fixture: line on apiVersion
kind: Secret
metadata:
  # fixture: head inside metadata
  name: fixture
  labels: {app: fixture}
# fixture: head of stringData key
stringData: # fixture: line on stringData key
  # fixture: head of first entry
  token: fixture-token # fixture: inline on token
  # fixture: between entries
  count: 3
  flag: true
  ratio: 1.5
  empty: ""
  nothing: null
  nested:
    # fixture: head in nested
    inner_secret: fixture-inner
    list:
      # fixture: head of list item
      - fixture-item-one # fixture: inline on item
      - key: fixture-item-two
        # fixture: foot of map item
      - - fixture-deep
    # fixture: trailing nested
  plain_unencrypted: fixture-visible
  # fixture: trailing stringData
# fixture: head of data
data:
  blob: Zml4dHVyZQ==
other_secret: fixture-other
tail:
  - fixture-tail
# fixture: end of document
---
# fixture: second document
apiVersion: v1
kind: ConfigMap
stringData:
  second: fixture-second
EOF_YAML
readonly YAML_FIXTURE="${TEST_DIR}/kubernetes/app/secret.sops.yaml"
for target in "${YAML_FIXTURE}" "${TEST_DIR}/talos/talsecret.sops.yaml" \
    "${TEST_DIR}/selectors/suffix/secret.sops.yaml" "${TEST_DIR}/selectors/regex/secret.sops.yaml"; do
    sops encrypt --filename-override "${target}" --output "${target}" "${TEST_DIR}/plain.yaml"
done
cp "${YAML_FIXTURE}" "${TEST_DIR}/valid.yaml"
git -C "${TEST_DIR}" add .
"${TEST_DIR}/scripts/ci/validate-sops.sh"
echo 'ok: SOPS-encrypted YAML accepted for every path selector, comments included'

# Insert one line after the nth line equal to anchor in the valid YAML fixture.
# CIPHERTEXT in the line stands for the fixture's first encrypted value.
function inject_yaml() {
    python3 - "${TEST_DIR}/valid.yaml" "${YAML_FIXTURE}" "$@" <<'PYTHON'
from pathlib import Path
import re
import sys

source, target, anchor, line = sys.argv[1:5]
occurrence = int(sys.argv[5]) if len(sys.argv) > 5 else 1
text = Path(source).read_text()
lines = text.split("\n")
index = [i for i, entry in enumerate(lines) if entry == anchor][occurrence - 1]
lines.insert(index + 1, line.replace("CIPHERTEXT", re.search(r"ENC\[[^]]*type:str\]", text)[0]))
Path(target).write_text("\n".join(lines))
PYTHON
}

inject_yaml 'stringData:' '  leaked: throwaway-plaintext'
expect_failure 'plaintext YAML value' 'plaintext value at stringData.leaked'

inject_yaml 'stringData:' '  # throwaway-plaintext-comment'
expect_failure 'plaintext YAML comment' 'plaintext comment in stringData'

inject_yaml 'stringData:' '  leaked: throwaway-plaintext' 2
expect_failure 'plaintext value in a later document' 'plaintext value at stringData.leaked'

inject_yaml 'stringData:' '  sops: throwaway-plaintext'
expect_failure 'nested sops key' 'plaintext value at stringData.sops'

inject_yaml 'kind: Secret' 'misplaced: CIPHERTEXT'
expect_failure 'ciphertext outside the selector' 'ciphertext value at misplaced, outside the encryption selector'

inject_yaml 'kind: Secret' 'source: &fixture throwaway-plaintext'
expect_failure 'YAML anchor' 'YAML anchors and aliases are not supported'

sops encrypt --filename-override "${YAML_FIXTURE}" --encrypted-regex '^data$' --output "${YAML_FIXTURE}" "${TEST_DIR}/plain.yaml"
expect_failure 'YAML selector differs from the creation rule' 'encryption selector does not match the creation rule'

sops encrypt --filename-override "${YAML_FIXTURE}" --age "${wrong_recipient}" --output "${YAML_FIXTURE}" "${TEST_DIR}/plain.yaml"
expect_failure 'wrong YAML recipient' 'age recipients do not match the creation rule'

cp "${TEST_DIR}/valid.yaml" "${YAML_FIXTURE}"
printf 'leaked: throwaway-plaintext\n' >> "${TEST_DIR}/talos/talsecret.sops.yaml"
expect_failure 'plaintext value under the default selector' 'plaintext value at leaked'

echo 'SOPS validator regression tests passed.'
