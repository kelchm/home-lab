#!/usr/bin/env bash
# Install or update the GLKVM's ACME client from a workstation. Installs pinned
# Lego, its configuration, the renewal and deploy scripts, and the boot script
# that restores the cron entry. Leaves the Cloudflare token, Lego's account and
# certificate state, and the served certificate unchanged; requesting a
# certificate is a separate step (see devices/glkvm/README.md#tls).
set -euo pipefail

if (( $# > 1 )); then
    echo "usage: $0 [SSH_TARGET]" >&2
    exit 2
fi

target=${1:-root@10.32.20.10}
src=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    'https://github.com/go-acme/lego/releases/download/v5.6.0/lego_v5.6.0_linux_arm64.tar.gz' \
    --output "$stage/lego.tar.gz"
echo "34d87a6552d69390c4a4560237045c47713a8e148d899e16db0e97040894ed1f  $stage/lego.tar.gz" |
    shasum -a 256 -c --quiet
tar -xzf "$stage/lego.tar.gz" -C "$stage" lego
cp "$src/lego.yml" "$src/renew.sh" "$src/deploy.sh" "$src/S50acme-renewal" "$stage/"

COPYFILE_DISABLE=1 tar -C "$stage" -cf - lego lego.yml renew.sh deploy.sh S50acme-renewal |
    ssh -o BatchMode=yes "$target" '
        set -eu
        umask 077
        stage=$(mktemp -d /tmp/glkvm-acme.XXXXXX)
        trap '\''rm -rf "$stage"'\'' EXIT
        tar -xf - -C "$stage"

        acme=/etc/kvmd/user/acme
        mkdir -p "$acme/bin" /etc/kvmd/user/scripts /etc/kvmd/user/backups
        chmod 0700 "$acme" "$acme/bin"

        # Keep the vendor self-signed pair once, before anything replaces it.
        if [ ! -e /etc/kvmd/user/backups/ssl-vendor ]; then
            cp -a /etc/kvmd/user/ssl /etc/kvmd/user/backups/ssl-vendor
        fi

        install_file() { cp "$stage/$1" "$2.new" && chmod "$3" "$2.new" && mv -f "$2.new" "$2"; }
        install_file lego "$acme/bin/lego" 0700
        install_file lego.yml "$acme/lego.yml" 0600
        install_file renew.sh "$acme/renew.sh" 0700
        install_file deploy.sh "$acme/deploy.sh" 0700
        install_file S50acme-renewal /etc/kvmd/user/scripts/S50acme-renewal 0755

        /etc/kvmd/user/scripts/S50acme-renewal start
        "$acme/bin/lego" --version
        grep -F "$acme/renew.sh" /etc/crontabs/root
        if [ -f "$acme/cloudflare-token" ]; then
            echo "Cloudflare token present"
        else
            echo "Cloudflare token missing: install it before the first renewal" >&2
        fi
    '
