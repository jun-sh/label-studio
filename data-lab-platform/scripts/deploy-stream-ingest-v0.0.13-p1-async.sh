#!/usr/bin/env bash
# DEPRECATED — use deploy-stream-ingest-v0.1.1-async.sh
echo "[deprecated] deploy-stream-ingest-v0.0.13-p1-async.sh → deploy-stream-ingest-v0.1.1-async.sh" >&2
exec bash "$(dirname "$0")/deploy-stream-ingest-v0.1.1-async.sh" "$@"
