#!/usr/bin/env python3
"""EGO edge local capture control — stdlib-only HTTP server (mobile web UI)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Config (override via environment)
# ---------------------------------------------------------------------------
HOST = os.environ.get("EGO_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("EGO_WEB_PORT", "8080"))
CAPTURE_TARGET = os.environ.get("EGO_CAPTURE_TARGET", "ecs-oak-capture-stack.target")
CAPTURE_RECORD_UNIT = os.environ.get("EGO_CAPTURE_RECORD_UNIT", "ecs-record-oak-stream.service")
STANDBY_STACK_TARGET = os.environ.get("EGO_STANDBY_STACK_TARGET", "ecs-oak-standby-stack.target")
STATION_HEARTBEAT_UNIT = os.environ.get(
    "EGO_STATION_HEARTBEAT_UNIT",
    "ecs-station-heartbeat.service",
)
SEGMENT_ACTIVE_ROOT = Path(
    os.environ.get("EGO_SEGMENT_ACTIVE_ROOT", "/dev/shm/ego-capture-active"),
)
SEGMENT_ROOT = Path(
    os.environ.get("EGO_SEGMENT_ROOT", "/home/server/cache/ego-lan-214/segments"),
)
CHECKPOINT_PATH = Path(
    os.environ.get(
        "EGO_CAPTURE_CHECKPOINT",
        "/home/server/cache/ego-lan-214/segments/checkpoint.json",
    ),
)
SEGMENT_STORE_VERSION = 1
DEFAULT_CAPTURE_TASK = os.environ.get(
    "EGO_DEFAULT_CAPTURE_TASK",
    "Perform egocentric manipulation tasks at the laboratory workbench",
)
PREVIEW_URL = os.environ.get(
    "EGO_PREVIEW_URL",
    "http://127.0.0.1:8765/preview/front_left/jpg",
)
STORAGE_WARN_GB = float(os.environ.get("EGO_STORAGE_WARN_GB", "2"))
START_TIMEOUT_S = float(os.environ.get("EGO_CAPTURE_START_TIMEOUT_S", "45"))
STOP_TIMEOUT_S = float(os.environ.get("EGO_CAPTURE_STOP_TIMEOUT_S", "120"))
ENSURE_STOPPED_TIMEOUT_S = float(os.environ.get("EGO_CAPTURE_ENSURE_STOPPED_S", "120"))
MIN_ACTION_INTERVAL_S = float(os.environ.get("EGO_MIN_ACTION_INTERVAL_S", "3"))
JOURNAL_CACHE_TTL_S = float(os.environ.get("EGO_JOURNAL_CACHE_TTL_S", "0.4"))
STATUS_POLL_MS = int(os.environ.get("EGO_STATUS_POLL_MS", "300"))

_lock = threading.Lock()
_busy = False
_busy_action: str | None = None
_last_action_mono = 0.0
_last_error = ""
_capture_writing_since: float | None = None
_journal_cache: tuple[float, float | None, bool] | None = None
_idle_standby_last_try = 0.0


def _unit_active_since_epoch(unit: str) -> float | None:
    proc = _systemctl(
        "show",
        unit,
        "--property=ActiveEnterTimestamp",
        "--value",
        timeout=5,
    )
    raw = proc.stdout.strip()
    if not raw or raw == "n/a":
        return None
    try:
        epoch = subprocess.run(
            ["date", "-d", raw, "+%s"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if epoch.returncode == 0 and epoch.stdout.strip().isdigit():
            return float(epoch.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        dt = datetime.strptime(raw.rsplit(" ", 1)[0], "%a %Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _manifest_created_epoch(data: dict[str, Any]) -> float | None:
    raw = data.get("created_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        if raw.endswith("Z"):
            dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _capture_run_since_epoch() -> float | None:
    since = _unit_active_since_epoch(CAPTURE_RECORD_UNIT)
    if since is not None:
        return since
    return _unit_active_since_epoch(CAPTURE_TARGET)


def _shm_open_segment_bin_count(since_epoch: float | None) -> int:
    """Count frame bins only in open segments started during the current capture run."""
    sessions = SEGMENT_ACTIVE_ROOT / "sessions"
    if not sessions.is_dir():
        return 0
    best = 0
    for manifest_path in sessions.glob("*/segments/seg_*/manifest.json"):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("closed"):
            continue
        if since_epoch is not None:
            created = _manifest_created_epoch(data)
            # Ignore stale shm dirs left from prior failed stops.
            if created is None or created < since_epoch - 2.0:
                continue
        frames_dir = manifest_path.parent / "frames"
        if frames_dir.is_dir():
            best = max(best, sum(1 for _ in frames_dir.glob("*.bin")))
    return best


def _journal_capture_progress_since(since_epoch: float | None) -> bool:
    """True when record unit logged capture-only start or recent captured= lines."""
    global _journal_cache
    if since_epoch is None:
        return False
    now = time.monotonic()
    if (
        _journal_cache is not None
        and _journal_cache[1] == since_epoch
        and now - _journal_cache[0] < JOURNAL_CACHE_TTL_S
    ):
        return _journal_cache[2]
    since_local = datetime.fromtimestamp(since_epoch).strftime("%Y-%m-%d %H:%M:%S")
    proc_tail = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            CAPTURE_RECORD_UNIT,
            f"--since={since_local}",
            "-n",
            "40",
            "--no-pager",
            "-o",
            "cat",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if proc_tail.returncode == 0 and "captured=" in proc_tail.stdout:
        _journal_cache = (now, since_epoch, True)
        return True
    proc_grep = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            CAPTURE_RECORD_UNIT,
            f"--since={since_local}",
            "-g",
            "capture-only session=",
            "--no-pager",
            "-o",
            "cat",
            "-n",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    result = proc_grep.returncode == 0 and proc_grep.stdout.strip() != ""
    _journal_cache = (now, since_epoch, result)
    return result


def _capture_frames_writing() -> bool:
    since = _capture_run_since_epoch()
    if _shm_open_segment_bin_count(since) > 0:
        return True
    if _journal_capture_progress_since(since):
        return True
    cp = _read_checkpoint()
    try:
        return int(cp.get("nextFrameIndex", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex}"


def _read_checkpoint() -> dict[str, Any]:
    if not CHECKPOINT_PATH.is_file():
        return {}
    try:
        raw = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _begin_new_capture_session() -> str:
    """Each start-recording → fresh sessionId (one episode on 34 after upload)."""
    prev = _read_checkpoint()
    task = prev.get("task") or DEFAULT_CAPTURE_TASK
    session_id = _new_session_id()
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SEGMENT_STORE_VERSION,
        "sessionId": session_id,
        "nextFrameIndex": 0,
        "segmentSeq": 0,
        "task": task,
    }
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(CHECKPOINT_PATH)
    strict_emit = CHECKPOINT_PATH.parent / "strict_emit_ts.json"
    try:
        strict_emit.unlink(missing_ok=True)
    except OSError:
        pass
    print(f"capture_session_new session_id={session_id}", flush=True)
    return session_id


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _systemctl(*args: str, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=["systemctl", "--user", *args],
            returncode=124,
            stdout="",
            stderr="timeout",
        )


def _capture_unit_state(unit: str = CAPTURE_RECORD_UNIT) -> str:
    proc = _systemctl("is-active", unit, timeout=5)
    return proc.stdout.strip()


def _capture_active() -> bool:
    rec = _capture_unit_state(CAPTURE_RECORD_UNIT)
    if rec in ("active", "activating", "deactivating"):
        return True
    return _capture_unit_state(CAPTURE_TARGET) == "active"


def _capture_stopping() -> bool:
    state = _capture_unit_state(CAPTURE_RECORD_UNIT)
    return state in ("activating", "deactivating")


def _preview_port_in_use(port: int = 8765) -> bool:
    proc = subprocess.run(
        ["ss", "-H", "-tln", f"sport = :{port}"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return proc.returncode == 0 and proc.stdout.strip() != ""


def _wait_preview_port_free(timeout_s: float = 20.0, port: int = 8765) -> bool:
    """Standby preview holds :8765; capture record must bind the same port."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _preview_port_in_use(port):
            return True
        time.sleep(0.25)
    return False


