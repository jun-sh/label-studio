#!/usr/bin/env bash
# Bind-mounted ./mydata is often root-owned on the host; fix ownership before dropping privileges.
set -e
DATA_DIR="${LABEL_STUDIO_BASE_DATA_DIR:-/label-studio/data}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R 1001:0 "$DATA_DIR"
  case "${1:-}" in
    nginx)
      exec /label-studio/deploy/docker-entrypoint.sh "$@"
      ;;
    *)
      exec gosu 1001 /label-studio/deploy/docker-entrypoint.sh "$@"
      ;;
  esac
fi
exec /label-studio/deploy/docker-entrypoint.sh "$@"
