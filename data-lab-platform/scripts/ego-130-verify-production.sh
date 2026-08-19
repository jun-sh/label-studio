#!/usr/bin/env bash
# Verify 130 ego-standard JPEG production capture path.
# Usage: ego-130-verify-production.sh [ssh_target]
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"

echo "==> JPEG capture path on ${TARGET}"
ssh "${TARGET}" bash -s <<'REMOTE'
set -euo pipefail
fail=0
die() { echo "FAIL: $*"; fail=1; }
env="$(systemctl --user show ecs-record-oak-stream -p Environment --no-pager 2>/dev/null || true)"
get_env() { echo "$env" | tr ' ' '\n' | grep "^$1=" | cut -d= -f2- || true; }
frame_bin="$(get_env SEGMENT_FRAME_BIN)"
hw_jpeg="$(get_env OAK_HW_JPEG)"
oak_h264="$(get_env OAK_H264)"
[[ "$frame_bin" == "1" ]] || die "SEGMENT_FRAME_BIN=${frame_bin:-unset} (want 1)"
[[ "$hw_jpeg" == "1" ]] || die "OAK_HW_JPEG=${hw_jpeg:-unset} (want 1)"
[[ "$oak_h264" == "0" ]] || die "OAK_H264=${oak_h264:-unset} (want 0)"
grep -q '^OAK_H264=0' "$HOME/.config/ego-station.env.d/station.conf" 2>/dev/null \
  || die "station.conf missing OAK_H264=0"
for d in "$HOME/.config/systemd/user/ecs-record-oak-stream.service.d" \
           /etc/systemd/system/ecs-record-oak-stream.service.d; do
  [[ -d "$d" ]] || continue
  [[ -f "$d/z-production-egoverse.conf" ]] || die "$d/z-production-egoverse.conf missing"
  for bad in v0.0.8-segment-mp4.conf v0.0.9-h264-plan-b.conf phase2-h264-poc.conf; do
    [[ -f "$d/$bad" ]] && die "$d/$bad present (legacy H264 drop-in)"
  done
done
command -v ffmpeg >/dev/null || die "ffmpeg missing"
[[ "$fail" -eq 0 ]] && echo "OK: JPEG-only capture path verified"
exit "$fail"
REMOTE
