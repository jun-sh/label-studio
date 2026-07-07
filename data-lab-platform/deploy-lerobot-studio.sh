#!/usr/bin/env bash
# Deploy IO-AI LeRobot Studio overlay (fast image build; assets download on first start).
set -euo pipefail
cd "$(dirname "$0")/.."
export LABEL_STUDIO_HOST="${LABEL_STUDIO_HOST:-http://10.10.10.34:8080}"

echo "1/3 Building lerobot image (seconds, no asset download)..."
docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml build lerobot

echo "2/4 Starting lerobot + nginx..."
docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml up -d lerobot nginx

echo "3/4 Wait for lerobot embed route..."
for _ in $(seq 1 24); do
  if curl -fsS --max-time 5 "${LABEL_STUDIO_HOST}/lerobot/?datalab_embed=1" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

echo "4/4 Verify /data page (embed manifest + bundled datasets)..."
bash data-lab-platform/verify-data-page.sh || exit 1

echo "    Open: ${LABEL_STUDIO_HOST}/data"
echo "    Tail lerobot logs (Ctrl+C to stop tail)..."
docker logs -f data-lab-lerobot-1
