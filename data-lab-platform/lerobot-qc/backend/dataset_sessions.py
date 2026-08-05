"""In-memory per-dataset sessions — isolates concurrent QC clients by package."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from dataset_manager import DatasetState
from qc_store import QcStore, canonical_dataset_key

DATASET_PATH_HEADER = "X-Dataset-Path"
DEFAULT_MAX_SESSIONS = int(os.environ.get("LEROBOT_QC_MAX_SESSIONS", "64"))
DEFAULT_TTL_SECONDS = float(os.environ.get("LEROBOT_QC_SESSION_TTL", "7200"))


@dataclass
class DatasetSession:
    state: DatasetState
    store: QcStore
    dataset_key: str
    local_path: str
    last_access: float = field(default_factory=time.monotonic)


class DatasetSessionRegistry:
    """Thread-safe LRU-ish cache of loaded datasets keyed by canonical package identity."""

    def __init__(self, *, max_sessions: int = DEFAULT_MAX_SESSIONS, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._sessions: dict[str, DatasetSession] = {}
        self._path_aliases: dict[str, str] = {}
        self._lock = threading.RLock()
        self._max_sessions = max(1, max_sessions)
        self._ttl_seconds = max(60.0, ttl_seconds)

    @staticmethod
    def _resolve_path(local_path: str) -> Path:
        return Path(local_path).expanduser().resolve()

    @staticmethod
    def _dataset_key(root: Path) -> str:
        return canonical_dataset_key(root)

    def open(self, *, state: DatasetState, store: QcStore, local_path: str) -> DatasetSession:
        root = state.dataset_root.resolve()
        key = self._dataset_key(root)
        resolved_path = str(root)
        now = time.monotonic()
        with self._lock:
            self._evict_expired(now)
            session = DatasetSession(
                state=state,
                store=store,
                dataset_key=key,
                local_path=resolved_path,
                last_access=now,
            )
            self._sessions[key] = session
            self._path_aliases[resolved_path] = key
            self._path_aliases[str(self._resolve_path(local_path))] = key
            self._enforce_limit()
            return session

    def get_by_path(self, local_path: str) -> DatasetSession | None:
        if not local_path or not local_path.strip():
            return None
        resolved = str(self._resolve_path(local_path))
        now = time.monotonic()
        with self._lock:
            self._evict_expired(now)
            key = self._path_aliases.get(resolved)
            if key:
                session = self._sessions.get(key)
                if session is not None:
                    session.last_access = now
                    return session
            candidate_key = self._dataset_key(Path(resolved))
            session = self._sessions.get(candidate_key)
            if session is not None:
                session.last_access = now
                self._path_aliases[resolved] = candidate_key
                return session
            return None

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._path_aliases.clear()

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, session in self._sessions.items() if now - session.last_access > self._ttl_seconds]
        for key in expired:
            self._drop_session(key)

    def _enforce_limit(self) -> None:
        while len(self._sessions) > self._max_sessions:
            oldest_key = min(self._sessions.items(), key=lambda item: item[1].last_access)[0]
            self._drop_session(oldest_key)

    def _drop_session(self, key: str) -> None:
        session = self._sessions.pop(key, None)
        if session is None:
            return
        stale_paths = [path for path, mapped in self._path_aliases.items() if mapped == key]
        for path in stale_paths:
            self._path_aliases.pop(path, None)
