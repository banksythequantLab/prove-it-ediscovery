#!/usr/bin/env bash
# Run ON a Vultr Cloud Compute VM (Ubuntu 24.04) as root.
#   export VULTR_INFERENCE_API_KEY=...  DOMAIN=<ip-with-dashes>.sslip.io   (or leave DOMAIN unset for plain :80)
# Demo set: build it anywhere that has the Enron maildir, then copy it over:
#   python scripts/build_demo.py ./maildir ./demo_out && scp demo_out/* root@VM:/var/lib/prove-it/demo/
set -euo pipefail
REPO=${REPO:-https://github.com/banksythequantLab/prove-it-ediscovery.git}
apt-get update && apt-get install -y docker.io docker-compose-v2 git ufw
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
mkdir -p /var/lib/prove-it/{evidence,jobs,db,uploads,demo}
[ -d /opt/prove-it ] || git clone "$REPO" /opt/prove-it
cd /opt/prove-it && git pull --ff-only || true
if [ ! -f .env ]; then
  umask 077
  cat > .env <<ENV
VULTR_INFERENCE_API_KEY=${VULTR_INFERENCE_API_KEY:?set VULTR_INFERENCE_API_KEY}
VULTR_MODEL=${VULTR_MODEL:-glm-5.3-flash}
DOMAIN=${DOMAIN:-:80}
ENV
fi
chown root:root .env && chmod 600 .env
docker compose build && docker compose up -d app runner caddy
sleep 3; docker compose ps
echo "Smoke test: python3 scripts/smoke.py https://\$DOMAIN"
