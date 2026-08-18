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
STANDBY_PREVIEW_UNIT = os.environ.get(
    "EGO_STANDBY_PREVIEW_UNIT",
    "ecs-preview-standby.service",
)
STANDBY_IDLE_STOP_S = float(os.environ.get("EGO_STANDBY_IDLE_STOP_S", "45"))
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
START_TIMEOUT_S = float(os.environ.get("EGO_CAPTURE_START_TIMEOUT_S", "90"))
STOP_TIMEOUT_S = float(os.environ.get("EGO_CAPTURE_STOP_TIMEOUT_S", "120"))
STOP_DEACTIVATING_KILL_S = float(os.environ.get("EGO_CAPTURE_DEACTIVATING_KILL_S", "45"))
MIN_ACTION_INTERVAL_S = float(os.environ.get("EGO_MIN_ACTION_INTERVAL_S", "3"))
JOURNAL_CACHE_TTL_S = float(os.environ.get("EGO_JOURNAL_CACHE_TTL_S", "0.4"))
STATUS_POLL_MS = int(os.environ.get("EGO_STATUS_POLL_MS", "300"))
PREVIEW_POLL_MS = int(os.environ.get("EGO_PREVIEW_POLL_MS", "200"))

_lock = threading.Lock()
_busy = False
_busy_action: str | None = None
_last_action_mono = 0.0
_last_error = ""
_capture_writing_since: float | None = None
_journal_cache: tuple[float, float | None, bool] | None = None
_standby_preview_touch_mono = 0.0
_last_preview_jpeg: bytes | None = None


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


def _journal_has_capture_only_since(since_epoch: float | None) -> bool:
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
    proc = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            CAPTURE_RECORD_UNIT,
            f"--since={since_local}",
            "-n",
            "200",
            "--no-pager",
            "-o",
            "cat",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0:
        _journal_cache = (now, since_epoch, False)
        return False
    text = proc.stdout
    result = "capture-only session=" in text or "captured=" in text
    _journal_cache = (now, since_epoch, result)
    return result


def _capture_frames_writing() -> bool:
    rec = _capture_unit_state(CAPTURE_RECORD_UNIT)
    if rec in ("deactivating", "inactive", "failed", ""):
        return False
    since = _capture_run_since_epoch()
    if _shm_open_segment_bin_count(since) > 0:
        return True
    return _journal_has_capture_only_since(since)


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
    if rec in ("active", "activating"):
        return True
    return _capture_unit_state(CAPTURE_TARGET) == "active"


