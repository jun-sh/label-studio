#!/usr/bin/env python3
"""DEPRECATED — redirects to JPEG production verify."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = sys.argv[1] if len(sys.argv) > 1 else "server@10.10.10.130"

print("DEPRECATED: use ego-130-verify-production-paramiko.py", file=sys.stderr)
raise SystemExit(
    subprocess.call([str(ROOT / "scripts" / "ego-130-verify-production-paramiko.py"), TARGET])
)
