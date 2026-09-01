#!/usr/bin/env python3
"""Fetch Track2 Round3 MCAP from 130, run local H.264 remux derive, print timing."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[2]
STUDIO = ROOT / "data-lab-platform" / "lerobot-studio"
STATION = "ego-mcap-track2"
SESSION = os.environ.get("TRACK2_SESSION_ID", "sess_5a86abed47324de78fd587302e3a1e98")
SEG = "seg_000001"
TARGET = os.environ.get("PROVISION_TARGET", "server@10.10.10.130")
USER, HOST = TARGET.split("@", 1)
PASSWORD = os.environ.get("RC_CAPTURE_PASS", "1")
REMOTE_MCAP = (
    f"/home/server/cache/ego-mcap-track2/segments/sessions/{SESSION}/segments/{SEG}/segment.mcap"
)
STREAM_ROOT = ROOT / "data-storage" / "stream" / STATION
LOCAL_MCAP = Path("/tmp") / f"{SEG}.mcap"
LOCAL_ZST = STREAM_ROOT / "raw" / "segments" / SESSION / f"{SEG}.mcap.zst"
TRACK1_BASELINE_S = float(os.environ.get("TRACK1_DERIVE_BASELINE_S", "47"))


def fetch_mcap() -> Path:
    LOCAL_MCAP.parent.mkdir(parents=True, exist_ok=True)
    if LOCAL_MCAP.is_file() and LOCAL_MCAP.stat().st_size > 1_000_000:
        print(f"[verify] reuse cached {LOCAL_MCAP}")
        return LOCAL_MCAP
    print(f"[verify] fetching {REMOTE_MCAP} from {HOST} ...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    sftp = c.open_sftp()
    sftp.get(REMOTE_MCAP, str(LOCAL_MCAP))
    sftp.close()
    c.close()
    print(f"[verify] downloaded {LOCAL_MCAP.stat().st_size / 1048576:.2f} MB")
    return LOCAL_MCAP


def pack_and_stage(mcap: Path) -> None:
    LOCAL_ZST.parent.mkdir(parents=True, exist_ok=True)
    (STREAM_ROOT / "meta").mkdir(parents=True, exist_ok=True)
    subprocess.run(["zstd", "-f", "-o", str(LOCAL_ZST), str(mcap)], check=True)
    print(f"[verify] staged {LOCAL_ZST} ({LOCAL_ZST.stat().st_size / 1048576:.2f} MB zst)")


def run_derive() -> dict:
    node = f"""
import {{ deriveUnit }} from './derive/unit.mjs';
const out = await deriveUnit('{STATION}', '{STREAM_ROOT}', '{SESSION}');
console.log(JSON.stringify({{
  ok: out.ok,
  frames: out.frames,
  mux: (out.muxResults || []).map((m) => ({{
    videoKey: m.videoKey,
    mode: m.mode,
    ok: m.ok,
    elapsedMs: m.elapsedMs,
  }})),
  deriveMs: out.unitJson?.derive?.elapsed_ms,
  videoCodec: out.unitJson?.derive?.video_codec,
  muxMode: out.unitJson?.derive?.mux_mode,
}}));
process.exit(out.ok ? 0 : 1);
"""
    started = time.time()
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node],
        cwd=STUDIO,
        capture_output=True,
        text=True,
    )
    elapsed = time.time() - started
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    line = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith("{")][-1]
    result = json.loads(line)
    result["wall_s"] = round(elapsed, 2)
    return result


def main() -> int:
    subprocess.run(
        ["bash", str(ROOT / "data-lab-platform/scripts/ego-mcap-track2-bootstrap-34.sh")],
        check=True,
    )
    mcap = fetch_mcap()
    pack_and_stage(mcap)
    result = run_derive()
    derive_s = round((result.get("deriveMs") or 0) / 1000, 2)
    wall_s = result.get("wall_s", derive_s)
    modes = {m.get("mode") for m in result.get("mux", [])}
    print(json.dumps(result, indent=2))
    print(f"[verify] derive_total_s={derive_s} wall_s={wall_s} modes={modes}")
    print(f"[verify] vs Track1 baseline {TRACK1_BASELINE_S}s → {derive_s / TRACK1_BASELINE_S * 100:.1f}%")
    if "encode" in modes:
        print("[verify] FAIL: MUX_ENCODE path detected", file=sys.stderr)
        return 1
    if not result.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
