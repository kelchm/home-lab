#!/bin/sh
# Converge to the default single-node profile after boot. TP=2 profiles never
# auto-start: unattended dual-rank bring-up is the documented UMA wedge path.
set -eu

# Same lock as the switch/publish/down transactions: at real boot /tmp is
# fresh, so this only yields when a transaction is genuinely mid-flight
# (the controller can be reachable before this oneshot finishes).
lock='{{ lock_dir }}'
if ! mkdir "$lock" 2>/dev/null; then
  echo "transaction lock held; boot convergence yields" >&2
  exit 0
fi
trap 'rmdir "$lock"' EXIT

# A TP=2 remnant means an operator intervened or is mid-recovery; touch
# nothing, including the route.
if [ -n "$(docker ps -q --filter name=dspark-guide)" ] \
  || [ -n "$(docker ps -q --filter name=glm53-exl3-)" ]; then
  exit 0
fi

# Docker's own restart policy usually brings qwen back before this unit runs;
# only start it when it did not.
if [ -z "$(docker ps -q --filter name=vllm-qwen)" ]; then
  cd '{{ remote_dir }}/inference'
  # Never pull unattended: the pinned image was present before the reboot.
  docker compose up -d --pull never
fi

# Converge the stable route in every case: a route published for a TP=2
# profile before the reboot would otherwise keep targeting a dead port.
if [ -f '{{ caddy_dir }}/Caddyfile.boot' ]; then
  cp '{{ caddy_dir }}/Caddyfile.boot' '{{ caddy_dir }}/Caddyfile'
  reloaded=0
  for _ in $(seq 1 12); do
    if docker exec spark-caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null; then
      reloaded=1
      break
    fi
    sleep 5
  done
  if [ "$reloaded" -eq 0 ]; then
    # Caddy already loaded the stale file at container start; a restart is the
    # reliable way to pick up the boot route this early in boot.
    docker restart spark-caddy >/dev/null 2>&1 || true
  fi
fi

# Residency converges with the workload; status and the controller would
# otherwise keep reporting the pre-reboot profile.
printf '%s\n' '{{ default_profile }}' > '{{ remote_dir }}/resident'
printf '%s boot-converge %s\n' "$(date -Is)" '{{ default_profile }}' >> '{{ remote_dir }}/switch.log'
