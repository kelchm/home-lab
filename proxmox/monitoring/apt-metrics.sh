#!/bin/sh
# Writes this host's pending apt upgrades as node-exporter textfile metrics every 15 minutes. It reads the host's
# package state, mounted read-only, and never refreshes the package lists: the host's own daily job does that.
# Kept identical in proxmox/monitoring and sparks/monitoring.
set -u
while :; do
  if sim=$(apt-get -s -o Debug::NoLocking=1 -o Dir::Cache::pkgcache= -o Dir::Cache::srcpkgcache= dist-upgrade 2>/dev/null) &&
    refreshed=$(stat -c %Y /var/lib/apt/lists/partial); then
    inst=$(printf '%s\n' "$sim" | grep '^Inst ')
    all=$(printf '%s' "$inst" | grep -c '^Inst ')
    # The candidate's archives are in the parentheses: "Inst pkg [old] (new Debian:13.1/stable-security [amd64])".
    security=$(printf '%s' "$inst" | grep -c '^Inst [^(]*([^)]*-security')
    cat > /textfile/apt.prom.tmp <<METRICS
# HELP apt_upgrades_pending Packages with an upgrade available.
# TYPE apt_upgrades_pending gauge
apt_upgrades_pending $all
# HELP apt_security_upgrades_pending Packages with an upgrade available from a security archive.
# TYPE apt_security_upgrades_pending gauge
apt_security_upgrades_pending $security
# HELP apt_package_lists_refreshed_timestamp_seconds When the host last refreshed its package lists.
# TYPE apt_package_lists_refreshed_timestamp_seconds gauge
apt_package_lists_refreshed_timestamp_seconds $refreshed
METRICS
    mv /textfile/apt.prom.tmp /textfile/apt.prom
  else
    echo "apt-metrics: apt-get simulation or reading the lists' refresh time failed" >&2
  fi
  sleep "${APT_METRICS_INTERVAL:-900}"
done
