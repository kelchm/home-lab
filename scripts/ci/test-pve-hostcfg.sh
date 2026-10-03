#!/usr/bin/env bash
# Real Git + local Ansible integration; no root, packages, services, or network changes.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
export ANSIBLE_PLAYBOOK=${ANSIBLE_PLAYBOOK:-$(command -v ansible-playbook)}
python3 "$root/proxmox/host-config/tests/controller.py"
