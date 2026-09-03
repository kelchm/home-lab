#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly MANIFEST_DIR="${1:-${ROOT_DIR}/kubernetes}"

function main() {
    local failed=0
    local file
    local policy

    while IFS= read -r -d '' file; do
        # Cilium rejects a policy rule when all four traffic rule lists are
        # absent or empty. An empty object inside ingress/egress is valid and
        # intentionally enables default deny without granting an allow rule.
        # $name below is a yq variable, not a shell variable.
        # shellcheck disable=SC2016
        while IFS= read -r policy; do
            [[ -n "${policy}" ]] || continue
            printf 'Cilium policy %q in %s has no ingress or egress rules\n' \
                "${policy}" "${file#"${ROOT_DIR}"/}" >&2
            failed=1
        done < <(yq eval --unwrapScalar \
            'select(.kind == "CiliumNetworkPolicy" or
                    .kind == "CiliumClusterwideNetworkPolicy") |
             [{"name": .metadata.name, "rule": .spec},
              (.metadata.name as $name | .specs[]? |
                {"name": $name, "rule": .})] |
             .[] | select(.rule != null) |
             select(([.rule.ingress[]?, .rule.ingressDeny[]?,
                      .rule.egress[]?, .rule.egressDeny[]?] | length) == 0) |
             .name' "${file}")
    done < <(rg --files-with-matches --null \
        --glob '*.yaml' --glob '*.yml' \
        '^kind: Cilium(Network|ClusterwideNetwork)Policy$' \
        "${MANIFEST_DIR}")

    if ((failed != 0)); then
        return 1
    fi

    echo "All Cilium policies contain at least one ingress or egress rule."
}

main "$@"
