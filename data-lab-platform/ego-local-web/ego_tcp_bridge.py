#!/usr/bin/env python3
"""TCP JSON control bridge for Ego mobile apps — reuses ego_web capture logic."""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
from typing import Any

import ego_web

HOST = os.environ.get("EGO_TCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("EGO_TCP_PORT", "9100"))
PREVIEW_HOST = os.environ.get("EGO_TCP_PREVIEW_HOST", "10.10.10.130").strip()
PREVIEW_HTTP_PORT = int(os.environ.get("EGO_TCP_PREVIEW_HTTP_PORT", "8765"))
MAX_FRAME_BYTES = int(os.environ.get("EGO_TCP_MAX_FRAME_BYTES", str(1 << 20)))
READ_TIMEOUT_S = float(os.environ.get("EGO_TCP_READ_TIMEOUT_S", "120"))

VIEW_BY_NUM: dict[int, str] = {
    1: "front_left",
    2: "front_right",
    3: "rear_left",
    4: "rear_right",
}

_server_lock = threading.Lock()
_selected_view_num = 1


class ProtocolError(ValueError):
    pass


def encode_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise ProtocolError(f"message exceeds {MAX_FRAME_BYTES} bytes")
    return struct.pack(">I", len(body)) + body


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("connection closed while reading frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(sock: socket.socket) -> dict[str, Any] | None:
    header = sock.recv(4)
    if not header:
        return None
    if len(header) < 4:
        header += recv_exact(sock, 4 - len(header))
    length = struct.unpack(">I", header)[0]
    if length == 0:
        raise ProtocolError("empty frame body")
    if length > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame length {length} exceeds limit {MAX_FRAME_BYTES}")
    body = recv_exact(sock, length)
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ProtocolError("frame body must be a JSON object")
    return payload


def send_message(sock: socket.socket, payload: dict[str, Any]) -> None:
    sock.sendall(encode_message(payload))


def _preview_urls(view_id: str) -> dict[str, str]:
    base = f"http://{PREVIEW_HOST}:{PREVIEW_HTTP_PORT}/preview/{view_id}"
    return {
        "preview_mjpeg_url": f"{base}/mjpeg",
        "preview_jpg_url": f"{base}/jpg",
    }


def _storage_free_gb() -> float | None:
    free_bytes = ego_web._storage_free_bytes(ego_web.SEGMENT_ROOT)
    if free_bytes is None:
        return None
    return round(free_bytes / (1024**3), 2)


def _build_device_info_result() -> dict[str, Any]:
    global _selected_view_num
    status = ego_web._build_status()
    view_id = VIEW_BY_NUM.get(_selected_view_num, "front_left")
    data: dict[str, Any] = {
        "state": status.get("state"),
        "duration": status.get("duration", 0),
        "segment_count": status.get("segment_count", 0),
        "storage_free_gb": _storage_free_gb(),
        "frames_writing": status.get("frames_writing", False),
        "view_num": _selected_view_num,
        "view_id": view_id,
        **{k: v for k, v in _preview_urls(view_id).items()},
    }
    msg = status.get("msg")
    if msg:
        data["msg"] = msg
    return {"type": "device_info_result", "data": data}


def _build_view_changed_result(view_num: int) -> dict[str, Any]:
    view_id = VIEW_BY_NUM[view_num]
    return {
        "type": "view_changed_result",
        "data": {
            "view_num": view_num,
            "view_id": view_id,
            **{k: v for k, v in _preview_urls(view_id).items()},
        },
    }


def _error_message(code: str, message: str, *, request_type: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"code": code, "message": message}
    if request_type:
        data["request_type"] = request_type
    return {"type": "error", "data": data}


def _start_recording_result(success: bool, msg: str) -> dict[str, Any]:
    return {
        "type": "start_recording_result",
        "data": {"success": success, "msg": msg},
    }


def _stop_recording_result(success: bool, msg: str, is_save: bool) -> dict[str, Any]:
    return {
        "type": "stop_recording_result",
        "data": {"success": success, "is_save": is_save, "msg": msg},
    }


def _prepare_multi_preview_result() -> dict[str, Any]:
    return {
        "type": "prepare_multi_preview_result",
        "data": ego_web.prepare_multi_preview_access(),
    }


def _handle_change_view(data: Any) -> dict[str, Any]:
    global _selected_view_num
    if not isinstance(data, dict):
        return _error_message(
            "invalid_payload",
            "change_view requires data.view_num",
            request_type="change_view",
        )
    view_num_raw = data.get("view_num")
    try:
        view_num = int(view_num_raw)
    except (TypeError, ValueError):
        return _error_message(
            "invalid_view_num",
            "view_num must be an integer 1-4",
            request_type="change_view",
        )
    if view_num not in VIEW_BY_NUM:
        return _error_message(
            "invalid_view_num",
            "view_num must be 1, 2, 3, or 4",
            request_type="change_view",
        )
    with _server_lock:
        _selected_view_num = view_num
        ego_web._prepare_view_switch_preview_access()
    return _build_view_changed_result(view_num)


def _parse_stop_is_save(data: Any) -> bool | None:
    if data is None:
        return True
    if not isinstance(data, dict):
        return None
    if "is_save" not in data:
        return True
    return bool(data.get("is_save"))


def _handle_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    msg_type = msg.get("type")
    if not isinstance(msg_type, str) or not msg_type:
        return [_error_message("missing_type", "message type is required")]

    data = msg.get("data")

    if msg_type == "heartbeat":
        return [{"type": "heartbeat_ack"}]

    if msg_type in ("device_info", "get_device_status"):
        return [_build_device_info_result()]

    if msg_type == "start_recording":
        ok, text = ego_web._run_capture_action("start")
        replies = [_start_recording_result(ok, text)]
        if ok:
            replies.append(_build_device_info_result())
        return replies

    if msg_type in ("stop_recording", "stop_and_save_recording"):
        is_save = _parse_stop_is_save(data)
        if is_save is None:
            return [
                _error_message(
                    "invalid_payload",
                    "stop_recording requires data.is_save (bool)",
                    request_type="stop_recording",
                )
            ]
        if is_save:
            ok, text = ego_web._run_capture_action("stop")
        else:
            ok, text = ego_web._run_capture_abandon()
        replies = [_stop_recording_result(ok, text, is_save)]
        if ok:
            replies.append(_build_device_info_result())
        return replies

    if msg_type == "change_view":
        return [_handle_change_view(data)]

    if msg_type == "prepare_multi_preview":
        return [_prepare_multi_preview_result()]

    return [_error_message("unknown_type", f"unsupported message type: {msg_type}", request_type=msg_type)]


def handle_client(sock: socket.socket, peer: tuple[str, int]) -> None:
    sock.settimeout(READ_TIMEOUT_S)
    peer_label = f"{peer[0]}:{peer[1]}"
    print(f"tcp_client_connected peer={peer_label}", flush=True)
    try:
        send_message(sock, _build_device_info_result())
        while True:
            try:
                msg = read_message(sock)
            except socket.timeout:
                continue
            if msg is None:
                break
            for reply in _handle_message(msg):
                send_message(sock, reply)
    except (ConnectionError, BrokenPipeError, ConnectionResetError):
        pass
    except ProtocolError as exc:
        print(f"tcp_protocol_error peer={peer_label} err={exc}", flush=True)
        try:
            send_message(sock, _error_message("protocol_error", str(exc)))
        except OSError:
            pass
    except json.JSONDecodeError as exc:
        print(f"tcp_json_error peer={peer_label} err={exc}", flush=True)
        try:
            send_message(sock, _error_message("invalid_json", "frame body is not valid JSON"))
        except OSError:
            pass
    except OSError as exc:
        print(f"tcp_client_error peer={peer_label} err={exc}", flush=True)
    finally:
        try:
            sock.close()
        except OSError:
            pass
        print(f"tcp_client_disconnected peer={peer_label}", flush=True)


def serve_forever() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(8)
    print(
        f"ego-tcp-bridge listening on {HOST}:{PORT} "
        f"(preview_host={PREVIEW_HOST}:{PREVIEW_HTTP_PORT})",
        flush=True,
    )
    try:
        while True:
            client, peer = server.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(client, peer),
                name=f"ego-tcp-{peer[0]}:{peer[1]}",
                daemon=True,
            )
            thread.start()
    finally:
        server.close()


def main() -> None:
    serve_forever()


if __name__ == "__main__":
    main()
