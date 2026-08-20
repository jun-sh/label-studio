#!/usr/bin/env bash
# DEPRECATED — use deploy-stream-ingest-v0.1.1-async.sh
echo "[deprecated] deploy-stream-ingest-v0.1.0-async.sh → deploy-stream-ingest-v0.1.1-async.sh" >&2
exec bash "$(dirname "$0")/deploy-stream-ingest-v0.1.1-async.sh" "$@"
