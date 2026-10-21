#!/usr/bin/env bash
set -euo pipefail

if (( $# > 1 )); then
    echo "usage: $0 [SSH_TARGET]" >&2
    exit 2
fi

target=${1:-root@10.32.20.10}
assets_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

tar -C "$assets_dir" -cf - index.html style.css app.js nginx.ctx-server.conf |
    ssh -o BatchMode=yes "$target" '
        set -eu
        cmp /rom/etc/kvmd/nginx-kvmd.conf /etc/kvmd/nginx-kvmd.conf
        stage=$(mktemp -d /tmp/glkvm-hosts.XXXXXX)
        trap '\''rm -rf "$stage"'\'' EXIT
        tar -xf - -C "$stage"

        assets=/etc/kvmd/user/hosts-prototype
        extra=/usr/share/kvmd/extras/hosts-prototype
        mkdir -p /etc/kvmd/user/backups
        backup=$(mktemp -d /etc/kvmd/user/backups/hosts-prototype.XXXXXX)
        if [ -d "$assets" ]; then
            cp -a "$assets" "$backup/assets"
        fi
        if [ -f "$extra/nginx.ctx-server.conf" ]; then
            cp -p "$extra/nginx.ctx-server.conf" "$backup/nginx.ctx-server.conf"
        fi

        restore_needed=1
        cleanup() {
            result=$?
            trap - EXIT
            if [ "$restore_needed" = 1 ]; then
                rm -f "$assets/index.html" "$assets/style.css" "$assets/app.js"
                if [ -d "$backup/assets" ]; then
                    cp -a "$backup/assets/." "$assets/"
                fi
                if [ -f "$backup/nginx.ctx-server.conf" ]; then
                    cp -p "$backup/nginx.ctx-server.conf" "$extra/nginx.ctx-server.conf"
                else
                    rm -f "$extra/nginx.ctx-server.conf"
                fi
                echo "Installation failed; previous prototype files restored" >&2
            fi
            rm -rf "$stage"
            exit "$result"
        }
        trap cleanup EXIT

        mkdir -p "$assets" "$extra"
        cp "$stage/index.html" "$stage/style.css" "$stage/app.js" "$assets/"
        cp "$stage/nginx.ctx-server.conf" "$extra/nginx.ctx-server.conf"

        nginx -t -p /etc/kvmd/nginx -c /etc/kvmd/nginx-kvmd.conf \
            -g "pid /run/kvmd/nginx-test.pid; user root; error_log stderr;"

        kill -HUP "$(cat /run/kvmd/nginx.pid)"
        restore_needed=0
        printf "Prototype available at /hosts/; backup: %s\n" "$backup"
    '
