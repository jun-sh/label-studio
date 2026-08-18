#!/usr/bin/env bash
# Verify ego capture host (130) uses segment H264 / tar.zst production path.
# Usage: ego-130-verify-h264.sh [ssh_target]
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"

echo "==> H264 drop-ins on ${TARGET}"
ssh "${TARGET}" bash -s <<'REMOTE'
set -euo pipefail
fail=0
warn() { echo "WARN: $*"; }
die() { echo "FAIL: $*"; fail=1; }

for d in "$HOME/.config/systemd/user/ecs-record-oak-stream.service.d" \
           /etc/systemd/system/ecs-record-oak-stream.service.d; do
  [[ -d "$d" ]] || continue
  echo "--- $d ---"
  ls -la "$d" || true
  for bad in z-production-egoverse.conf scheme-a.conf; do
    if [[ -f "$d/$bad" ]]; then
      die "$d/$bad present (overrides SEGMENT_H264=0 / SEGMENT_FRAME_BIN=1)"
    fi
  done
  if [[ ! -f "$d/v0.0.8-segment-mp4.conf" ]]; then
    die "$d/v0.0.8-segment-mp4.conf missing"
  fi
done

echo "--- effective Environment (user unit) ---"
systemctl --user show ecs-record-oak-stream -p Environment --no-pager 2>/dev/null \
  | tr ' ' '\n' | grep -E '^(SEGMENT_H264|SEGMENT_FRAME_BIN|OAK_H264|UPLOAD_PROTOCOL)=' || true

seg_h264="$(systemctl --user show ecs-record-oak-stream -p Environment --no-pager 2>/dev/null | tr ' ' '\n' | grep '^SEGMENT_H264=' | cut -d= -f2 || true)"
legacy_append="$(systemctl --user show ecs-record-oak-stream -p Environment --no-pager 2>/dev/null | tr ' ' '\n' | grep '^SEGMENT_H264_LEGACY_APPEND=' | cut -d= -f2 || true)"
frame_bin="$(systemctl --user show ecs-record-oak-stream -p Environment --no-pager 2>/dev/null | tr ' ' '\n' | grep '^SEGMENT_FRAME_BIN=' | cut -d= -f2 || true)"
[[ "${seg_h264}" == "1" ]] || die "SEGMENT_H264=${seg_h264:-unset} (expected 1)"
[[ "${legacy_append}" == "1" ]] || die "SEGMENT_H264_LEGACY_APPEND=${legacy_append:-unset} (expected 1)"
[[ "${frame_bin}" == "0" ]] || die "SEGMENT_FRAME_BIN=${frame_bin:-unset} (expected 0)"

if ! command -v ffmpeg >/dev/null; then
  die "ffmpeg not installed"
fi

echo "--- last capture log (h264 markers) ---"
journalctl --user -u ecs-record-oak-stream --since '24 hours ago' --no-pager 2>/dev/null \
  | grep -E 'segment_h264_preflight|hw_h264=True|storage_h264=1' | tail -3 || warn "no recent capture log"

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "OK: segment H264 production path verified"
REMOTE
