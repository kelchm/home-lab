#!/bin/sh
# Converge to the default single-node profile after boot. TP=2 profiles never
# auto-start: unattended dual-rank bring-up is the documented UMA wedge path.
set -eu
if [ -n "$(docker ps -q --filter name=dspark-guide)" ] \
  || [ -n "$(docker ps -q --filter name=glm53-exl3-)" ] \
  || [ -n "$(docker ps -q --filter name=vllm-qwen)" ]; then
  exit 0
fi
cd /opt/spark-stack/inference
# Never pull unattended: the pinned image was present before the reboot.
docker compose up -d --pull never
# Re-point the stable endpoint at the default profile; a route published for a
# TP=2 profile before the reboot would otherwise target a dead port. Caddy may
# still be starting under docker at this point.
if [ -f /opt/spark-stack/caddy/Caddyfile.boot ]; then
  cp /opt/spark-stack/caddy/Caddyfile.boot /opt/spark-stack/caddy/Caddyfile
  for _ in $(seq 1 12); do
    if docker exec spark-caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null; then
      exit 0
    fi
    sleep 5
  done
  echo "caddy reload did not succeed; route follows the boot file on next caddy start" >&2
fi
