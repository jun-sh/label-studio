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
    os.environ.get(
        "EGO_SEGMENT_ROOT",
        f"/home/server/cache/{os.environ.get('EGO_STATION_ID', 'ego-001').strip() or 'ego-001'}/segments",
    )
)
CHECKPOINT_PATH = Path(
    os.environ.get(
        "EGO_CAPTURE_CHECKPOINT",
        f"/home/server/cache/{os.environ.get('EGO_STATION_ID', 'ego-001').strip() or 'ego-001'}/segments/checkpoint.json",
    )
)
STATIC_ROOT = Path(__file__).resolve().parent / "static"
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
_last_completed_action: str | None = None
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


def _wait_for_stop_release(timeout_s: float | None = None) -> bool:
    """Block start until an in-flight stop finishes (rapid stop→start UX)."""
    deadline = time.monotonic() + (timeout_s if timeout_s is not None else STOP_TIMEOUT_S + 15.0)
    while time.monotonic() < deadline:
        with _lock:
            if not _busy:
                return True
            if _busy_action != "stop":
                return False
        time.sleep(0.2)
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


def _session_disk_root(session_id: str) -> Path:
    return SEGMENT_ROOT / "sessions" / session_id


def _session_shm_root(session_id: str) -> Path:
    return SEGMENT_ACTIVE_ROOT / "sessions" / session_id


def _session_has_uploaded_segments(session_id: str) -> bool:
    seg_root = _session_disk_root(session_id) / "segments"
    if not seg_root.is_dir():
        return False
    for seg in seg_root.iterdir():
        if not seg.is_dir() or not seg.name.startswith("seg_"):
            continue
        manifest = seg / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("uploaded"):
            return True
    return False


def _delete_session_data(session_id: str) -> None:
    if not session_id or not session_id.startswith("sess_"):
        return
    for root in (_session_shm_root(session_id), _session_disk_root(session_id)):
        if root.is_dir():
            shutil.rmtree(root)


def _session_has_segment_dirs(session_id: str) -> bool:
    seg_root = _session_disk_root(session_id) / "segments"
    if seg_root.is_dir():
        for seg in seg_root.iterdir():
            if seg.is_dir() and seg.name.startswith("seg_"):
                return True
    shm_seg = _session_shm_root(session_id) / "segments"
    if shm_seg.is_dir():
        for seg in shm_seg.iterdir():
            if seg.is_dir() and seg.name.startswith("seg_"):
                return True
    return False


def _session_is_completely_empty(session_id: str) -> bool:
    for root in (_session_disk_root(session_id), _session_shm_root(session_id)):
        if not root.is_dir():
            continue
        if any(p.is_file() for p in root.rglob("*")):
            return False
    return True


def _cleanup_orphan_session(session_id: str, reason: str) -> None:
    if not session_id:
        return
    _delete_session_data(session_id)
    print(f"capture_session_cleanup session_id={session_id} reason={reason}", flush=True)


_prune_sessions_last_mono = 0.0
PRUNE_SESSIONS_INTERVAL_S = float(os.environ.get("EGO_PRUNE_SESSIONS_INTERVAL_S", "30"))


def _maybe_prune_stale_session_dirs() -> None:
    """Drop sess_* dirs with no seg_* on disk (empty or intrinsics-only leftovers)."""
    global _prune_sessions_last_mono
    if _capture_active() or _capture_stopping():
        with _lock:
            if _busy:
                return
    rec = _capture_unit_state(CAPTURE_RECORD_UNIT)
    if rec not in ("failed", "inactive", ""):
        return
    now = time.monotonic()
    if now - _prune_sessions_last_mono < PRUNE_SESSIONS_INTERVAL_S:
        return
    _prune_sessions_last_mono = now

    sessions_root = SEGMENT_ROOT / "sessions"
    if not sessions_root.is_dir():
        return

    for sess in sessions_root.iterdir():
        if not sess.is_dir() or not sess.name.startswith("sess_"):
            continue
        session_id = sess.name
        if _session_has_segment_dirs(session_id):
            continue
        if _session_has_uploaded_segments(session_id):
            continue
        _cleanup_orphan_session(session_id, "no_segments_on_disk")


