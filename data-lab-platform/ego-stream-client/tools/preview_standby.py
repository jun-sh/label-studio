#!/usr/bin/env python3
"""Idle-only preview probe (tools path for systemd on 214)."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

runpy.run_module("preview_standby", run_name="__main__")
