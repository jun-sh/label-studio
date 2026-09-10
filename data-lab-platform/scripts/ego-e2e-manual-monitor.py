#!/usr/bin/env python3
"""Monitor manual ego-001 capture → upload → derive → ego-process (real-time stdout)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

DATALAB = Path(os.environ.get("DATALAB_ROOT", Path(__file__).resolve().parents[2]))
STATION = os.environ.get("STATION_ID", "ego-001")
TARGET = int(os.environ.get("E2E_TARGET_EPISODES", "10"))
POLL_SEC = float(os.environ.get("E2E_POLL_SEC", "5"))
API_BASE = os.environ.get("LABEL_STUDIO_HOST", "http://127.0.0.1:8080").rstrip("/")
STREAM = DATALAB / "data-storage" / "stream" / STATION
RAW = STREAM / "raw" / "segments"
DERIVED = STREAM / "derived"
SAMPLES_ZIP = DATALAB / "data-storage" / "samples" / "egodome.zip"
EGO_PROCESS = DATALAB / "data-lab-platform" / "scripts" / "ego-process"


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def fetch_json(path: str) -> dict | None:
    try:
        with urlopen(f"{API_BASE}{path}", timeout=8) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def raw_sessions() -> list[str]:
    if not RAW.is_dir():
        return []
    return sorted(
        p.name
        for p in RAW.iterdir()
        if p.is_dir() and p.name.startswith("sess_")
    )


def ready_sessions() -> list[str]:
    out = []
    if not DERIVED.is_dir():
        return out
    for sess in raw_sessions():
        ready = STREAM / "state" / "sessions" / sess / "session.READY"
        if not ready.is_file():
            ready = STREAM / "state" / "sessions" / sess / ".status" / "READY"
        unit = DERIVED / sess / "unit.json"
        if ready.is_file() and unit.is_file():
            out.append(sess)
    return out


def derive_status() -> dict:
    data = fetch_json(f"/lerobot/api/collection/stations/{STATION}/derive-status")
    if not data:
        return {"phase": "?", "markers": 0, "total": 0, "parquetRows": 0}
    p = data.get("progress") or {}
    return {
        "phase": data.get("phase", "?"),
        "markers": p.get("markers", 0),
        "total": p.get("total", 0),
        "parquetRows": p.get("parquetRows", 0),
    }


def corpus_episode_count() -> int:
    meta = DATALAB / "data-storage" / "corpus" / "egodome" / "meta" / "info.json"
    if not meta.is_file():
        return 0
    try:
        info = json.loads(meta.read_text(encoding="utf-8"))
        return int(info.get("total_episodes") or info.get("total_frames") or 0)
    except Exception:
        return 0


def snapshot() -> dict:
    sessions = raw_sessions()
    ready = ready_sessions()
    d = derive_status()
    return {
        "uploaded": len(sessions),
        "ready": len(ready),
        "sessions": sessions,
        "ready_sessions": ready,
        "derive": d,
        "corpus_episodes": corpus_episode_count(),
        "viewer_zip": SAMPLES_ZIP.is_file(),
    }


def print_banner() -> None:
    log("=" * 60)
    log(f"手动 10 段验收监控 · station={STATION} · 目标={TARGET}")
    log("=" * 60)
    log("130 操作（每录 1 段重复一次）：")
    log("  1) 手机/网页 UI：开始录制 → 等 ~45s → 结束录制")
    log("     或: systemctl --user start ecs-record-oak-mcap.service")
    log("        等待结束后: systemctl --user stop ecs-record-oak-mcap.service")
    log("  2) 130 终端: ego-upload ego-001 --notify")
    log("  3) 看本监控进度，uploaded 到 10 后自动跑 ego-process")
    log("-" * 60)
    log(f"Collection: {API_BASE}/collection?station={STATION}")
    log(f"Egodome:    {API_BASE}/data/egodome")
    log("-" * 60)


def print_status(s: dict, prev: dict | None) -> None:
    d = s["derive"]
    line = (
        f"uploaded={s['uploaded']}/{TARGET}  ready={s['ready']}/{TARGET}  "
        f"derive={d['phase']} markers={d['markers']} parquet={d['parquetRows']}  "
        f"corpus={s['corpus_episodes']}  viewer_zip={'Y' if s['viewer_zip'] else 'N'}"
    )
    if prev is None or prev["uploaded"] != s["uploaded"]:
        new = set(s["sessions"]) - set((prev or {}).get("sessions", []))
        if new:
            log(f"🆕 新上传 session: {', '.join(sorted(new))}")
    if prev is None or prev["ready"] != s["ready"]:
        new_ready = set(s["ready_sessions"]) - set((prev or {}).get("ready_sessions", []))
        if new_ready:
            log(f"✅ derive READY: {', '.join(sorted(new_ready))}")
    log(line)


def wait_target_upload(prev: dict | None) -> dict:
    while True:
        s = snapshot()
        print_status(s, prev)
        if s["uploaded"] >= TARGET:
            log(f"已达 {TARGET} 段上传，等待全部 derive READY…")
            return s
        prev = s
        time.sleep(POLL_SEC)


def wait_all_ready(prev: dict) -> dict:
    while True:
        s = snapshot()
        print_status(s, prev)
        if s["ready"] >= TARGET and s["uploaded"] >= TARGET:
            return s
        prev = s
        time.sleep(POLL_SEC)


def run_ego_process() -> int:
    log("启动 ego-process（derive 已完成，跑 convert + viewer + 导出门禁）…")
    env = os.environ.copy()
    env.setdefault("EGO_EXPORT_REQUIRE_QC", "0")  # 技术验收：无 QC 旁路
    env.setdefault("EGO_CONVERT_SLO_B_SEC", "3600")
    proc = subprocess.Popen(
        ["bash", str(EGO_PROCESS), STATION],
        cwd=str(DATALAB),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip(), flush=True)
    return proc.wait()


def final_check() -> bool:
    s = snapshot()
    log("-" * 60)
    log("最终验收：")
    log(f"  raw sessions:     {s['uploaded']}/{TARGET}")
    log(f"  derive READY:     {s['ready']}/{TARGET}")
    log(f"  corpus episodes:  {s['corpus_episodes']}")
    log(f"  samples zip:      {SAMPLES_ZIP.is_file()}")
    delivery = fetch_json(f"/lerobot/api/collection/stations/{STATION}/delivery-status")
    if delivery:
        eps = delivery.get("episodes") or []
        log(f"  delivery-status:  {len(eps)} episodes")
        exportable = sum(1 for e in eps if e.get("delivery_status") == "exportable")
        log(f"  exportable:       {exportable}")
    export_dir = DATALAB / "data-storage" / "ego-delivery" / STATION / "by-session"
    manifests = list(export_dir.glob("*/meta/export_manifest.json")) if export_dir.is_dir() else []
    log(f"  export_manifest:  {len(manifests)}")
    ok = (
        s["uploaded"] >= TARGET
        and s["ready"] >= TARGET
        and s["corpus_episodes"] >= TARGET
        and SAMPLES_ZIP.is_file()
        and len(manifests) >= TARGET
    )
    if ok:
        log("🎉 全流程验收通过")
    else:
        log("❌ 验收未完全通过，请检查上方指标")
    return ok


def main() -> int:
    print_banner()
    prev: dict | None = None
    try:
        wait_target_upload(prev)
        prev = snapshot()
        wait_all_ready(prev)
        rc = run_ego_process()
        if rc != 0:
            log(f"ego-process 失败 exit={rc}")
            return rc
        return 0 if final_check() else 1
    except KeyboardInterrupt:
        log("监控已中断（用户 Ctrl+C）")
        return 130


if __name__ == "__main__":
    sys.exit(main())
