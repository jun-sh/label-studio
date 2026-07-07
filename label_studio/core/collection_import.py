"""Proxy single-segment browser fallback upload to stream-ingest (not batch production path)."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

STREAM_INGEST_URL = os.environ.get("STREAM_INGEST_INTERNAL_URL", "http://stream-ingest:7862").rstrip("/")
TOKENS_JSON = os.environ.get(
    "COLLECTION_STATION_TOKENS_JSON",
    str(
        Path(settings.BASE_DIR).parent
        / "data-lab-platform"
        / "lerobot-studio"
        / "config"
        / "collection-station-tokens.json"
    ),
)

_station_tokens: dict[str, str] | None = None


def _load_station_tokens() -> dict[str, str]:
    global _station_tokens
    if _station_tokens is not None:
        return _station_tokens
    try:
        with open(TOKENS_JSON, encoding="utf-8") as f:
            raw = json.load(f)
        _station_tokens = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except OSError:
        logger.warning("collection import: cannot read tokens from %s", TOKENS_JSON)
        _station_tokens = {}
    return _station_tokens


def _station_token(station_id: str) -> str | None:
    return _load_station_tokens().get(station_id)


def _forward(station_id: str, method: str, path_suffix: str, body: bytes | None, content_type: str | None):
    token = _station_token(station_id)
    url = f"{STREAM_INGEST_URL}/lerobot/api/collection/stations/{station_id}{path_suffix}"
    headers = {"Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    if token:
        headers["X-Station-Token"] = token
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        status = exc.code
    return status, payload


@login_required
@require_http_methods(["POST"])
def collection_station_import_upload(request, station_id: str):
    content_type = request.META.get("CONTENT_TYPE", "application/octet-stream")
    status, payload = _forward(
        station_id,
        "POST",
        "/import/upload",
        request.body,
        content_type,
    )
    return HttpResponse(payload, status=status, content_type="application/json")


@login_required
@require_http_methods(["GET"])
def collection_station_import_task(request, station_id: str, task_id: str):
    status, payload = _forward(station_id, "GET", f"/import/tasks/{task_id}", None, None)
    return HttpResponse(payload, status=status, content_type="application/json")
