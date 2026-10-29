#!/bin/sh
# Credential-free helper. Install root:root 0755; supply the setup key on stdin.
set -eu
if [ "$#" -ne 0 ]; then
    printf '%s\n' 'This helper accepts stdin only, with no arguments.' >&2
    exit 64
fi
# Bound stdin delivery and enrollment together; GNU timeout signals the process group.
# The inner shell expands these variables, never the calling shell.
# shellcheck disable=SC2016
exec timeout --signal=TERM --kill-after=5s 90s /bin/sh -eu -c '
    umask 077
    key_file=
    cleanup() {
        if [ -n "$key_file" ]; then
            rm -f -- "$key_file"
        fi
    }
    trap cleanup EXIT
    trap "exit 129" HUP
    trap "exit 130" INT
    trap "exit 143" TERM
    key_file=$(mktemp /run/netbird-router-enroll.XXXXXX)
    chmod 0600 "$key_file"
    cat > "$key_file"
    if [ ! -s "$key_file" ]; then
        printf "%s\n" "Empty enrollment input." >&2
        exit 65
    fi
    netbird up --setup-key-file "$key_file" --disable-client-routes --disable-dns
'
