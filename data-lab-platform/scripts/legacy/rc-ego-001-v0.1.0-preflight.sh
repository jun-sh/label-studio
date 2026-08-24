#!/usr/bin/env bash
# DEPRECATED — use rc-ego-001-v0.1.1-preflight.sh
echo "[deprecated] rc-ego-001-v0.1.0-preflight.sh → rc-ego-001-v0.1.1-preflight.sh" >&2
exec bash "$(dirname "$0")/rc-ego-001-v0.1.1-preflight.sh" "$@"
