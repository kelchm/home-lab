#!/bin/sh
# Converge to the default single-node profile after boot. TP=2 profiles never
# auto-start: unattended dual-rank bring-up is the documented UMA wedge path.
set -eu
if [ -n "$(docker ps -q --filter name=dspark-guide)" ] || [ -n "$(docker ps -q --filter name=vllm-qwen)" ]; then
  exit 0
fi
cd /opt/spark-stack/inference
# Never pull unattended: the pinned image was present before the reboot.
exec docker compose up -d --pull never
