#!/usr/bin/env bash
# Validate the catalog and, optionally, reject destructive changes to live labels.
set -euo pipefail

readonly ROOT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
readonly LIVE_LABELS="${2:-}"
catalog="$(yq -o=json '.' "${ROOT_DIR}/.github/labels.yaml")"
rules="$(yq -o=json '.' "${ROOT_DIR}/.github/labeler.yaml")"

jq --exit-status --null-input --argjson catalog "${catalog}" --argjson rules "${rules}" '
  def require($ok; $message): if $ok then . else error($message) end;
  require($catalog | type == "array" and length > 0; "catalog must be a nonempty array") |
  require($catalog | all(.[];
    (.name | type == "string" and test("^[a-z0-9][a-z0-9/-]*$")) and
    (.color | type == "string" and test("^[0-9a-fA-F]{6}$")) and
    ((.delete // false) == false) and
    ((.description // "") | type == "string" and length <= 100) and
    ((.aliases // []) | type == "array" and all(.[]; type == "string" and test("^[a-z0-9][a-z0-9/-]*$")))
  ); "invalid label fields or explicit deletion (use exact, unique names and aliases)") |
  ([$catalog[] | .name, (.aliases // [])[]]) as $names |
  require(($names | unique | length) == ($names | length); "duplicate label names or aliases") |
  require($rules | type == "object" and length > 0; "labeler must be a nonempty mapping") |
  (($rules | keys) - [$catalog[].name]) as $missing |
  require(($missing | length) == 0; "labeler keys absent from catalog: \($missing | join(", "))") |
  true
' >/dev/null

echo "Label catalog and labeler keys OK"
if [[ -n "${LIVE_LABELS}" ]]; then
    jq --exit-status --argjson catalog "${catalog}" '
      def require($ok; $message): if $ok then . else error($message) end;
      require(type == "array" and length > 0 and all(.[]; .name | type == "string"); "live labels must be a nonempty JSON array") |
      [.[].name | ascii_downcase] as $live |
      ([$catalog[] | .name, (.aliases // [])[]]) as $retained |
      ($live - $retained) as $deleted |
      require(($deleted | length) == 0; "STOP: labels would be deleted: \($deleted | join(", "))") |
      [$catalog[] | . as $label |
        [$label.name, ($label.aliases // [])[]] as $sources |
        [$live[] | select(. as $name | $sources | index($name))] as $matches |
        select(($matches | length) > 1) |
        "\($matches | join(" + ")) -> \($label.name)"
      ] as $merges |
      require(($merges | length) == 0; "STOP: labels would be merged: \($merges | join("; "))") |
      true
    ' "${LIVE_LABELS}" >/dev/null
    echo "Live label preflight OK: no deletions or merges"
fi
