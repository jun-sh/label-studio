"""Resolve edge capture state for heartbeat / remote-preview policy."""

from __future__ import annotations

import os
import subprocess

CAPTURE_TARGET = os.environ.get("EGO_CAPTURE_TARGET", "ecs-oak-capture-stack.target")
CAPTURE_RECORD_UNIT = os.environ.get("EGO_CAPTURE_RECORD_UNIT", "ecs-record-oak-stream.service")
STANDBY_PREVIEW_UNIT = os.environ.get("EGO_STANDBY_PREVIEW_UNIT", "ecs-preview-standby.service")

VALID_CAPTURE_STATES = frozenset(
    {"offline", "idle", "starting", "warming", "recording", "stopping", "unknown"}
)


def _systemctl_is_active(unit: str) -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return proc.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_capture_state() -> str:
    """Best-effort state for 34 remote preview gating (fail-closed on capture stack)."""
    capture_on = _systemctl_is_active(CAPTURE_TARGET) or _systemctl_is_active(CAPTURE_RECORD_UNIT)
    if capture_on:
        # Remote preview must stay off for the whole capture stack lifetime.
        return "recording"
    if _systemctl_is_active(STANDBY_PREVIEW_UNIT):
        return "idle"
    return "offline"


def remote_preview_allowed(capture_state: str) -> bool:
    return capture_state == "idle"