def _ensure_capture_fully_stopped(timeout_s: float = 25.0) -> bool:
    """Stop lingering capture unit before a new start (avoids OAK in-use / 120s stop wait)."""
    state = _capture_unit_state(CAPTURE_RECORD_UNIT)
    if state in ("inactive", "failed", ""):
        return True
    _systemctl("stop", CAPTURE_TARGET, timeout=STOP_TIMEOUT_S)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = _capture_unit_state(CAPTURE_RECORD_UNIT)
        if state in ("inactive", "failed", ""):
            return True
        time.sleep(0.25)
    return False


def _stop_standby_preview() -> None:
    """Release :8765 and OAK before capture (keep short — blocks the HTTP start thread)."""
    _systemctl("stop", STANDBY_STACK_TARGET, timeout=30)
    _systemctl("stop", "ecs-preview-standby.service", timeout=15)
    subprocess.run(
        ["pkill", "-TERM", "-f", "preview_standby"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if _preview_port_in_use():
        subprocess.run(
            ["fuser", "-k", "-TERM", "8765/tcp"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        time.sleep(0.5)
    if not _wait_preview_port_free(timeout_s=8.0):
        print("warning: preview port 8765 still in use after standby stop", flush=True)


def _start_standby_preview() -> None:
    _systemctl("start", STANDBY_STACK_TARGET, timeout=60)
    _ensure_station_heartbeat()


def _ensure_station_heartbeat() -> None:
    """Keep 34 collection UI in sync after capture stops (heartbeat is not PartOf capture stack)."""
    if _systemctl("is-active", STATION_HEARTBEAT_UNIT, timeout=5).stdout.strip() == "active":
        return
    _systemctl("start", STATION_HEARTBEAT_UNIT, timeout=20)


def _maybe_ensure_standby_preview() -> None:
    """130 on-demand preview: gateway on :8765; OAK wakes when preview HTTP is polled."""
    global _idle_standby_last_try
    if _capture_active():
        return
    now = time.monotonic()
    if now - _idle_standby_last_try < 20.0:
        return
    _idle_standby_last_try = now
    state = _systemctl("is-active", STANDBY_STACK_TARGET, timeout=5).stdout.strip()
    if state == "active":
        return
    threading.Thread(target=_start_standby_preview, name="ego-web-standby", daemon=True).start()


def _format_free_gb(path: Path) -> str:
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        gb = usage.free / (1024**3)
        return f"{gb:.1f} GB"
    except OSError:
        return "—"


def _storage_free_bytes(path: Path) -> int | None:
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        return int(usage.free)
    except OSError:
        return None


def _count_segments(root: Path) -> int:
    sessions = root / "sessions"
    if not sessions.is_dir():
        return 0
    count = 0
    for sess in sessions.iterdir():
        if not sess.is_dir():
            continue
        seg_root = sess / "segments"
        if not seg_root.is_dir():
            continue
        for seg in seg_root.iterdir():
            if seg.is_dir() and seg.name.startswith("seg_"):
                count += 1
    return count


def _build_status() -> dict[str, Any]:
    global _busy, _busy_action, _last_error, _capture_writing_since, _journal_cache

    with _lock:
        busy = _busy
        busy_action = _busy_action
        err = _last_error

    active = _capture_active()
    stopping = _capture_stopping()
    if not active and not busy:
        _maybe_ensure_standby_preview()
    frames_writing = active and not stopping and _capture_frames_writing()

    if not active:
        _capture_writing_since = None
        _journal_cache = None

    duration = 0
    if active and frames_writing:
        if _capture_writing_since is None:
            _capture_writing_since = time.time()
        duration = max(0, int(time.time() - _capture_writing_since))

    free_bytes = _storage_free_bytes(SEGMENT_ROOT)
    storage_warn = (
        free_bytes is not None and free_bytes < int(STORAGE_WARN_GB * (1024**3))
    )

    if busy and busy_action == "start":
        state = "starting"
        msg = "正在启动采集服务，请稍候…"
    elif busy and busy_action == "stop":
        state = "stopping"
        msg = "正在保存数据，请勿断电…"
    elif stopping and not frames_writing:
        state = "stopping"
        msg = "正在保存数据，完成后可再次开始录制"
    elif err and not (active and frames_writing):
        state = "error"
        msg = err
    elif active and frames_writing:
        state = "recording"
        msg = ""
    elif active:
        state = "warming"
        msg = "开始写入数据后计时"
    else:
        state = "idle"
        msg = ""

    return {
        "state": state,
        "duration": duration,
        "storage_free": _format_free_gb(SEGMENT_ROOT),
        "storage_warn": storage_warn,
        "segment_count": _count_segments(SEGMENT_ROOT),
        "msg": msg,
        "capture_active": active,
        "frames_writing": frames_writing,
    }


def _do_capture_action(action: str) -> tuple[bool, str]:
    """Run start/stop work (caller owns _busy lifecycle)."""
    global _last_error

    if action == "start":
        if _capture_active() and _capture_unit_state(CAPTURE_RECORD_UNIT) == "active":
            return True, "采集已在运行"
        if _capture_stopping():
            if not _ensure_capture_fully_stopped(timeout_s=ENSURE_STOPPED_TIMEOUT_S):
                _last_error = "上一段采集仍在退出，请稍后再试"
                return False, _last_error
        elif not _ensure_capture_fully_stopped(timeout_s=ENSURE_STOPPED_TIMEOUT_S):
            _last_error = "采集服务未能完全停止，请稍后再试"
            return False, _last_error
        _begin_new_capture_session()
        _stop_standby_preview()
        proc = _systemctl("start", CAPTURE_TARGET, timeout=START_TIMEOUT_S)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "启动失败").strip()
            _last_error = f"无法启动采集：{detail}"
            _start_standby_preview()
            return False, _last_error
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if _capture_active() and _capture_unit_state(CAPTURE_RECORD_UNIT) == "active":
                return True, ""
            time.sleep(0.5)
        _last_error = "相机启动超时，请检查 OAK 设备是否连接"
        _systemctl("stop", CAPTURE_TARGET, timeout=30)
        _start_standby_preview()
        return False, _last_error

    if action == "stop":
        if not _capture_active():
            _last_error = ""
            _start_standby_preview()
            return True, ""
        proc = _systemctl("stop", CAPTURE_TARGET, timeout=STOP_TIMEOUT_S)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "停止失败").strip()
            _last_error = f"无法停止采集：{detail}"
            return False, _last_error
        deadline = time.monotonic() + STOP_TIMEOUT_S
        ok = False
        while time.monotonic() < deadline:
            if not _capture_active():
                ok = True
                break
            time.sleep(0.5)
        if not ok:
            _systemctl("stop", CAPTURE_RECORD_UNIT, timeout=STOP_TIMEOUT_S)
            deadline = time.monotonic() + 90.0
            while time.monotonic() < deadline:
                if not _capture_active():
                    ok = True
                    break
                time.sleep(0.5)
        if not ok:
            _last_error = "停止超时，请稍后刷新页面查看状态"
            return False, _last_error
        _last_error = ""
        _start_standby_preview()
        return True, ""

    return False, "未知操作"


def _capture_action_worker(action: str) -> None:
    global _busy, _busy_action, _last_action_mono, _last_error
    try:
        ok, msg = _do_capture_action(action)
        if not ok and msg:
            _last_error = msg
    finally:
        with _lock:
            _busy = False
            _busy_action = None
            _last_action_mono = time.monotonic()


def _dispatch_capture_action(action: str) -> tuple[bool, str, bool]:
    """Queue start/stop on a worker thread. Returns (success, msg, accepted_async)."""
    global _busy, _busy_action, _last_error

    now = time.monotonic()
    with _lock:
        if _busy:
            return False, "操作进行中，请稍候", False
        if now - _last_action_mono < MIN_ACTION_INTERVAL_S:
            return False, "操作过快，请稍后再试", False

    if action == "start":
        rec = _capture_unit_state(CAPTURE_RECORD_UNIT)
        if rec == "active":
            return True, "采集已在运行", False
    elif action == "stop":
        if not _capture_active():
            ok, msg = _do_capture_action("stop")
            return ok, msg, False

    with _lock:
        _busy = True
        _busy_action = action
        _last_error = ""

    threading.Thread(
        target=_capture_action_worker,
        args=(action,),
        name=f"ego-web-{action}",
        daemon=True,
    ).start()
    return True, "", True


def _fetch_preview() -> tuple[bytes | None, str]:
    req = urllib.request.Request(PREVIEW_URL, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.read(), resp.headers.get("Content-Type", "image/jpeg")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, "image/jpeg"


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="theme-color" content="#f0f2f5" />
  <title>EGO 采集</title>
  <style>
    :root {
      --bg: #f0f2f5;
      --text: #1a1a1a;
      --muted: #8a8a8a;
      --accent: #6da9d6;
      --accent-dark: #4a8fc4;
      --preview-bg: #ebf4fb;
      --dot-idle: #f5a623;
      --dot-rec: #22c55e;
      --dot-warn: #d97706;
      --dot-err: #dc2626;
      --btn-start: #6da9d6;
      --btn-stop: #e57373;
      --value: #6da9d6;
      --warn: #b45309;
      --radius: 20px;
      --card-shadow: 0 4px 20px rgba(0,0,0,.06);
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0;
      font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg); color: var(--text);
      min-height: 100dvh;
    }
    .app {
      max-width: 480px; margin: 0 auto;
      padding: 14px 16px calc(20px + env(safe-area-inset-bottom));
      display: flex; flex-direction: column; gap: 14px;
    }

    .status-card {
      background: #fff;
      border-radius: var(--radius);
      padding: 16px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      box-shadow: var(--card-shadow);
    }
    .status-body {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      flex: 1;
      min-width: 0;
    }
    .status-dot {
      width: 10px; height: 10px;
      border-radius: 50%;
      margin-top: 6px;
      flex-shrink: 0;
      background: var(--dot-idle);
      transition: background .25s;
    }
    .status-card[data-state="idle"] .spinner { display: none !important; visibility: hidden; }
    .status-card[data-state="idle"] .status-dot { background: var(--dot-idle); }
    .status-card[data-state="recording"] .status-dot { background: var(--dot-rec); }
    .status-card[data-state="starting"] .status-dot,
    .status-card[data-state="warming"] .status-dot,
    .status-card[data-state="stopping"] .status-dot { background: var(--dot-warn); }
    .status-card[data-state="error"] .status-dot { background: var(--dot-err); }
    .status-text { flex: 1; min-width: 0; }
    .status-title-row {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 1.05rem;
      font-weight: 700;
      line-height: 1.35;
      color: var(--text);
    }
    .status-sub {
      margin-top: 4px;
      font-size: .82rem;
      color: var(--muted);
      line-height: 1.4;
    }
    .status-sub:empty { display: none; }
    .status-card[data-state="recording"] .status-sub { color: var(--dot-rec); }
    .status-wifi {
      width: 22px; height: 22px;
      flex-shrink: 0;
      color: var(--accent);
      opacity: .85;
    }
  .status-card[data-state="error"] .status-wifi { color: var(--dot-err); }
    .spinner {
      display: inline-block; width: 16px; height: 16px;
      border: 2px solid rgba(0,0,0,.12);
      border-top-color: var(--dot-warn);
      border-radius: 50%;
      animation: spin .8s linear infinite;
      flex-shrink: 0;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .preview-wrap {
      background: var(--preview-bg);
      border-radius: var(--radius);
      overflow: hidden;
      aspect-ratio: 4/3;
      position: relative;
      box-shadow: var(--card-shadow);
    }
    .preview-wrap img {
      width: 100%; height: 100%;
      object-fit: cover; display: block;
      background: var(--preview-bg);
    }
    .preview-placeholder {
      position: absolute; inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 12px;
      padding: 20px;
      text-align: center;
      color: var(--muted);
      font-size: .88rem;
      line-height: 1.45;
    }
    .preview-camera-icon {
      width: 72px; height: 72px;
      color: var(--accent);
      opacity: .75;
    }
    .preview-timer {
      position: absolute;
      top: 12px; left: 12px;
      background: rgba(255,255,255,.92);
      border-radius: 10px;
      padding: 6px 12px;
      font-size: .95rem;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      color: var(--text);
      box-shadow: 0 2px 8px rgba(0,0,0,.08);
      z-index: 2;
    }
    .preview-rec {
      position: absolute;
      top: 12px; right: 12px;
      background: rgba(255,255,255,.92);
      border-radius: 10px;
      padding: 6px 10px;
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: .78rem;
      font-weight: 700;
      color: #e53935;
      box-shadow: 0 2px 8px rgba(0,0,0,.08);
      z-index: 2;
    }
    .rec-dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #e53935;
      animation: rec-pulse 1.2s ease-in-out infinite;
    }
    @keyframes rec-pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: .35; }
    }

    .btn-main {
      width: 100%; min-height: 58px;
      border: none; border-radius: 999px;
      font-size: 1.15rem; font-weight: 700;
      color: #fff; cursor: pointer;
      box-shadow: var(--card-shadow);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      transition: transform .1s, opacity .2s, background .2s;
      -webkit-tap-highlight-color: transparent;
      touch-action: manipulation;
    }
    .btn-main:active:not(:disabled) { transform: scale(.98); }
    .btn-main:disabled { opacity: .55; cursor: not-allowed; }
    .btn-main[data-mode="start"] { background: var(--btn-start); }
    .btn-main[data-mode="stop"] { background: var(--btn-stop); }
    .btn-icon {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 22px; height: 22px;
    }
    .btn-icon svg { width: 20px; height: 20px; display: block; }

    .info {
      display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
    }
    .info-card {
      background: #fff;
      border-radius: 16px;
      padding: 14px 16px;
      box-shadow: var(--card-shadow);
    }
    .info-label { font-size: .78rem; color: var(--muted); }
    .info-value {
      font-size: 1.45rem;
      font-weight: 700;
      margin-top: 6px;
      color: var(--value);
      line-height: 1.2;
    }
    .info-value.warn { color: var(--warn); }

    .hints {
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 0 4px;
    }
    .hint-row {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      font-size: .78rem;
      color: var(--muted);
      line-height: 1.45;
    }
    .hint-icon {
      width: 16px; height: 16px;
      flex-shrink: 0;
      margin-top: 1px;
      color: #b0b0b0;
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="status-card" id="statusBar" data-state="idle">
      <div class="status-body">
        <span class="status-dot" id="statusDot" aria-hidden="true"></span>
        <div class="status-text">
          <div class="status-title-row">
            <span class="spinner" id="statusSpinner" hidden></span>
            <span id="statusTitleText">设备待机中</span>
          </div>
          <div class="status-sub" id="statusSub"></div>
        </div>
      </div>
      <svg class="status-wifi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M5 12.55a11 11 0 0 1 14 0"/><path d="M8.5 16.42a6 6 0 0 1 7 0"/><path d="M12 20h.01"/>
      </svg>
    </header>

    <section class="preview-wrap" aria-label="实时预览">
      <img id="previewImg" alt="" hidden />
      <div class="preview-placeholder" id="previewPh">
        <svg class="preview-camera-icon" viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <rect x="8" y="18" width="48" height="34" rx="4"/>
          <circle cx="32" cy="35" r="10"/>
          <path d="M22 18l4-8h20l4 8"/>
        </svg>
        <span>开始录制后显示实时画面</span>
      </div>
      <div class="preview-timer" id="previewTimer" hidden>00:00:00</div>
      <div class="preview-rec" id="previewRec" hidden><span class="rec-dot"></span>REC</div>
    </section>

    <button type="button" class="btn-main" id="mainBtn" data-mode="start">
      <span class="btn-icon" id="mainBtnIcon" aria-hidden="true"></span>
      <span id="mainBtnLabel">开始录制</span>
    </button>

    <section class="info">
      <div class="info-card">
        <div class="info-label">磁盘剩余</div>
        <div class="info-value" id="storageFree">—</div>
      </div>
      <div class="info-card">
        <div class="info-label">已生成段数</div>
        <div class="info-value" id="segmentCount">0 段</div>
      </div>
    </section>

    <div class="hints">
      <div class="hint-row">
        <svg class="hint-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M5 12.55a11 11 0 0 1 14 0"/><path d="M8.5 16.42a6 6 0 0 1 7 0"/><path d="M12 20h.01"/>
        </svg>
        <span>录制前请保持 WiFi 连接</span>
      </div>
      <div class="hint-row">
        <svg class="hint-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>
        </svg>
        <span>手机断开不影响数据写入</span>
      </div>
    </div>
  </div>
  <script>
(function () {
  var statusBar = document.getElementById("statusBar");
  var statusSpinner = document.getElementById("statusSpinner");
  var statusTitleText = document.getElementById("statusTitleText");
  var statusSub = document.getElementById("statusSub");
  var mainBtn = document.getElementById("mainBtn");
  var mainBtnIcon = document.getElementById("mainBtnIcon");
  var mainBtnLabel = document.getElementById("mainBtnLabel");
  var previewImg = document.getElementById("previewImg");
  var previewPh = document.getElementById("previewPh");
  var previewTimer = document.getElementById("previewTimer");
  var previewRec = document.getElementById("previewRec");
  var storageFree = document.getElementById("storageFree");
  var segmentCount = document.getElementById("segmentCount");

  var pollTimer = null;
  var previewPollTimer = null;
  var idlePreviewTimer = null;
  var actionInFlight = false;

  var ICON_PLAY = "<svg viewBox=\\"0 0 24 24\\" fill=\\"currentColor\\"><path d=\\"M8 5v14l11-7z\\"/></svg>";
  var ICON_STOP = "<svg viewBox=\\"0 0 24 24\\" fill=\\"currentColor\\"><rect x=\\"6\\" y=\\"6\\" width=\\"12\\" height=\\"12\\" rx=\\"1\\"/></svg>";

  function formatDuration(sec) {
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    return (h < 10 ? "0" : "") + h + ":" +
      (m < 10 ? "0" : "") + m + ":" +
      (s < 10 ? "0" : "") + s;
  }

  function setStatusTitle(showSpinner, text) {
    statusSpinner.hidden = !showSpinner;
    statusTitleText.textContent = text;
  }

  function setMainBtn(mode) {
    mainBtn.setAttribute("data-mode", mode);
    if (mode === "stop") {
      mainBtnLabel.textContent = "结束录制";
      mainBtnIcon.innerHTML = ICON_STOP;
    } else {
      mainBtnLabel.textContent = "开始录制";
      mainBtnIcon.innerHTML = ICON_PLAY;
    }
  }

  function setPreviewOverlay(st, duration) {
    var showOverlay = st === "recording" || st === "warming";
    previewTimer.hidden = !showOverlay;
    previewRec.hidden = st !== "recording";
    if (showOverlay) {
      previewTimer.textContent = formatDuration(duration || 0);
    }
  }

  function applyStatus(data) {
    var st = data.state || "idle";
    statusBar.setAttribute("data-state", st);

    if (st === "idle") {
      setStatusTitle(false, "设备待机中");
      statusSub.textContent = data.msg || "";
      setMainBtn("start");
    } else if (st === "warming") {
      setStatusTitle(true, "设备已连接");
      statusSub.textContent = data.msg || "相机初始化中";
      setMainBtn("stop");
    } else if (st === "recording") {
      setStatusTitle(false, "设备已连接");
      statusSub.textContent = "正在录制中";
      setMainBtn("stop");
    } else if (st === "starting") {
      setStatusTitle(true, "正在启动相机");
      statusSub.textContent = data.msg || "正在释放预览并启动采集，请稍候…";
      mainBtn.disabled = true;
    } else if (st === "stopping") {
      setStatusTitle(true, "正在保存数据");
      statusSub.textContent = data.msg || "请勿断电，保存完成后可再次开始";
      setMainBtn("stop");
      mainBtn.disabled = true;
      stopIdlePreview();
      stopPreview();
    } else if (st === "error") {
      setStatusTitle(false, "出现问题");
      statusSub.textContent = data.msg || "请稍后重试";
      setMainBtn(data.capture_active ? "stop" : "start");
    }

    if (st !== "starting" && st !== "stopping" && !actionInFlight) {
      mainBtn.disabled = false;
    } else if (st === "stopping") {
      mainBtn.disabled = true;
    }

    setPreviewOverlay(st, data.duration);

    storageFree.textContent = data.storage_free || "—";
    storageFree.className = "info-value" + (data.storage_warn ? " warn" : "");
    var seg = data.segment_count != null ? data.segment_count : 0;
    segmentCount.textContent = seg + " 段";

    if (st === "recording" || st === "warming") {
      stopIdlePreview();
      startPreview();
    } else if (st === "idle") {
      stopPreview();
      startIdlePreview();
    } else if (st !== "starting") {
      stopPreview();
      stopIdlePreview();
    }
  }

  function fetchStatus() {
    return fetch("/api/status", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(applyStatus)
      .catch(function () {
        statusBar.setAttribute("data-state", "error");
        setStatusTitle(false, "无法连接设备");
        statusSub.textContent = "请确认已连接 EGO WiFi";
        setPreviewOverlay("idle", 0);
      });
  }

  function tickPreview() {
    if (previewImg.hidden) {
      previewImg.hidden = false;
      previewPh.hidden = true;
    }
    previewImg.src = "/api/preview/main.jpg?t=" + Date.now();
  }

  function startPreview() {
    if (previewPollTimer) return;
    tickPreview();
    previewPollTimer = setInterval(tickPreview, 400);
  }

  function stopPreview() {
    if (previewPollTimer) {
      clearInterval(previewPollTimer);
      previewPollTimer = null;
    }
    if (!idlePreviewTimer) {
      previewImg.hidden = true;
      previewPh.hidden = false;
      previewImg.removeAttribute("src");
    }
  }

  function startIdlePreview() {
    if (idlePreviewTimer) return;
    tickPreview();
    idlePreviewTimer = setInterval(tickPreview, 2000);
  }

  function stopIdlePreview() {
    if (idlePreviewTimer) {
      clearInterval(idlePreviewTimer);
      idlePreviewTimer = null;
    }
    if (!previewPollTimer) {
      previewImg.hidden = true;
      previewPh.hidden = false;
      previewImg.removeAttribute("src");
    }
  }

  function doAction(path) {
    if (actionInFlight) return;
    actionInFlight = true;
    mainBtn.disabled = true;
    if (path === "/api/capture/stop") {
      stopIdlePreview();
      statusBar.setAttribute("data-state", "stopping");
      setStatusTitle(true, "正在保存数据");
      statusSub.textContent = "请勿断电，保存完成后可再次开始";
      setPreviewOverlay("stopping", 0);
      stopPreview();
    } else if (path === "/api/capture/start") {
      stopIdlePreview();
      stopPreview();
      statusBar.setAttribute("data-state", "starting");
      setStatusTitle(true, "正在启动相机");
      statusSub.textContent = "正在释放预览并启动采集，请稍候…";
      mainBtn.disabled = true;
    }
    fetch(path, { method: "POST", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success && res.msg) {
          statusBar.setAttribute("data-state", "error");
          setStatusTitle(false, "操作失败");
          statusSub.textContent = res.msg;
        }
        return fetchStatus();
      })
      .catch(function () {
        statusBar.setAttribute("data-state", "error");
        setStatusTitle(false, "操作失败");
        statusSub.textContent = "网络异常，请重试";
      })
      .finally(function () {
        actionInFlight = false;
      });
  }

  setMainBtn("start");
  mainBtn.addEventListener("click", function () {
    var mode = mainBtn.getAttribute("data-mode");
    if (mode === "stop") {
      doAction("/api/capture/stop");
    } else {
      doAction("/api/capture/start");
    }
  });

  fetchStatus();
  pollTimer = setInterval(fetchStatus, __STATUS_POLL_MS__);
})();
  </script>
</body>
</html>
"""


class EgoWebHandler(BaseHTTPRequestHandler):
    server_version = "ego-web/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = INDEX_HTML.replace("__STATUS_POLL_MS__", str(STATUS_POLL_MS)).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/status":
            _json_response(self, HTTPStatus.OK, _build_status())
            return

        if path == "/api/preview/main.jpg":
            data, ctype = _fetch_preview()
            if not data:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "preview unavailable")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        if length:
            _ = self.rfile.read(length)

        if path == "/api/capture/start":
            ok, msg, accepted = _dispatch_capture_action("start")
            payload: dict[str, Any] = {"success": ok, "msg": msg}
            if accepted:
                payload["accepted"] = True
            _json_response(
                self,
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
                payload,
            )
            return

        if path == "/api/capture/stop":
            ok, msg, accepted = _dispatch_capture_action("stop")
            payload = {"success": ok, "msg": msg}
            if accepted:
                payload["accepted"] = True
            _json_response(
                self,
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
                payload,
            )
            return

        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), EgoWebHandler)
    print(f"ego-web listening on http://{HOST}:{PORT}", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