def _fail_start_capture(session_id: str, msg: str) -> tuple[bool, str]:
    global _last_error
    _last_error = msg
    _ensure_capture_fully_stopped(timeout_s=30.0)
    _cleanup_orphan_session(session_id, "start_failed")
    _start_standby_preview()
    return False, msg


def _log_abandon(session_id: str) -> None:
    log_dir = SEGMENT_ROOT.parent / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        payload = {
            "sessionId": session_id,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": "abandon",
        }
        (log_dir / f"abandon-{stamp}-{session_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        print(f"capture_abandon session_id={session_id}", flush=True)


def _stop_capture_wait() -> bool:
    """Stop capture stack and wait until the record unit is idle."""
    global _last_error
    if not _capture_active():
        return True
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
        return False
    _start_standby_preview()
    return True


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
    elif busy and busy_action == "abandon":
        state = "stopping"
        msg = "正在放弃录制，请稍候…"
    elif busy and busy_action == "stop":
        state = "stopping"
        rec = _capture_unit_state(CAPTURE_RECORD_UNIT)
        if rec == "active" and frames_writing:
            msg = "正在停止采集，请稍候…"
        elif rec == "deactivating":
            msg = "正在写入磁盘，请勿断电"
        else:
            msg = "正在保存数据，请勿断电…"
    elif active and frames_writing:
        state = "recording"
        msg = err if err else ""
    elif active:
        state = "warming"
        msg = err or "正在准备录制，相机初始化中…"
    elif err:
        state = "error"
        msg = err
    else:
        state = "idle"
        msg = ""
        _last_error = ""
        _maybe_prune_stale_session_dirs()

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
    global _busy, _busy_action, _last_error, _last_action_mono, _last_completed_action

    now = time.monotonic()
    if action == "start":
        with _lock:
            if _busy and _busy_action != "stop":
                return False, "操作进行中，请稍候"
        if not _wait_for_stop_release():
            return False, "上一段仍在保存，请稍后再试"

    with _lock:
        if _busy:
            return False, "操作进行中，请稍候"
        if (
            now - _last_action_mono < MIN_ACTION_INTERVAL_S
            and _last_completed_action == action
        ):
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
            session_id = _begin_new_capture_session()
            proc = _systemctl("start", CAPTURE_TARGET, timeout=START_TIMEOUT_S)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "启动失败").strip()
                return _fail_start_capture(session_id, f"无法启动采集：{detail}")
            deadline = time.monotonic() + START_TIMEOUT_S
            while time.monotonic() < deadline:
                if _capture_active():
                    return True, ""
                time.sleep(0.5)
            _systemctl("stop", CAPTURE_TARGET, timeout=30)
            return _fail_start_capture(
                session_id,
                "相机启动超时，请检查 OAK 设备是否连接",
            )

        if action == "stop":
            if not _stop_capture_wait():
                return False, _last_error
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
            if action in ("start", "stop", "abandon"):
                _last_completed_action = action


