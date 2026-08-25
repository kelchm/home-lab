#!/usr/bin/env bash
# Check that docs/README.md indexes every plan and runbook, and that dated plans carry a recognized status line.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "${ROOT}"

INDEX="docs/README.md"
violations=0

# Rule 1: every plan and runbook file must be referenced by filename in the docs index.
while IFS= read -r -d '' file; do
  base="$(basename "${file}")"
  if ! grep -Fq "${base}" "${INDEX}"; then
    echo "docs index missing reference to ${file}"
    violations=$((violations + 1))
  fi
done < <(find docs/plans docs/runbooks -maxdepth 1 -type f -name '*.md' -print0 | sort -z)

# Rule 2: every dated plan must contain a recognized status line.
status_re='^\*\*Status:\*\* (Proposed|Active|Implemented|Superseded)\b'
while IFS= read -r -d '' file; do
  if ! grep -Eq "${status_re}" "${file}"; then
    echo "plan status missing or unrecognized in ${file}"
    violations=$((violations + 1))
  fi
done < <(find docs/plans -maxdepth 1 -type f -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-*.md' -print0 | sort -z)

if ((violations > 0)); then
  echo "docs index validation failed with ${violations} violation(s)"
  exit 1
fi

echo "docs index and plan statuses OK"
