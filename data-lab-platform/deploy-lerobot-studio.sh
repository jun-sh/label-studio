#!/usr/bin/env bash
# Deploy IO-AI LeRobot Studio overlay (fast image build; assets download on first start).
set -euo pipefail
cd "$(dirname "$0")/.."
export LABEL_STUDIO_HOST="${LABEL_STUDIO_HOST:-http://10.10.10.34:8080}"

echo "1/3 Building lerobot image (seconds, no asset download)..."
docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml build lerobot

echo "2/3 Starting lerobot + nginx..."
docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml up -d lerobot nginx

echo "3/3 Tail lerobot logs until assets are ready (Ctrl+C to stop tail)..."
echo "    Then open: ${LABEL_STUDIO_HOST}/lerobot/?url=sample%3A%2F%2Fsensexperience_ego"
docker logs -f data-lab-lerobot-1