def _preview_port_in_use(port: int = 8765) -> bool:
    proc = subprocess.run(
        ["ss", "-H", "-tln", f"sport = :{port}"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return proc.returncode == 0 and proc.stdout.strip() != ""


def _wait_preview_port_free(timeout_s: float = 25.0, port: int = 8765) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _preview_port_in_use(port):
            return True
        time.sleep(0.25)
    return False


def _capture_fully_idle() -> bool:
    return _capture_unit_state(CAPTURE_RECORD_UNIT) in ("inactive", "failed", "")


def _capture_stopping() -> bool:
    state = _capture_unit_state(CAPTURE_RECORD_UNIT)
    return state in ("activating", "deactivating")


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


def _touch_standby_preview_activity() -> None:
    global _standby_preview_touch_mono
    _standby_preview_touch_mono = time.monotonic()


def _wake_standby_preview_if_idle() -> None:
    """Start low-FPS standby preview when user browses (OAK off otherwise)."""
    with _lock:
        if _busy:
            return
    if _capture_active() or _capture_stopping():
        return
    _touch_standby_preview_activity()
    if _capture_unit_state(STANDBY_PREVIEW_UNIT) != "active":
        _systemctl("start", STANDBY_PREVIEW_UNIT, timeout=20)


def _maybe_stop_idle_standby_preview() -> None:
    """Stop standby preview after no browse/preview traffic."""
    global _standby_preview_touch_mono
    with _lock:
        busy = _busy
    if _capture_active() or busy or _capture_stopping():
        return
    if _standby_preview_touch_mono <= 0:
        return
    if time.monotonic() - _standby_preview_touch_mono < STANDBY_IDLE_STOP_S:
        return
    if _capture_unit_state(STANDBY_PREVIEW_UNIT) == "active":
        _systemctl("stop", STANDBY_PREVIEW_UNIT, timeout=10)
        _standby_preview_touch_mono = 0.0


def _stop_standby_preview() -> None:
    """Release :8765 and OAK before capture."""
    _systemctl("stop", STANDBY_PREVIEW_UNIT, timeout=10)
    _systemctl("stop", STANDBY_STACK_TARGET, timeout=10)
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
    if _preview_port_in_use():
        subprocess.run(
            ["fuser", "-k", "-KILL", "8765/tcp"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        time.sleep(0.3)
    if not _wait_preview_port_free(timeout_s=15.0):
        print("warning: preview port 8765 still in use after standby stop", flush=True)
    global _standby_preview_touch_mono
    _standby_preview_touch_mono = 0.0


def _start_standby_preview() -> None:
    _ensure_station_heartbeat()
    _wake_standby_preview_if_idle()


def _ensure_station_heartbeat() -> None:
    """Keep 34 collection UI in sync after capture stops (heartbeat is not PartOf capture stack)."""
    if _systemctl("is-active", STATION_HEARTBEAT_UNIT, timeout=5).stdout.strip() == "active":
        return
    _systemctl("start", STATION_HEARTBEAT_UNIT, timeout=20)


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
    rec_state = _capture_unit_state(CAPTURE_RECORD_UNIT)
    frames_writing = active and rec_state not in ("deactivating",) and _capture_frames_writing()

    if not active:
        _capture_writing_since = None
        _journal_cache = None
        _maybe_stop_idle_standby_preview()

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
        rec = _capture_unit_state(CAPTURE_RECORD_UNIT)
        if rec == "active" and frames_writing:
            msg = "正在停止采集，请稍候…"
        elif rec == "deactivating":
            msg = "正在写入磁盘，请勿断电（段数已不再增加）"
        else:
            msg = "正在保存数据，请勿断电…"
    elif active and frames_writing:
        state = "recording"
        msg = err if err else ""
    elif active:
        state = "warming"
        msg = err or "开始写入数据后计时"
    elif err:
        state = "error"
        msg = err
    else:
        state = "idle"
        msg = ""
        _last_error = ""

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


def _run_capture_action(action: str) -> tuple[bool, str]:
    global _busy, _busy_action, _last_error, _last_action_mono

    now = time.monotonic()
    with _lock:
        if _busy:
            return False, "操作进行中，请稍候"
        if now - _last_action_mono < MIN_ACTION_INTERVAL_S:
            return False, "操作过快，请稍后再试"
        _busy = True
        _busy_action = action
        _last_error = ""

    try:
        if action == "start":
            if _capture_active():
                return True, "采集已在运行"
            if _capture_stopping():
                if not _ensure_capture_fully_stopped():
                    _last_error = "上一段采集仍在退出，请稍后再试"
                    return False, _last_error
            elif not _ensure_capture_fully_stopped():
                _last_error = "采集服务未能完全停止，请稍后再试"
                return False, _last_error
            cached = _grab_preview_bytes()
            if cached:
                global _last_preview_jpeg
                _last_preview_jpeg = cached
            _stop_standby_preview()
            if _preview_port_in_use():
                _last_error = "预览端口 8765 未释放，请等待 10 秒后重试"
                _start_standby_preview()
                return False, _last_error
            _begin_new_capture_session()
            proc = _systemctl("start", CAPTURE_TARGET, timeout=START_TIMEOUT_S)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "启动失败").strip()
                _last_error = f"无法启动采集：{detail}"
                _start_standby_preview()
                return False, _last_error
            deadline = time.monotonic() + START_TIMEOUT_S
            while time.monotonic() < deadline:
                if _capture_active():
                    return True, ""
                time.sleep(0.5)
            _last_error = "相机启动超时，请检查 OAK 设备是否连接"
            _systemctl("stop", CAPTURE_TARGET, timeout=30)
            _start_standby_preview()
            return False, _last_error

        if action == "stop":
            if not _capture_active():
                ok = True
            else:
                _systemctl("stop", CAPTURE_RECORD_UNIT, timeout=30)
                _systemctl("stop", CAPTURE_TARGET, timeout=15)
                deadline = time.monotonic() + STOP_TIMEOUT_S
                ok = False
                deactivating_since: float | None = None
                while time.monotonic() < deadline:
                    rec = _capture_unit_state(CAPTURE_RECORD_UNIT)
                    if rec == "deactivating":
                        if deactivating_since is None:
                            deactivating_since = time.monotonic()
                        elif time.monotonic() - deactivating_since >= STOP_DEACTIVATING_KILL_S:
                            _systemctl("kill", CAPTURE_RECORD_UNIT, timeout=20)
                            deactivating_since = time.monotonic()
                    else:
                        deactivating_since = None
                    if _capture_fully_idle():
                        ok = True
                        break
                    time.sleep(0.5)
                if not ok:
                    _systemctl("kill", CAPTURE_RECORD_UNIT, timeout=20)
                    deadline = time.monotonic() + 45.0
                    while time.monotonic() < deadline:
                        if _capture_fully_idle():
                            ok = True
                            break
                        time.sleep(0.5)
                if not ok:
                    _last_error = "停止超时，请再次点击结束录制"
                    return False, _last_error
            _start_standby_preview()
            return True, ""

        return False, "未知操作"
    except Exception as exc:
        _last_error = f"操作异常：{exc}"
        return False, _last_error
    finally:
        with _lock:
            _busy = False
            _busy_action = None
            _last_action_mono = time.monotonic()


def _grab_preview_bytes() -> bytes | None:
    req = urllib.request.Request(PREVIEW_URL, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = resp.read()
            if len(data) > 100:
                return data
    except (urllib.error.URLError, TimeoutError, OSError, urllib.error.HTTPError):
        pass
    return None


def _fetch_preview() -> tuple[bytes | None, str]:
    global _last_preview_jpeg
    with _lock:
        busy_start = _busy and _busy_action == "start"
    if not (_capture_active() or _capture_stopping() or busy_start):
        return None, "image/jpeg"
    data = _grab_preview_bytes()
    if data:
        _last_preview_jpeg = data
        return data, "image/jpeg"
    with _lock:
        busy_start = _busy and _busy_action == "start"
    if _last_preview_jpeg and (_capture_active() or _capture_stopping() or busy_start):
        return _last_preview_jpeg, "image/jpeg"
    return None, "image/jpeg"


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="theme-color" content="#EEF4FF" />
  <title>EGO 采集</title>
  <style>
    :root {
      --bg: #f4f3ef;
      --text: #1a1a18;
      --muted: #6b6b66;
      --idle-bg: #EEF4FF;
      --idle-text: #1E3A8A;
      --idle-border: #BFDBFE;
      --rec: #1f8a4c;
      --err: #c0392b;
      --warn: #b45309;
      --btn-idle: #2563eb;
      --btn-stop: #dc2626;
      --radius: 16px;
      --shadow: 0 8px 28px rgba(0,0,0,.12);
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
      padding: 12px 16px calc(20px + env(safe-area-inset-bottom));
      display: flex; flex-direction: column; gap: 16px;
    }
    .status {
      border-radius: var(--radius);
      padding: 0 16px;
      min-height: 96px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      box-shadow: var(--shadow);
      transition: background .25s, color .25s;
    }
    .status[data-state="idle"] {
      background: var(--idle-bg);
      color: var(--idle-text);
      border: 1px solid var(--idle-border);
    }
    .status[data-state="recording"] { background: var(--rec); color: #fff; padding: 18px 16px; }
    .status[data-state="starting"],
    .status[data-state="warming"],
    .status[data-state="stopping"] {
      background: #b8860b;
      color: #fff;
      padding: 18px 16px;
    }
    .status[data-state="error"] { background: var(--err); color: #fff; padding: 18px 16px; }
    .status-title {
      font-size: 1.5rem;
      font-weight: 700;
      letter-spacing: .04em;
      line-height: 1.3;
      width: 100%;
      margin: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
    }
    .status[data-state="idle"] .status-title {
      font-size: 1.6rem;
      line-height: 1.25;
      padding: 0;
    }
  /* 待机无副文案：主标题在条内垂直正中 */
    .status[data-state="idle"].status--solo .status-title {
      min-height: 96px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .status-sub {
      margin: 0;
      padding: 0 0 14px;
      font-size: .95rem;
      width: 100%;
    }
    .status-sub:empty {
      display: none;
      min-height: 0;
      padding: 0;
    }
    .status[data-state="idle"] .status-sub:not(:empty) {
      color: #3B5B9A;
      opacity: .9;
      padding-bottom: 16px;
    }
    .status:not([data-state="idle"]) .status-sub {
      margin-top: 6px;
      padding-bottom: 14px;
      opacity: .95;
    }
    .spinner {
      display: inline-block; width: 18px; height: 18px;
      border: 2px solid rgba(255,255,255,.35);
      border-top-color: #fff; border-radius: 50%;
      animation: spin .8s linear infinite;
      vertical-align: -3px; margin-right: 6px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .preview-wrap {
      background: #111; border-radius: var(--radius);
      overflow: hidden; aspect-ratio: 4/3;
      position: relative; box-shadow: var(--shadow);
    }
    .preview-wrap img {
      width: 100%; height: 100%; object-fit: cover; display: block;
      background: #222;
    }
    .preview-placeholder {
      position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center;
      color: #aaa; font-size: .95rem; padding: 16px; text-align: center;
    }

    .btn-main {
      width: 100%; min-height: 64px;
      border: none; border-radius: 999px;
      font-size: 1.35rem; font-weight: 700;
      color: #fff; cursor: pointer;
      box-shadow: var(--shadow);
      transition: transform .1s, opacity .2s, background .2s;
      -webkit-tap-highlight-color: transparent;
      touch-action: manipulation;
    }
    .btn-main:active:not(:disabled) { transform: scale(.98); }
    .btn-main:disabled { opacity: .55; cursor: not-allowed; }
    .btn-main[data-mode="start"] { background: var(--btn-idle); }
    .btn-main[data-mode="stop"] { background: var(--btn-stop); }

    .info {
      display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
    }
    .info-card {
      background: #fff; border-radius: 12px; padding: 12px 14px;
      box-shadow: 0 2px 10px rgba(0,0,0,.06);
    }
    .info-label { font-size: .78rem; color: var(--muted); }
    .info-value { font-size: 1.1rem; font-weight: 600; margin-top: 4px; }
    .info-value.warn { color: var(--warn); }

    .hint {
      font-size: .8rem; color: var(--muted); text-align: center; line-height: 1.5;
      padding: 0 8px;
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="status" id="statusBar" data-state="idle">
      <div class="status-title" id="statusTitle">
        <span class="spinner" id="statusSpinner" hidden></span><span id="statusTitleText">设备待机中</span>
      </div>
      <div class="status-sub" id="statusSub"></div>
    </header>

    <section class="preview-wrap" aria-label="实时预览">
      <img id="previewImg" alt="" hidden />
      <div class="preview-placeholder" id="previewPh">开始录制后显示实时画面</div>
    </section>

    <button type="button" class="btn-main" id="mainBtn" data-mode="start">开始录制</button>

    <section class="info">
      <div class="info-card">
        <div class="info-label">磁盘剩余</div>
        <div class="info-value" id="storageFree">—</div>
      </div>
      <div class="info-card">
        <div class="info-label">已生成段数</div>
        <div class="info-value" id="segmentCount">0</div>
      </div>
    </section>

    <p class="hint">结束录制前请保持与本设备的 WiFi 连接。<br />录制过程中手机断开不影响数据写入。</p>
  </div>
  <script>
(function () {
  var statusBar = document.getElementById("statusBar");
  var statusTitle = document.getElementById("statusTitle");
  var statusSpinner = document.getElementById("statusSpinner");
  var statusTitleText = document.getElementById("statusTitleText");
  var statusSub = document.getElementById("statusSub");
  var mainBtn = document.getElementById("mainBtn");
  var previewImg = document.getElementById("previewImg");
  var previewPh = document.getElementById("previewPh");
  var storageFree = document.getElementById("storageFree");
  var segmentCount = document.getElementById("segmentCount");

  var pollTimer = null;
  var previewTimer = null;
  var previewPollMs = __PREVIEW_POLL_MS__;
  var actionInFlight = false;
  var previewWantLive = false;
  var previewHasFrame = false;

  function formatDuration(sec) {
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  function setStatusTitle(showSpinner, text) {
    statusSpinner.hidden = !showSpinner;
    statusTitleText.textContent = text;
  }

  function applyStatus(data) {
    var st = data.state || "idle";
    statusBar.setAttribute("data-state", st);

    if (st === "idle") {
      setStatusTitle(false, "设备待机中");
      statusSub.textContent = data.msg || "";
      mainBtn.textContent = "开始录制";
      mainBtn.setAttribute("data-mode", "start");
      if (data.msg) {
        statusBar.classList.remove("status--solo");
      } else {
        statusBar.classList.add("status--solo");
      }
      stopIdlePreview();
      stopPreview();
      previewHasFrame = false;
      showPreviewPlaceholder("开始录制后显示实时画面");
    } else if (st === "warming") {
      statusBar.classList.remove("status--solo");
      setStatusTitle(true, "相机初始化中");
      statusSub.textContent = data.msg || "开始写入数据后计时";
      mainBtn.textContent = "结束录制";
      mainBtn.setAttribute("data-mode", "stop");
    } else if (st === "recording") {
      statusBar.classList.remove("status--solo");
      setStatusTitle(false, "正在录制中");
      statusSub.textContent = formatDuration(data.duration || 0);
      mainBtn.textContent = "结束录制";
      mainBtn.setAttribute("data-mode", "stop");
    } else if (st === "starting") {
      statusBar.classList.remove("status--solo");
      setStatusTitle(true, "正在启动相机");
      statusSub.textContent = data.msg || "请稍候，约需数秒";
      mainBtn.disabled = true;
      stopIdlePreview();
      startPreview();
    } else if (st === "stopping") {
      statusBar.classList.remove("status--solo");
      setStatusTitle(true, "正在保存数据");
      statusSub.textContent = data.msg || "请勿断电";
      mainBtn.textContent = "保存中…";
      mainBtn.setAttribute("data-mode", "saving");
      mainBtn.disabled = true;
      stopIdlePreview();
      stopPreview();
      showPreviewPlaceholder("正在写入磁盘，请勿断电…");
    } else if (st === "error") {
      statusBar.classList.remove("status--solo");
      setStatusTitle(false, "出现问题");
      statusSub.textContent = data.msg || "请稍后重试";
      mainBtn.textContent = data.capture_active ? "结束录制" : "开始录制";
      mainBtn.setAttribute("data-mode", data.capture_active ? "stop" : "start");
    }

    if (st !== "starting" && st !== "stopping" && !actionInFlight) {
      mainBtn.disabled = false;
    }

    storageFree.textContent = data.storage_free || "—";
    storageFree.className = "info-value" + (data.storage_warn ? " warn" : "");
    segmentCount.textContent = String(data.segment_count != null ? data.segment_count : 0);

    if (st === "recording" || st === "warming") {
      stopIdlePreview();
      startPreview();
    } else if (st !== "starting") {
      stopPreview();
      if (st !== "idle" && st !== "stopping") stopIdlePreview();
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
      });
  }

  function showPreviewPlaceholder(text) {
    previewPh.textContent = text;
    previewPh.hidden = false;
    if (!previewHasFrame) {
      previewImg.hidden = true;
    }
  }

  function tickPreview() {
    previewImg.src = "/api/preview/main.jpg?t=" + Date.now();
  }

  previewImg.addEventListener("load", function () {
    previewHasFrame = true;
    previewImg.hidden = false;
    previewPh.hidden = true;
  });

  previewImg.addEventListener("error", function () {
    if (previewWantLive) {
      if (!previewHasFrame) {
        showPreviewPlaceholder("预览连接中…");
      } else {
        previewPh.hidden = true;
      }
    } else if (!previewHasFrame) {
      showPreviewPlaceholder("开始录制后显示实时画面");
    }
  });

  function startPreview() {
    previewWantLive = true;
    if (previewTimer) return;
    tickPreview();
    previewTimer = setInterval(tickPreview, previewPollMs);
  }

  function stopPreview() {
    previewWantLive = false;
    if (previewTimer) {
      clearInterval(previewTimer);
      previewTimer = null;
    }
    previewHasFrame = false;
    previewImg.hidden = true;
    previewPh.hidden = false;
    previewPh.textContent = "开始录制后显示实时画面";
    previewImg.removeAttribute("src");
  }

  function stopIdlePreview() {
    /* no-op: idle standby preview disabled */
  }

  function doAction(path) {
    if (actionInFlight) return;
    actionInFlight = true;
    mainBtn.disabled = true;
    if (path === "/api/capture/stop") {
      statusBar.setAttribute("data-state", "stopping");
      setStatusTitle(true, "正在保存数据");
      statusSub.textContent = "请勿断电";
      stopIdlePreview();
      stopPreview();
    } else if (path === "/api/capture/start") {
      statusBar.setAttribute("data-state", "starting");
      setStatusTitle(true, "正在启动相机");
      statusSub.textContent = "请稍候，约需数秒";
      stopIdlePreview();
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
      .finally(function () {
        actionInFlight = false;
      });
  }

  mainBtn.addEventListener("click", function () {
    var mode = mainBtn.getAttribute("data-mode");
    if (mode === "saving") return;
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
            body = INDEX_HTML.replace("__STATUS_POLL_MS__", str(STATUS_POLL_MS)).replace(
                "__PREVIEW_POLL_MS__", str(PREVIEW_POLL_MS)
            ).encode("utf-8")
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
            ok, msg = _run_capture_action("start")
            _json_response(
                self,
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
                {"success": ok, "msg": msg},
            )
            return

        if path == "/api/capture/stop":
            ok, msg = _run_capture_action("stop")
            _json_response(
                self,
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
                {"success": ok, "msg": msg},
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