def _run_capture_abandon() -> tuple[bool, str]:
    global _busy, _busy_action, _last_error, _last_action_mono, _last_completed_action

    if not _capture_active():
        return False, "当前未在录制"

    checkpoint = _read_checkpoint()
    session_id = str(checkpoint.get("sessionId") or "").strip()
    if session_id and _session_has_uploaded_segments(session_id):
        return False, "本场已有已上传段，无法放弃"

    now = time.monotonic()
    with _lock:
        if _busy:
            return False, "操作进行中，请稍候"
        if (
            now - _last_action_mono < MIN_ACTION_INTERVAL_S
            and _last_completed_action == "abandon"
        ):
            return False, "操作过快，请稍后再试"
        _busy = True
        _busy_action = "abandon"
        _last_error = ""

    try:
        if not _stop_capture_wait():
            return False, _last_error
        if session_id:
            _delete_session_data(session_id)
            _log_abandon(session_id)
        return True, ""
    except Exception as exc:
        _last_error = f"操作异常：{exc}"
        return False, _last_error
    finally:
        with _lock:
            _busy = False
            _busy_action = None
            _last_action_mono = time.monotonic()
            _last_completed_action = "abandon"


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
  <meta name="theme-color" content="#f3f4f6" />
  <title>EGO 采集</title>
  <style>
    :root {
      --bg: #f3f4f6;
      --card: #fff;
      --text: #111827;
      --muted: #6b7280;
      --blue: #2563eb;
      --blue-soft: #eef4ff;
      --green: #16a34a;
      --orange: #f59e0b;
      --err: #dc2626;
      --warn: #d97706;
      --radius: 16px;
      --shadow: 0 2px 12px rgba(15, 23, 42, .08);
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0;
      font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg); color: var(--text);
      min-height: 100dvh;
    }
    .app {
      max-width: 420px; margin: 0 auto;
      padding: 14px 16px calc(24px + env(safe-area-inset-bottom));
      display: flex; flex-direction: column; gap: 14px;
    }
    .card {
      background: var(--card);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .status-card {
      padding: 16px 18px;
      display: flex;
      align-items: flex-start;
      gap: 12px;
    }
    .status-dot {
      width: 10px; height: 10px; border-radius: 50%;
      margin-top: 6px; flex-shrink: 0;
      background: var(--orange);
      transition: background .25s;
    }
    .status-card[data-link="live"] .status-dot { background: var(--green); }
    .status-card[data-link="warming"] .status-dot {
      background: var(--orange);
      animation: pulse 1.2s ease infinite;
    }
    .status-card[data-link="busy"] .status-dot { background: var(--orange); animation: pulse 1.2s ease infinite; }
    .status-card[data-link="error"] .status-dot { background: var(--err); }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }
    .status-body { flex: 1; min-width: 0; }
    .status-title {
      font-size: 1.125rem; font-weight: 700; line-height: 1.35;
      display: flex; align-items: center; gap: 8px;
    }
    .status-sub {
      margin: 4px 0 0; font-size: .875rem; color: var(--muted); line-height: 1.4;
    }
    .status-sub.live { color: var(--green); font-weight: 600; }
    .status-sub.warming { color: var(--warn); font-weight: 600; }
    .status-sub.err { color: var(--err); }
    .status-wifi {
      flex-shrink: 0; width: 28px; height: 28px;
      color: #9ca3af; transition: color .25s;
    }
    .status-card[data-link="live"] .status-wifi { color: var(--blue); }
    .spinner {
      display: inline-block; width: 16px; height: 16px;
      border: 2px solid #d1d5db; border-top-color: var(--blue);
      border-radius: 50%; animation: spin .75s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .preview-wrap {
      background: var(--blue-soft);
      border-radius: var(--radius);
      overflow: hidden;
      aspect-ratio: 16 / 10;
      position: relative;
      box-shadow: var(--shadow);
    }
    .preview-wrap img {
      width: 100%; height: 100%; object-fit: cover; display: block;
      background: var(--blue-soft);
    }
    .preview-placeholder {
      position: absolute; inset: 0;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      gap: 10px; padding: 20px; text-align: center;
      color: #64748b;
    }
    .preview-placeholder svg { opacity: .55; }
    .preview-placeholder span { font-size: .85rem; line-height: 1.45; max-width: 220px; }
    .preview-overlay {
      position: absolute; z-index: 2;
      background: rgba(255,255,255,.92);
      border-radius: 8px;
      padding: 6px 10px;
      font-size: .8rem; font-weight: 600;
      color: var(--text);
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
    }
    .preview-overlay.top-left { top: 12px; left: 12px; font-variant-numeric: tabular-nums; }
    .preview-overlay.top-right {
      top: 12px; right: 12px;
      display: flex; align-items: center; gap: 6px;
      font-size: .75rem; font-weight: 700;
    }
    .rec-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--err);
      animation: recblink 1s step-end infinite;
    }
    @keyframes recblink { 50% { opacity: .2; } }

    .btn-main {
      width: 100%; min-height: 56px;
      border: none; border-radius: 999px;
      font-size: 1.125rem; font-weight: 700;
      color: #fff; cursor: pointer;
      box-shadow: 0 4px 14px rgba(37, 99, 235, .35);
      display: flex; align-items: center; justify-content: center; gap: 10px;
      transition: transform .1s, opacity .2s, background .2s, box-shadow .2s;
      -webkit-tap-highlight-color: transparent;
      touch-action: manipulation;
    }
    .btn-main:active:not(:disabled) { transform: scale(.98); }
    .btn-main:disabled { opacity: .55; cursor: not-allowed; box-shadow: none; }
    .btn-main[data-mode="start"] { background: var(--blue); }
    .btn-main[data-mode="stop"] {
      background: var(--err);
      box-shadow: 0 4px 14px rgba(220, 38, 38, .35);
    }
    .btn-icon { width: 22px; height: 22px; flex-shrink: 0; }

    .info {
      display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
    }
    .info-card {
      background: var(--card); border-radius: var(--radius);
      padding: 14px 16px; box-shadow: var(--shadow);
    }
    .info-label { font-size: .8rem; color: var(--muted); }
    .info-value {
      font-size: 1.65rem; font-weight: 700; margin-top: 6px;
      color: var(--blue); font-variant-numeric: tabular-nums;
    }
    .info-value.warn { color: var(--warn); }

    .hints { display: flex; flex-direction: column; gap: 8px; padding: 4px 6px 0; }
    .hint-row {
      display: flex; align-items: flex-start; gap: 8px;
      font-size: .78rem; color: var(--muted); line-height: 1.45;
    }
    .hint-row svg { flex-shrink: 0; margin-top: 1px; color: #9ca3af; }
  </style>
</head>
<body>
  <div class="app">
    <header class="card status-card" id="statusCard" data-link="idle">
      <span class="status-dot" id="statusDot" aria-hidden="true"></span>
      <div class="status-body">
        <div class="status-title" id="statusTitle">
          <span class="spinner" id="statusSpinner" hidden></span>
          <span id="statusTitleText">设备待机中</span>
        </div>
        <p class="status-sub" id="statusSub">等待连接</p>
      </div>
      <svg class="status-wifi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <path d="M5 12.55a11 11 0 0 1 14 0"/><path d="M8.5 16.42a6 6 0 0 1 7 0"/><path d="M12 20h.01"/>
      </svg>
    </header>

    <section class="preview-wrap" aria-label="实时预览">
      <img id="previewImg" alt="" hidden />
      <div class="preview-placeholder" id="previewPh">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M15 10l4.553-2.276A1 1 0 0 1 21 8.618v6.764a1 1 0 0 1-1.447.894L15 14M5 18h8a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2z"/>
        </svg>
        <span>开始录制后显示实时画面</span>
      </div>
      <div class="preview-overlay top-left" id="previewTimer" hidden>00:00:00</div>
      <div class="preview-overlay top-right" id="previewRec" hidden>
        <span class="rec-dot"></span>REC
      </div>
    </section>

    <button type="button" class="btn-main" id="mainBtn" data-mode="start">
      <svg class="btn-icon" id="btnIconStart" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M8 5v14l11-7z"/>
      </svg>
      <svg class="btn-icon" id="btnIconStop" viewBox="0 0 24 24" fill="currentColor" hidden aria-hidden="true">
        <rect x="6" y="6" width="12" height="12" rx="1"/>
      </svg>
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
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M5 12.55a11 11 0 0 1 14 0"/><path d="M8.5 16.42a6 6 0 0 1 7 0"/><path d="M12 20h.01"/>
        </svg>
        <span>录制前请保持 WiFi 连接</span>
      </div>
      <div class="hint-row">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
        </svg>
        <span>手机断开不影响数据写入</span>
      </div>
    </div>
  </div>
  <script>
(function () {
  var statusCard = document.getElementById("statusCard");
  var statusSpinner = document.getElementById("statusSpinner");
  var statusTitleText = document.getElementById("statusTitleText");
  var statusSub = document.getElementById("statusSub");
  var mainBtn = document.getElementById("mainBtn");
  var mainBtnLabel = document.getElementById("mainBtnLabel");
  var btnIconStart = document.getElementById("btnIconStart");
  var btnIconStop = document.getElementById("btnIconStop");
  var previewImg = document.getElementById("previewImg");
  var previewPh = document.getElementById("previewPh");
  var previewTimer = document.getElementById("previewTimer");
  var previewRec = document.getElementById("previewRec");
  var storageFree = document.getElementById("storageFree");
  var segmentCount = document.getElementById("segmentCount");

  var pollTimer = null;
  var previewPollTimer = null;
  var previewPollMs = __PREVIEW_POLL_MS__;
  var actionInFlight = false;
  var previewWantLive = false;
  var previewHasFrame = false;

  function formatDuration(sec) {
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    function pad2(n) { return (n < 10 ? "0" : "") + n; }
    return pad2(h) + ":" + pad2(m) + ":" + pad2(s);
  }

  function setBtnMode(mode, label, disabled) {
    mainBtn.setAttribute("data-mode", mode);
    mainBtnLabel.textContent = label;
    btnIconStart.hidden = mode !== "start";
    btnIconStop.hidden = mode !== "stop";
    if (typeof disabled === "boolean") mainBtn.disabled = disabled;
  }

  function setStatusLink(link) {
    statusCard.setAttribute("data-link", link);
  }

  function setHeader(title, sub, opts) {
    opts = opts || {};
    statusSpinner.hidden = !opts.spinner;
    statusTitleText.textContent = title;
    statusSub.textContent = sub || "";
    statusSub.className = "status-sub" + (opts.subClass ? " " + opts.subClass : "");
  }

  function updatePreviewOverlays(st, data) {
    var showRec = st === "recording" && (data.frames_writing || (data.duration || 0) > 0);
    var showTimer = showRec;
    previewTimer.hidden = !showTimer;
    previewRec.hidden = !showRec;
    if (showTimer) {
      previewTimer.textContent = formatDuration(data.duration || 0);
    }
  }

  function applyStatus(data) {
    var st = data.state || "idle";

    if (st === "idle") {
      setStatusLink("idle");
      setHeader("设备待机中", data.msg || "等待连接", { subClass: data.msg ? "err" : "" });
      setBtnMode("start", "开始录制", false);
      stopPreview();
      previewHasFrame = false;
      showPreviewPlaceholder("开始录制后显示实时画面");
      updatePreviewOverlays(st, data);
    } else if (st === "warming") {
      setStatusLink("warming");
      setHeader(
        "设备已连接",
        data.msg || "正在准备录制，相机初始化中…",
        { subClass: "warming", spinner: true }
      );
      setBtnMode("stop", "结束录制", false);
      updatePreviewOverlays(st, data);
      startPreview();
    } else if (st === "recording") {
      setStatusLink("live");
      setHeader("设备已连接", "正在录制中", { subClass: "live" });
      setBtnMode("stop", "结束录制", false);
      updatePreviewOverlays(st, data);
      startPreview();
    } else if (st === "starting") {
      setStatusLink("busy");
      setHeader("设备连接中", data.msg || "正在启动相机…", { spinner: true });
      setBtnMode("start", "启动中…", true);
      stopPreview();
      updatePreviewOverlays(st, data);
    } else if (st === "stopping") {
      setStatusLink("busy");
      setHeader("正在保存数据", data.msg || "请勿断电", { spinner: true });
      setBtnMode("stop", "保存中…", true);
      stopPreview();
      showPreviewPlaceholder("正在写入磁盘，请勿断电…");
      updatePreviewOverlays(st, data);
    } else if (st === "error") {
      setStatusLink("error");
      setHeader("出现问题", data.msg || "请稍后重试", { subClass: "err" });
      if (data.capture_active) {
        setBtnMode("stop", "结束录制", false);
      } else {
        setBtnMode("start", "开始录制", false);
      }
      stopPreview();
      updatePreviewOverlays(st, data);
    }

    if (st !== "starting" && st !== "stopping" && !actionInFlight) {
      mainBtn.disabled = false;
    }

    storageFree.textContent = data.storage_free || "—";
    storageFree.className = "info-value" + (data.storage_warn ? " warn" : "");
    var seg = data.segment_count != null ? data.segment_count : 0;
    segmentCount.textContent = String(seg) + " 段";

    if (st === "recording" || st === "warming") {
      startPreview();
    } else if (st !== "starting") {
      stopPreview();
    }
  }

  function fetchStatus() {
    return fetch("/api/status", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(applyStatus)
      .catch(function () {
        setStatusLink("error");
        setHeader("无法连接设备", "请确认已连接采集热点", { subClass: "err" });
        setBtnMode("start", "开始录制", false);
      });
  }

  function showPreviewPlaceholder(text) {
    previewPh.querySelector("span").textContent = text;
    previewPh.hidden = false;
    if (!previewHasFrame) previewImg.hidden = true;
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
      if (!previewHasFrame) showPreviewPlaceholder("预览连接中…");
    } else if (!previewHasFrame) {
      showPreviewPlaceholder("开始录制后显示实时画面");
    }
  });

  function startPreview() {
    previewWantLive = true;
    if (previewPollTimer) return;
    tickPreview();
    previewPollTimer = setInterval(tickPreview, previewPollMs);
  }

  function stopPreview() {
    previewWantLive = false;
    if (previewPollTimer) {
      clearInterval(previewPollTimer);
      previewPollTimer = null;
    }
    previewHasFrame = false;
    previewImg.hidden = true;
    previewPh.hidden = false;
    previewPh.querySelector("span").textContent = "开始录制后显示实时画面";
    previewImg.removeAttribute("src");
  }

  function doAction(path) {
    if (actionInFlight) return;
    actionInFlight = true;
    mainBtn.disabled = true;
    if (path === "/api/capture/stop") {
      setStatusLink("busy");
      setHeader("正在保存数据", "请勿断电", { spinner: true });
      setBtnMode("stop", "保存中…", true);
      stopPreview();
    } else if (path === "/api/capture/start") {
      setStatusLink("busy");
      setHeader("设备连接中", "正在启动相机…", { spinner: true });
      setBtnMode("start", "启动中…", true);
    }
    fetch(path, { method: "POST", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success && res.msg) {
          setStatusLink("error");
          setHeader("操作失败", res.msg, { subClass: "err" });
        }
        return fetchStatus();
      })
      .finally(function () {
        actionInFlight = false;
      });
  }

  mainBtn.addEventListener("click", function () {
    var mode = mainBtn.getAttribute("data-mode");
    if (mode === "stop") {
      doAction("/api/capture/stop");
    } else if (mode === "start") {
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


def _index_html_body() -> bytes:
    tpl = Path(__file__).resolve().parent / "templates" / "capture_ui.html"
    if tpl.is_file():
        html = tpl.read_text(encoding="utf-8")
    else:
        html = INDEX_HTML
    html = html.replace("__STATUS_POLL_MS__", str(STATUS_POLL_MS)).replace(
        "__PREVIEW_POLL_MS__", str(PREVIEW_POLL_MS)
    )
    return html.encode("utf-8")


_STATIC_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}


def _serve_static_file(handler: BaseHTTPRequestHandler, rel_path: str) -> bool:
    if not rel_path or ".." in rel_path.split("/"):
        return False
    fp = STATIC_ROOT / rel_path
    if not fp.is_file():
        return False
    data = fp.read_bytes()
    ctype = _STATIC_MIME.get(fp.suffix.lower(), "application/octet-stream")
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "public, max-age=86400")
    handler.end_headers()
    handler.wfile.write(data)
    return True


class EgoWebHandler(BaseHTTPRequestHandler):
    server_version = "ego-web/2.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = _index_html_body()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/static/"):
            if _serve_static_file(self, path[len("/static/") :]):
                return
            self.send_error(HTTPStatus.NOT_FOUND)
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

        if path == "/api/capture/abandon":
            ok, msg = _run_capture_abandon()
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
