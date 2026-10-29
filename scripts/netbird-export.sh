#!/usr/bin/env bash
# Export NetBird Cloud configuration to a private temporary directory with read-only API calls.
#
# The token comes from NETBIRD_TOKEN_FILE when set, otherwise from 1Password. Setup-key values are
# removed from the export. The output also holds peer addresses and IDs: keep it out of Git and issues.

set -euo pipefail

api="https://api.netbird.io/api"
token_ref="op://Project - home-lab/netbird-export/credential"

for command in curl jq; do
  command -v "${command}" >/dev/null || {
    echo "required command not found: ${command}" >&2
    exit 1
  }
done

umask 077
out="$(mktemp -d "${TMPDIR:-/tmp}/netbird-export.XXXXXX")"
header="${out}/.authorization"
trap 'rm -f "${header}"' EXIT

if [[ -n "${NETBIRD_TOKEN_FILE:-}" ]]; then
  token="$(tr -d '\r\n' <"${NETBIRD_TOKEN_FILE}")"
else
  token="$(op read --no-newline "${token_ref}")"
fi
printf 'Authorization: Token %s\n' "${token}" >"${header}"
unset token

failures=0

fetch() {
  local path="$1" file="$2" filter="${3:-.}" body
  if body="$(curl -q --silent --show-error --fail-with-body --max-time 20 \
    --header @"${header}" --header 'Accept: application/json' "${api}/${path}")"; then
    jq "${filter}" <<<"${body}" >"${out}/${file}.json"
    printf '%-40s ok, %s entries\n' "${path}" "$(jq 'if type == "array" then length else 1 end' "${out}/${file}.json")"
  else
    printf '%-40s failed: %s\n' "${path}" "$(jq -c . <<<"${body}" 2>/dev/null || echo "${body}")" >&2
    failures=$((failures + 1))
  fi
}

fetch accounts accounts
fetch groups groups
fetch posture-checks posture-checks
fetch policies policies
fetch dns/nameservers dns-nameservers
fetch dns/settings dns-settings
fetch peers peers
fetch setup-keys setup-keys 'map(del(.key))'
fetch 'users?service_user=true' service-users
fetch networks networks

if [[ -f "${out}/networks.json" ]]; then
  while IFS=$'\t' read -r id name; do
    name="${name//[^A-Za-z0-9._-]/_}"
    fetch "networks/${id}/resources" "network-${id}-${name}-resources"
    fetch "networks/${id}/routers" "network-${id}-${name}-routers"
  done < <(jq -r '.[] | [.id, .name] | @tsv' "${out}/networks.json")
fi

echo "export: ${out}"
[[ "${failures}" -eq 0 ]] || {
  echo "${failures} request(s) failed" >&2
  exit 1
}
