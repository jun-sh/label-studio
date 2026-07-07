#!/usr/bin/env bash
# End-to-end checks for http://<host>/data embed (LeRobot manifest + real datasets).
# Does not inspect minified JS; validates HTTP outcomes only.
#
# Usage:
#   bash data-lab-platform/verify-data-page.sh
#   DATA_LAB_HOST=http://10.10.10.34:8080 bash data-lab-platform/verify-data-page.sh
set -euo pipefail

HOST="${DATA_LAB_HOST:-${LABEL_STUDIO_HOST:-http://10.10.10.34:8080}}"
HOST="${HOST%/}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

ok() {
  echo "OK: $*"
}

echo "==> verify /data embed @ ${HOST}"

EMBED_HTML="$(curl -fsS --max-time 15 "${HOST}/lerobot/?datalab_embed=1&lang=zh")" ||
  fail "embed page unreachable: ${HOST}/lerobot/?datalab_embed=1"

NATIVE_HTML="$(curl -fsS --max-time 15 "${HOST}/lerobot/?lang=zh")" ||
  fail "native lerobot page unreachable: ${HOST}/lerobot/"

echo "$EMBED_HTML" | grep -q 'manifest-embed-fix.js' ||
  fail "embed page missing manifest-embed-fix.js (datalab_embed=1 inject broken)"

if echo "$NATIVE_HTML" | grep -q 'manifest-embed-fix.js'; then
  fail "native /lerobot/ must not load manifest-embed-fix.js"
fi
ok "inject scope: embed only"

MANIFEST_JSON="$(curl -fsS --max-time 15 "${HOST}/lerobot/sample-datasets.manifest.json")" ||
  fail "manifest unreachable: ${HOST}/lerobot/sample-datasets.manifest.json"

python3 - "$MANIFEST_JSON" "$HOST" <<'PY'
import json
import sys
import urllib.error
import urllib.request

manifest_raw, host = sys.argv[1], sys.argv[2]
try:
    manifest = json.loads(manifest_raw)
except json.JSONDecodeError as e:
    sys.exit(f"manifest is not valid JSON: {e}")

if manifest.get("schemaVersion") != 1:
    sys.exit(f"manifest schemaVersion must be 1, got {manifest.get('schemaVersion')!r}")

datasets = manifest.get("datasets")
if not isinstance(datasets, list) or not datasets:
    sys.exit("manifest datasets must be a non-empty array")

ids = [d.get("id") for d in datasets if isinstance(d, dict)]
if "sensexperience_ego" not in ids:
    sys.exit(f"sensexperience_ego missing from manifest ids: {ids}")

# Upstream placeholder cards (Mi[]) — must never appear as manifest entries.
placeholder_ids = {f"sample-{i}" for i in range(1, 9)}
placeholder_titles = {
    "机器人抓取任务数据集",
    "移动机器人导航数据集",
    "双臂协作操作数据集",
    "视觉定位与建图数据集",
    "LeRobot v3 当前数据集集",
}

for d in datasets:
    did = d.get("id") or ""
    title = d.get("title") or d.get("name") or ""
    if did in placeholder_ids:
        sys.exit(f"placeholder dataset id in manifest: {did}")
    if title in placeholder_titles:
        sys.exit(f"placeholder dataset title in manifest: {title!r}")

required = [
    "sensexperience_ego",
    "sensexperience_umi",
    "dualairbot_fold",
    "dualpiper_pulling",
]
missing = [rid for rid in required if rid not in ids]
if missing:
    sys.exit(f"required bundled datasets missing from manifest: {missing}")

for rid in required:
    entry = next(d for d in datasets if d.get("id") == rid)
    archive = (entry.get("archiveUrl") or "").strip()
    if not archive.startswith("/lerobot/bundled/"):
        sys.exit(f"{rid}: archiveUrl must be under /lerobot/bundled/, got {archive!r}")
    url = host + archive
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status >= 400:
                sys.exit(f"{rid}: bundled archive HEAD {resp.status} for {url}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{rid}: bundled archive HEAD HTTP {e.code} for {url}")

print(f"manifest datasets ({len(datasets)}): {', '.join(ids)}")
PY

ok "manifest lists real bundled datasets (sensexperience_ego + peers), no placeholder cards"

echo "==> verify-data-page passed"
