#!/usr/bin/env bash
# RC-3: healthz must stay responsive for 60s after stream-ingest restart.
set -euo pipefail
CID="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
echo "restart ${CID}..."
docker restart "${CID}" >/dev/null
sleep 2
ok=0
fail=0
for i in $(seq 1 60); do
  if docker exec "${CID}" node -e "
import http from 'node:http';
http.get('http://127.0.0.1:7862/healthz', r => {
  let d=''; r.on('data', c => d += c);
  r.on('end', () => process.exit(r.statusCode===200 && d.trim()==='ok' ? 0 : 1));
}).on('error', () => process.exit(1));
" 2>/dev/null; then
    ok=$((ok + 1))
  else
    fail=$((fail + 1))
    echo "t+${i}s FAIL"
  fi
  sleep 1
done
echo "healthz smoke: ok=${ok} fail=${fail}"
test "${ok}" -ge 58
