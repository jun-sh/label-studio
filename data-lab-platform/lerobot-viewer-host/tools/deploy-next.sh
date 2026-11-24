#!/usr/bin/env bash
# Build the host and swap it into the lerobot container's /srv/lerobot/next (no restart needed).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
C="${LEROBOT_CONTAINER:-data-lab-lerobot-1}"

cd "$HERE"
rm -rf dist
npx vite build --logLevel error
docker exec "$C" rm -rf /srv/lerobot/next.tmp
docker cp dist "$C:/srv/lerobot/next.tmp"
docker exec "$C" sh -c 'rm -rf /srv/lerobot/next && mv /srv/lerobot/next.tmp /srv/lerobot/next'
echo "deployed: $(grep -o 'assets/index-[^"]*\.js' dist/index.html)"
