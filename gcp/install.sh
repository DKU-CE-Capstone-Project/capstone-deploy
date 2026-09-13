#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Run on the existing VM as econmind. No GitHub token or deployment SSH key needed.
if [[ "$(id -un)" != econmind ]]; then
  echo 'Run this installer as econmind on the deployment VM.' >&2
  exit 1
fi
sudo -n docker compose -f /home/econmind/econmind-gcp-deploy/compose.yaml config -q
sudo -n systemctl stop econmind-deploy.timer 2>/dev/null || true
sudo -n systemctl stop econmind-deploy.service 2>/dev/null || true
mkdir -p /home/econmind/econmind-cd/controller
install -m 644 deploy.py /home/econmind/econmind-cd/controller/deploy.py
sudo -n install -m 644 econmind-deploy.service econmind-deploy.timer /etc/systemd/system/
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now econmind-deploy.timer
sudo -n systemctl start --no-block econmind-deploy.service
