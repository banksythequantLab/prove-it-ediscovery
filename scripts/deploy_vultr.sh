#!/usr/bin/env bash
# Run ON a fresh Vultr Cloud Compute VM (Ubuntu 24.04) as root. Not executed by the author yet.
#   export VULTR_INFERENCE_API_KEY=...  DOMAIN=proveit.example.com (or leave unset for plain :80)
set -euo pipefail
REPO=${REPO:-https://github.com/YOUR_GITHUB/prove-it.git}
apt-get update && apt-get install -y docker.io docker-compose-v2 git ufw
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable
mkdir -p /var/lib/prove-it/sbx
[ -d /opt/prove-it ] || git clone "$REPO" /opt/prove-it
cd /opt/prove-it && git pull
cat > .env <<ENV
VULTR_INFERENCE_API_KEY=${VULTR_INFERENCE_API_KEY:?set VULTR_INFERENCE_API_KEY}
VULTR_MODEL=${VULTR_MODEL:-glm-5.3-flash}
DOMAIN=${DOMAIN:-:80}
ENV
chmod 600 .env
docker compose build && docker compose up -d app caddy
echo "Up. Check: curl -s http://localhost/api/status (via caddy)"
