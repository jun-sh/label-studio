#!/usr/bin/env bash
# Verify 130 MCAP pilot unit config (does not touch ecs-record-oak-stream).
set -euo pipefail

die() { echo "ego-130-verify-mcap-pilot: $*" >&2; exit 1; }

UNIT="ecs-record-oak-mcap-pilot.service"
DROPIN="${HOME}/.config/systemd/user/${UNIT}.d/z-mcap-pilot.conf"

[[ -f "${DROPIN}" ]] || die "missing ${DROPIN} (provision pilot drop-in first)"

grep -q '^SEGMENT_MCAP=1' "${DROPIN}" || die "SEGMENT_MCAP not enabled in pilot drop-in"
grep -q '^OAK_H264=0' "${DROPIN}" || die "OAK_H264 must be 0 for Track1 pilot"
grep -q '^SEGMENT_H264=0' "${DROPIN}" || die "SEGMENT_H264 must be 0 for Track1 pilot"
grep -q 'ego-mcap-pilot' "${DROPIN}" || die "EGO_STATION_ID must be ego-mcap-pilot"
grep -q '^Environment=EGO_SEGMENT_ROOT=/home/server/cache/ego-mcap-pilot/segments' "${DROPIN}" \
  || die "EGO_SEGMENT_ROOT must point to ego-mcap-pilot cache"
grep -q ':7863/' "${DROPIN}" || die "EGO_UPLOAD_URL must use pilot ingest :7863"
! grep -q 'EnvironmentFile=.*ego-station.env' "${HOME}/.config/systemd/user/${UNIT}" 2>/dev/null \
  && ! grep -q 'EnvironmentFile=.*ego-station.env' "/etc/systemd/system/${UNIT}" 2>/dev/null \
  || die "pilot unit must not load ~/.config/ego-station.env"

if systemctl --user is-active "${UNIT}" >/dev/null 2>&1; then
  env_line="$(systemctl --user show "${UNIT}" -p Environment --value)"
  echo "${env_line}" | tr ' ' '\n' | grep -E '^(SEGMENT_MCAP|OAK_H264|EGO_STATION_ID)=' | sort
fi

echo "OK: mcap pilot unit configuration verified"
