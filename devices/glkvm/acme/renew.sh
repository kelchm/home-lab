#!/bin/sh
# Daily ACME run on the GLKVM. Lego renews only when the certificate is due and
# then runs deploy.sh. Installed as /etc/kvmd/user/acme/renew.sh.
set -eu
umask 077
dir=/etc/kvmd/user/acme
log=$dir/renewal.log

# This firmware starts two crond instances that read the same crontab, so every
# entry fires twice. Let only one copy run.
exec 9>"$dir/renew.lock"
flock -n 9 || exit 0

unset CF_API_KEY CLOUDFLARE_API_KEY CF_DNS_API_TOKEN CLOUDFLARE_DNS_API_TOKEN \
    CF_ZONE_API_TOKEN CLOUDFLARE_ZONE_API_TOKEN
export CF_DNS_API_TOKEN_FILE="$dir/cloudflare-token"
if [ ! -s "$CF_DNS_API_TOKEN_FILE" ]; then
    echo "== $(date '+%Y-%m-%dT%H:%M:%S%z') no Cloudflare token; skipped" >>"$log"
    exit 1
fi

if [ -f "$log" ] && [ "$(wc -l <"$log")" -gt 5000 ]; then
    tail -n 2000 "$log" >"$log.tmp" && mv "$log.tmp" "$log"
fi

status=0
{
    echo "== $(date '+%Y-%m-%dT%H:%M:%S%z')"
    "$dir/bin/lego" --config "$dir/lego.yml" || status=$?
    echo "== exit $status"
} >>"$log" 2>&1
exit "$status"
