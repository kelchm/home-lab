#!/bin/sh
# Lego deploy hook: install the issued certificate where the firmware's nginx
# reads it and reload nginx. Restores the previous pair if nginx rejects the new
# one. Installed as /etc/kvmd/user/acme/deploy.sh; safe to rerun by hand.
set -eu
umask 077
name=kvm-compute-rack.home.kelch.io
src=/etc/kvmd/user/acme/lego-state/certificates
ssl=/etc/kvmd/user/ssl
crt=$src/$name.crt
key=$src/$name.key

fingerprint() { openssl sha256 | cut -d' ' -f2; }
[ "$(openssl x509 -in "$crt" -noout -pubkey | fingerprint)" = \
    "$(openssl pkey -in "$key" -pubout | fingerprint)" ]
openssl x509 -in "$crt" -noout -checkhost "$name" | grep -q 'does match'
openssl x509 -in "$crt" -noout -checkend 86400 >/dev/null

cp -p "$ssl/server.crt" "$ssl/server.crt.prev"
cp -p "$ssl/server.key" "$ssl/server.key.prev"
cp "$crt" "$ssl/server.crt.new"
cp "$key" "$ssl/server.key.new"
chmod 0644 "$ssl/server.crt.new"
chmod 0600 "$ssl/server.key.new"
mv -f "$ssl/server.crt.new" "$ssl/server.crt"
mv -f "$ssl/server.key.new" "$ssl/server.key"

if ! /usr/sbin/nginx -t -p /etc/kvmd/nginx -c /etc/kvmd/nginx-kvmd.conf \
    -g "pid /run/kvmd/nginx-test.pid; user root; error_log stderr;"; then
    mv -f "$ssl/server.crt.prev" "$ssl/server.crt"
    mv -f "$ssl/server.key.prev" "$ssl/server.key"
    echo "nginx rejected the new certificate; previous pair restored" >&2
    exit 1
fi
sync
kill -HUP "$(cat /run/kvmd/nginx.pid)"
echo "Installed $(openssl x509 -in "$ssl/server.crt" -noout -enddate)"
