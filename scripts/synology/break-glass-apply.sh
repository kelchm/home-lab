#!/usr/bin/env bash
# Break-glass apply of one Synology Compose project over SSH, for when doco-cd
# is unavailable or a fix cannot wait for a merge. See synology/README.md.
#
# Usage: SOPS_AGE_KEY="$(op read 'op://Private/athena-doco-cd-sops-age-key/password')" \
#          scripts/synology/break-glass-apply.sh <project>
#
# Decrypts the project's *.sops.env files into a private local staging
# directory and refuses to continue while any staged file still carries SOPS
# ciphertext or metadata. Only then does it stop doco-cd and run
# docker-compose on Athena. On exit, whether or not it succeeded, it removes
# the local copy and the decrypted files on Athena.

set -euo pipefail
umask 077

readonly NAS="kelchm@10.32.20.5"
readonly REMOTE_ROOT="/volume1/docker/break-glass"

# check_plaintext DIR: fail if any file under DIR still looks SOPS-encrypted.
check_plaintext() {
    local leftovers
    leftovers="$(grep -rlE 'ENC\[[A-Z0-9_]+,data:|^sops_[a-z_]+=|^sops:|"sops": *\{' "$1")" || true
    if [[ -n "${leftovers}" ]]; then
        echo "refusing to deploy, still encrypted:" >&2
        echo "${leftovers}" >&2
        return 1
    fi
}

# stage_project SOURCE DEST: copy SOURCE into the empty DEST, decrypting each
# *.sops.env in place under its own name, which compose.yaml references.
stage_project() {
    local -r source="$1" dest="$2"
    local file
    cp -R "${source}/." "${dest}/" || return 1
    while IFS= read -r -d '' file; do
        sops decrypt --input-type dotenv --output-type dotenv --output "${file}.plain" "${file}" || return 1
        mv -f "${file}.plain" "${file}" || return 1
    done < <(find "${dest}" -type f -name '*.sops.env' -print0)
    check_plaintext "${dest}"
}

main() {
    if [[ $# -ne 1 || ! "$1" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        echo "usage: $0 <project>  (a directory under synology/; doco-cd itself has its own procedure)" >&2
        exit 2
    fi
    local -r project="$1"
    local -r source="$(git rev-parse --show-toplevel)/synology/${project}"
    local -r remote="${REMOTE_ROOT}/${project}"
    [[ -f "${source}/compose.yaml" ]] || { echo "no Compose project at ${source}" >&2; exit 2; }

    stage="$(mktemp -d)"
    remote_staged=""
    trap cleanup EXIT
    stage_project "${source}" "${stage}"

    remote_staged="${remote}"
    # shellcheck disable=SC2029  # remote is validated above and expanded locally on purpose
    ssh "${NAS}" "umask 077 && mkdir -p '${remote}'"
    rsync -a --delete --chmod=go-rwx "${stage}/" "${NAS}:${remote}/"
    # shellcheck disable=SC2029
    ssh "${NAS}" "
        set -e
        cd '${remote}'
        sudo /usr/local/bin/docker stop doco-cd
        status=0
        sudo /usr/local/bin/docker-compose up -d || status=\$?
        sudo /usr/local/bin/docker-compose ps -a || true
        exit \"\${status}\"
    "
}

# Remove the local staging copy and, once anything reached Athena, the
# decrypted files there. Compose has read them by then; later Compose commands
# in that directory need a fresh run of this script.
cleanup() {
    rm -rf -- "${stage}"
    if [[ -n "${remote_staged}" ]]; then
        # shellcheck disable=SC2029
        ssh "${NAS}" "find '${remote_staged}' -type f -name '*.sops.env' -exec rm -f {} +" ||
            echo "WARNING: decrypted *.sops.env files may remain in ${NAS}:${remote_staged}; remove them by hand" >&2
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
