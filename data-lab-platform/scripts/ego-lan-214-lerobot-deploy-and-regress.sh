#!/usr/bin/env bash
# Wait for bookworm+lerobot image build, recreate containers, verify, reset, regression.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE="docker-compose -f ${ROOT}/docker-compose.yml -f ${ROOT}/data-lab-platform/docker-compose.platform.yml"
LOG="${ROOT}/data-lab-platform/scripts/.lerobot-deploy.log"
BUILD_LOG="/tmp/lerobot-build.log"
STATION="${STATION_ID:-ego-lan-214}"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"

exec > >(tee -a "$LOG") 2>&1
echo "=== ego-lan-214 LeRobot deploy $(date -Is) ==="

echo "[1/6] waiting for image build..."
for i in $(seq 1 180); do
  if grep -q 'Successfully tagged.*data-lab-lerobot-studio' "$BUILD_LOG" 2>/dev/null; then
    echo "build ok"
    break
  fi
  if ! pgrep -f 'docker-compose.*build' >/dev/null 2>&1; then
    if ! grep -q 'Successfully tagged' "$BUILD_LOG" 2>/dev/null; then
      echo "build failed or stopped"; tail -30 "$BUILD_LOG"; exit 1
    fi
    break
  fi
  sleep 10
done

echo "[2/6] force-recreate stream-ingest + derive-worker..."
cd "$ROOT"
$COMPOSE up -d --force-recreate stream-ingest derive-worker
sleep 8

echo "[3/6] verify env + lerobot import..."
for v in DERIVE_PARQUET_BACKEND DERIVE_VIDEO_EXPORT_BACKEND; do
  val="$(docker exec "$INGEST" printenv "$v")"
  if [[ "$val" != "lerobot" ]]; then
    echo "FAIL $v=$val"; exit 1
  fi
  echo "OK $v=lerobot"
done
docker exec "$INGEST" python3 -c "from lerobot.datasets.lerobot_dataset import LeRobotDataset; print('lerobot import ok')"
os="$(docker exec "$INGEST" sh -c 'grep ^NAME= /etc/os-release | cut -d= -f2')"
echo "container OS: $os"
if echo "$os" | grep -qi alpine; then
  echo "FAIL still on Alpine image"; exit 1
fi

echo "[4/6] reset station stream data..."
EGO_RESET_YES=1 bash "${ROOT}/data-lab-platform/scripts/ego-lan-214-reset-for-rerun.sh" --yes

echo "[5/6] derive-status (expect IDLE until new uploads)..."
curl -sf "http://127.0.0.1:8080/lerobot/api/collection/stations/${STATION}/derive-status" | python3 -m json.tool | head -20 || true

RAW_N="$(find "${ROOT}/data-storage/stream/${STATION}/raw/segments" -name '*.tar.zst' 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${RAW_N}" -ge 2 ]]; then
  echo "[5b] found ${RAW_N} raw segments — triggering derive..."
  bash "${ROOT}/data-lab-platform/scripts/ego-derive" run --station "$STATION" || true
  sleep 5
  echo "[6/6] multi-session regression..."
  EXPECTED_EPISODES=2 bash "${ROOT}/data-lab-platform/scripts/ego-lan-214-multi-session-regression.sh"
else
  echo "[6/6] SKIP regression — need 2+ sessions recorded & uploaded (raw tar.zst=${RAW_N})"
  echo "After 2 recordings on 214: upload → ego-derive run → re-run:"
  echo "  bash data-lab-platform/scripts/ego-lan-214-multi-session-regression.sh"
fi

echo "=== deploy pipeline done $(date -Is) ==="
