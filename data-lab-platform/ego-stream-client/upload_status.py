"""Structured upload observability for ecs-upload-loop (P0 commercial delivery)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REASON_LABELS_ZH: dict[str, str] = {
    "missing_manifest": "缺失清单文件（数据不完整，已隔离）",
    "upload_timeout": "上传超时",
    "upload_fail": "上传失败",
    "pending_cap": "本地队列超限已丢弃",
    "disk_quota": "磁盘配额超限已丢弃",
}

PHASE_LABELS_ZH: dict[str, str] = {
    "idle": "空闲（无待传段）",
    "uploading": "传输中",
    "no_session": "未找到采集会话",
    "stopped": "服务未运行",
}


def default_status_path() -> Path:
    explicit = os.environ.get("EGO_UPLOAD_STATUS_PATH", "").strip()
    if explicit:
        return Path(explicit)
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return Path(runtime) / "ego-upload-status.json"
    return Path.home() / ".local" / "state" / "ego-upload-status.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _elapsed_since(iso: str | None) -> int | None:
    start = _parse_iso(iso)
    if start is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - start).total_seconds()))


def _progress_bar(completed: int, total: int, *, width: int = 22) -> str:
    if total <= 0:
        return f"[{'░' * width}] 0%"
    pct = min(100, round((completed / total) * 100))
    filled = int(width * pct / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct}%"


def _progress_bar_chars(completed: int, total: int, *, width: int = 22) -> str:
    if total <= 0:
        return f"[{'░' * width}]"
    pct = min(100, round((completed / total) * 100))
    filled = int(width * pct / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def _short_session_id(session_id: str) -> str:
    sid = (session_id or "—").strip()
    if len(sid) > 16:
        return f"{sid[:8]}…{sid[-8:]}"
    return sid


def _progress_summary_line(
    completed: int,
    total: int,
    speed_bps: float,
    *,
    eta: Any = None,
    pending: int = 0,
    width: int = 22,
) -> str:
    if total <= 0:
        return f"[{'░' * width}] 0% · 0/0 段"
    pct = min(100, round((completed / total) * 100))
    bar = _progress_bar_chars(completed, total, width=width)
    parts = [f"{bar} {pct}%", f"{completed}/{total} 段"]
    speed_str = _format_speed_mb_s(speed_bps)
    if speed_str:
        parts.append(f"速度：{speed_str}")
    eta_str = _format_eta_zh(eta, pending)
    if eta_str:
        parts.append(eta_str)
    return " · ".join(parts)


def _format_eta_zh(eta: Any, pending: int) -> str:
    if pending <= 0 or not isinstance(eta, int) or eta <= 0:
        return ""
    if eta < 90:
        return f"剩余 ~{eta}s"
    mins = max(1, round(eta / 60))
    return f"剩余 ~{mins} 分钟"


def _current_segment_phase(elapsed: int | None, speed_bps: float) -> str:
    speed_str = _format_speed_mb_s(speed_bps)
    if speed_str:
        return speed_str
    if elapsed is not None and elapsed < 4:
        return "校验 SHA256"
    return "上传中"


def _format_speed_mb_s(speed_bps: float) -> str:
    if speed_bps <= 0:
        return ""
    return f"{speed_bps / 1_000_000:.1f} MB/s"


# Ignore bogus samples from duplicate skips (full archive size / near-zero wall time).
_MAX_SPEED_BPS = 250_000_000  # 250 MB/s ≈ 2 Gbps


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_status(path: Path | str | None = None) -> dict[str, Any] | None:
    p = Path(path) if path else default_status_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class UploadStatusWriter:
    """Append-only event log + rolling metrics; safe for loop + subprocess uploads."""

    _singleton: UploadStatusWriter | None = None

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_status_path()
        self._speed_samples: list[float] = []
        self._avg_segment_bytes: float = 0.0
        self._completed_bytes: int = 0
        self._state: dict[str, Any] = self._empty_state()

    @classmethod
    def get_default(cls) -> UploadStatusWriter:
        if cls._singleton is None:
            cls._singleton = cls()
        return cls._singleton

    def _empty_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "updatedAt": _iso_now(),
            "service": {"running": True, "phase": "idle"},
            "sessionId": None,
            "progress": {
                "total": 0,
                "completed": 0,
                "pending": 0,
                "skipped": 0,
                "failed": 0,
                "percent": 0,
            },
            "current": None,
            "throughputBytesPerSec": 0.0,
            "etaSeconds": None,
            "skippedSegments": [],
            "recentEvents": [],
            "lastOk": None,
            "lastError": None,
        }

    def _load_or_init(self) -> dict[str, Any]:
        existing = read_status(self.path)
        if existing and isinstance(existing, dict):
            self._state = existing
            samples = existing.get("throughputSamples")
            if isinstance(samples, list):
                self._speed_samples = [float(x) for x in samples[-20:]]
            self._avg_segment_bytes = float(existing.get("avgSegmentBytes") or 0)
            return self._state
        self._state = self._empty_state()
        return self._state

    def _persist(self) -> None:
        st = self._state
        st["updatedAt"] = _iso_now()
        st["throughputSamples"] = self._speed_samples[-20:]
        st["avgSegmentBytes"] = self._avg_segment_bytes
        _atomic_write(self.path, st)

    def _push_event(self, event: dict[str, Any], *, limit: int = 30) -> None:
        events = self._state.setdefault("recentEvents", [])
        events.insert(0, event)
        del events[limit:]

    def _clear_error_if_recovered(self, segment_id: str) -> None:
        last_err = self._state.get("lastError")
        if isinstance(last_err, dict) and last_err.get("segmentId") == segment_id:
            self._state["lastError"] = None
            prog = self._state.setdefault("progress", {})
            prog["failed"] = max(0, int(prog.get("failed", 0)) - 1)

    def _clear_stale_error_if_idle(self) -> None:
        prog = self._state.get("progress") or {}
        pending = int(prog.get("pending", 0))
        failed = int(prog.get("failed", 0))
        phase = (self._state.get("service") or {}).get("phase", "idle")
        if pending <= 0 and failed <= 0 and phase == "idle" and not self._state.get("current"):
            self._state["lastError"] = None

    def mark_no_session(self) -> None:
        self._load_or_init()
        self._state["service"] = {"running": True, "phase": "no_session"}
        self._persist()

    def reset_session(self, session_id: str) -> None:
        self._load_or_init()
        self._speed_samples = []
        self._avg_segment_bytes = 0.0
        self._completed_bytes = 0
        self._state = self._empty_state()
        self._state["sessionId"] = session_id
        self._persist()

    def refresh_queue(
        self,
        *,
        session_id: str,
        pending: int,
        skipped_segments: list[dict[str, str]],
        phase: str = "idle",
    ) -> None:
        self._load_or_init()
        skipped = len(skipped_segments)
        completed = int(self._state.get("progress", {}).get("completed", 0))
        total = max(pending + completed + skipped, pending + completed)
        if total == 0 and pending == 0:
            total = skipped
        percent = round((completed / total) * 100) if total > 0 else (100 if pending == 0 else 0)

        self._state["sessionId"] = session_id
        self._state["service"] = {"running": True, "phase": phase}
        self._state["progress"] = {
            "total": total,
            "completed": completed,
            "pending": pending,
            "skipped": skipped,
            "failed": int(self._state.get("progress", {}).get("failed", 0)),
            "percent": min(100, percent),
        }
        self._state["skippedSegments"] = skipped_segments
        self._update_eta(pending)
        self._clear_stale_error_if_idle()
        self._persist()

    def set_uploading(
        self,
        *,
        session_id: str,
        segment_id: str,
        bytes_total: int,
    ) -> None:
        self._load_or_init()
        self._state["sessionId"] = session_id
        self._state["service"] = {"running": True, "phase": "uploading"}
        self._state["current"] = {
            "segmentId": segment_id,
            "bytes": bytes_total,
            "startedAt": _iso_now(),
        }
        self._persist()

    def clear_current(self) -> None:
        self._load_or_init()
        self._state["current"] = None
        if self._state.get("progress", {}).get("pending", 0) > 0:
            self._state["service"] = {"running": True, "phase": "uploading"}
        else:
            self._state["service"] = {"running": True, "phase": "idle"}
        self._persist()

    def record_ok(
        self,
        *,
        session_id: str,
        segment_id: str,
        bytes_total: int,
        elapsed_s: float,
        duplicate: bool,
    ) -> str:
        self._load_or_init()
        speed = bytes_total / elapsed_s if elapsed_s > 0 and bytes_total > 0 else 0.0
        if speed > 0 and not duplicate and speed <= _MAX_SPEED_BPS:
            self._speed_samples.append(speed)
            self._speed_samples = self._speed_samples[-20:]
        if bytes_total > 0 and not duplicate:
            self._completed_bytes += bytes_total
            n = int(self._state.get("progress", {}).get("completed", 0)) + 1
            prev_avg = self._avg_segment_bytes
            self._avg_segment_bytes = (
                prev_avg * (n - 1) + bytes_total
            ) / n if n > 0 else float(bytes_total)

        prog = self._state.setdefault("progress", {})
        prog["completed"] = int(prog.get("completed", 0)) + 1
        pending = max(0, int(prog.get("pending", 0)) - 1)
        prog["pending"] = pending
        total = int(prog.get("total", 0))
        if total > 0:
            prog["percent"] = min(100, round((int(prog["completed"]) / total) * 100))

        if duplicate:
            msg = f"第 {segment_id} 段已存在，自动跳过"
            event_type = "skip_duplicate"
        else:
            msg = f"第 {segment_id} 段上传完成"
            event_type = "ok"

        event = {
            "at": _iso_now(),
            "type": event_type,
            "segmentId": segment_id,
            "duplicate": duplicate,
            "bytes": bytes_total,
            "elapsedSeconds": round(elapsed_s, 2),
            "speedMbps": round(speed * 8 / 1_000_000, 2) if speed and not duplicate else 0,
            "message": msg,
        }
        self._push_event(event)
        self._state["lastOk"] = event
        self._state["current"] = None
        self._state["sessionId"] = session_id
        self._state["throughputBytesPerSec"] = (
            sum(self._speed_samples) / len(self._speed_samples) if self._speed_samples else 0.0
        )
        self._update_eta(pending)
        if pending <= 0:
            self._state["service"] = {"running": True, "phase": "idle"}
        self._clear_error_if_recovered(segment_id)
        self._clear_stale_error_if_idle()
        self._persist()
        return msg

    def record_fail(
        self,
        *,
        session_id: str,
        segment_id: str,
        error: str,
    ) -> str:
        self._load_or_init()
        msg = f"第 {segment_id} 段上传失败：{error[:120]}"
        event = {
            "at": _iso_now(),
            "type": "fail",
            "segmentId": segment_id,
            "message": msg,
            "error": error[:500],
        }
        self._push_event(event)
        prog = self._state.setdefault("progress", {})
        prog["failed"] = int(prog.get("failed", 0)) + 1
        self._state["lastError"] = event
        self._state["current"] = None
        self._state["sessionId"] = session_id
        self._persist()
        return msg

    def record_quarantine_skip(
        self,
        *,
        session_id: str,
        segment_id: str,
        reason: str,
    ) -> str:
        self._load_or_init()
        label = REASON_LABELS_ZH.get(reason, reason)
        msg = f"第 {segment_id} 段已跳过：{label}"
        event = {
            "at": _iso_now(),
            "type": "skip",
            "segmentId": segment_id,
            "reason": reason,
            "message": msg,
        }
        self._push_event(event)
        skipped_list = self._state.setdefault("skippedSegments", [])
        if not any(s.get("segmentId") == segment_id for s in skipped_list):
            skipped_list.append({"segmentId": segment_id, "reason": reason, "reasonLabel": label})
        prog = self._state.setdefault("progress", {})
        prog["skipped"] = len(skipped_list)
        total = int(prog.get("completed", 0)) + int(prog.get("pending", 0)) + len(skipped_list)
        prog["total"] = max(total, len(skipped_list))
        self._state["sessionId"] = session_id
        self._persist()
        return msg

    def _update_eta(self, pending: int) -> None:
        avg_speed = (
            sum(self._speed_samples) / len(self._speed_samples) if self._speed_samples else 0.0
        )
        self._state["throughputBytesPerSec"] = avg_speed
        if pending <= 0 or avg_speed <= 0:
            self._state["etaSeconds"] = 0 if pending <= 0 else None
            return
        est_bytes = pending * (self._avg_segment_bytes or 78 * 1024 * 1024)
        self._state["etaSeconds"] = int(est_bytes / avg_speed)


def scan_skipped_segments(root: Path, session_id: str) -> list[dict[str, str]]:
    seg_root = root / "sessions" / session_id / "segments"
    if not seg_root.is_dir():
        return []
    out: list[dict[str, str]] = []
    for child in sorted(seg_root.iterdir()):
        if not child.is_dir() or not child.name.startswith("seg_"):
            continue
        skip_marker = child / ".upload_skip"
        if not skip_marker.is_file():
            continue
        reason = "unknown"
        try:
            raw = json.loads(skip_marker.read_text(encoding="utf-8"))
            reason = str(raw.get("reason") or reason)
        except (OSError, json.JSONDecodeError):
            pass
        label = REASON_LABELS_ZH.get(reason, reason)
        out.append({"segmentId": child.name, "reason": reason, "reasonLabel": label})
    return out


def format_human_status(st: dict[str, Any] | None, *, lang: str = "zh") -> str:
    if not st:
        if lang.startswith("zh"):
            return "尚无上传状态（上传服务可能未启动，或尚未产生状态文件）。"
        return "No upload status yet (service may be stopped)."

    if lang.startswith("zh"):
        return _format_zh(st)
    return _format_en(st)


def _format_zh(st: dict[str, Any]) -> str:
    lines: list[str] = []
    sid = str(st.get("sessionId") or "—")
    short_sid = _short_session_id(sid)

    prog = st.get("progress") or {}
    total = int(prog.get("total") or 0)
    completed = int(prog.get("completed") or 0)
    pending = int(prog.get("pending") or 0)
    failed = int(prog.get("failed", 0))
    cur = st.get("current")
    speed_bps = float(st.get("throughputBytesPerSec") or 0)
    eta = st.get("etaSeconds")
    phase = (st.get("service") or {}).get("phase", "idle")
    upload_done = phase == "idle" and pending <= 0 and failed <= 0 and not cur
    actively_uploading = not upload_done and (phase == "uploading" or cur or pending > 0)

    lines.append(f"ego-upload · {short_sid}")

    if actively_uploading:
        if total > 0:
            lines.append(
                _progress_summary_line(
                    completed, total, speed_bps, eta=eta, pending=pending
                )
            )
        elif pending > 0:
            lines.append(f"待传 {pending} 段")

        if cur and isinstance(cur, dict):
            seg = cur.get("segmentId", "—")
            nbytes = int(cur.get("bytes") or 0)
            size_mb = f"{nbytes / 1_000_000:.0f} MB" if nbytes >= 1_000_000 else ""
            elapsed = _elapsed_since(cur.get("startedAt"))
            elapsed_s = f"{elapsed}s" if elapsed is not None else "—"
            phase_label = _current_segment_phase(elapsed, speed_bps)
            seg_parts = [f"▶ {seg}"]
            if size_mb:
                seg_parts.append(size_mb)
            seg_parts.append(elapsed_s)
            if not _format_speed_mb_s(speed_bps):
                seg_parts.append(phase_label)
            lines.append(" · ".join(seg_parts))

        last_err = st.get("lastError")
        if isinstance(last_err, dict) and last_err.get("message") and (pending > 0 or failed > 0):
            seg = last_err.get("segmentId", "")
            err = str(last_err.get("error") or last_err.get("message") or "")[:60]
            lines.append(f"✕ {seg} · {err}" if seg else f"✕ {err}")
        return "\n".join(lines)

    if total > 0:
        lines.append(_progress_summary_line(completed, total, speed_bps))
    elif pending > 0:
        lines.append(f"待传 {pending} 段")

    if phase == "no_session":
        lines.append(PHASE_LABELS_ZH.get(phase, phase))
    elif pending > 0 and not cur:
        lines.append(PHASE_LABELS_ZH.get(phase, phase))

    for item in st.get("skippedSegments") or []:
        if isinstance(item, dict):
            lines.append(
                f"⚠ {item.get('segmentId')} · "
                f"{item.get('reasonLabel', item.get('reason'))}"
            )

    last_err = st.get("lastError")
    if isinstance(last_err, dict) and last_err.get("message"):
        show_err = pending > 0 or failed > 0 or phase == "uploading"
        if show_err:
            lines.append(f"✕ {last_err['message']}")

    if upload_done and completed > 0:
        try:
            from ego_capture_studio.capture.derive_status import upload_complete_derive_hint

            lines.append(upload_complete_derive_hint(lang="zh"))
        except ImportError:
            lines.append("→ 数据已送达服务器，后台派生中请耐心等待。")

    return "\n".join(lines)


def live_ui_enabled() -> bool:
    return os.environ.get("EGO_UPLOAD_LIVE_UI", "").strip().lower() in ("1", "true", "yes")


def paint_live_status(
    *,
    path: Path | None = None,
    lang: str = "zh",
    prev_lines: int = 0,
    finalize: bool = False,
) -> int:
    """Overwrite previous status block on stdout; return new line count."""
    text = format_human_status(read_status(path), lang=lang)
    if not text:
        return prev_lines
    lines = text.split("\n")
    if prev_lines > 0:
        sys.stdout.write(f"\033[{prev_lines}A")
    for line in lines:
        sys.stdout.write("\033[K" + line + "\n")
    extra = max(0, prev_lines - len(lines))
    if finalize:
        sys.stdout.write("\033[J")
    elif extra > 0:
        for _ in range(extra):
            sys.stdout.write("\033[K\n")
    sys.stdout.flush()
    return len(lines)


class LiveStatusPrinter:
    """Background poll of upload-status.json → single updating panel on stdout."""

    def __init__(
        self,
        *,
        interval_s: float = 0.5,
        path: Path | None = None,
        lang: str = "zh",
    ) -> None:
        self.interval_s = max(0.5, interval_s)
        self.path = path
        self.lang = lang
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._prev_lines = 0

    def __enter__(self) -> LiveStatusPrinter:
        if not sys.stdout.isatty():
            return self
        self._thread = threading.Thread(target=self._loop, name="ego-upload-live-ui", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_s + 1.0)
        if sys.stdout.isatty():
            paint_live_status(
                path=self.path, lang=self.lang, prev_lines=self._prev_lines, finalize=True
            )
            sys.stdout.flush()
            self._prev_lines = 0

    def _loop(self) -> None:
        while not self._stop.is_set():
            if sys.stdout.isatty():
                self._prev_lines = paint_live_status(
                    path=self.path, lang=self.lang, prev_lines=self._prev_lines
                )
            if self._stop.wait(self.interval_s):
                break


def _format_en(st: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Session: {st.get('sessionId') or '—'}")
    prog = st.get("progress") or {}
    lines.append(
        f"Progress: {prog.get('completed', 0)}/{prog.get('total', 0)} segments · {prog.get('percent', 0)}%"
    )
    lines.append(f"Pending: {prog.get('pending', 0)} · Skipped: {prog.get('skipped', 0)}")
    phase = (st.get("service") or {}).get("phase", "idle")
    lines.append(f"Phase: {phase}")
    return "\n".join(lines)
