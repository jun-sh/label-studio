#!/usr/bin/env python3
"""Integration test client for ego_tcp_bridge — simulates App control messages."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

# Allow `python3 tools/ego_tcp_client_test.py` from ego-local-web/
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ego_tcp_bridge import encode_message, read_message, send_message

DEFAULT_HOST = "10.10.10.130"
DEFAULT_PORT = 9100


def recv_one(sock: socket.socket, timeout_s: float) -> dict:
    sock.settimeout(timeout_s)
    msg = read_message(sock)
    if msg is None:
        raise ConnectionError("server closed connection")
    return msg


def recv_until_type(sock: socket.socket, msg_type: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        msg = recv_one(sock, remaining)
        print(f"  ← {json.dumps(msg, ensure_ascii=False)}")
        if msg.get("type") == msg_type:
            return msg
    raise TimeoutError(f"timed out waiting for {msg_type}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ego TCP bridge integration test")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--skip-capture", action="store_true", help="skip start/stop recording")
    parser.add_argument("--heartbeat-only", action="store_true")
    args = parser.parse_args()

    addr = (args.host, args.port)
    print(f"Connecting to {addr[0]}:{addr[1]} …")
    sock = socket.create_connection(addr, timeout=10)
    sock.settimeout(30)

    try:
        print("\n[1] Expect initial device_info on connect")
        info = recv_until_type(sock, "device_info", 5)
        state = (info.get("data") or {}).get("state")
        print(f"    state={state}")

        print("\n[2] heartbeat → heartbeat_ack")
        send_message(sock, {"type": "heartbeat"})
        ack = recv_until_type(sock, "heartbeat_ack", 5)
        assert ack.get("type") == "heartbeat_ack"

        print("\n[3] get_device_status → device_info")
        send_message(sock, {"type": "get_device_status"})
        recv_until_type(sock, "device_info", 5)

        print("\n[4] change_view view_num=2 → view_changed")
        send_message(sock, {"type": "change_view", "data": {"view_num": 2}})
        changed = recv_until_type(sock, "view_changed", 5)
        assert (changed.get("data") or {}).get("view_id") == "front_right"

        if args.heartbeat_only:
            print("\n[done] heartbeat-only mode")
            return 0

        if args.skip_capture:
            print("\n[done] skipped capture commands (--skip-capture)")
            return 0

        print("\n[5] start_recording → capture_result (+ device_info if ok)")
        send_message(sock, {"type": "start_recording"})
        result = recv_until_type(sock, "capture_result", 120)
        success = (result.get("data") or {}).get("success")
        print(f"    success={success}")
        if success:
            recv_until_type(sock, "device_info", 10)
            time.sleep(2)
            print("\n[6] stop_and_save_recording → capture_result")
            send_message(sock, {"type": "stop_and_save_recording"})
            stop = recv_until_type(sock, "capture_result", 180)
            print(f"    success={(stop.get('data') or {}).get('success')}")
            recv_until_type(sock, "device_info", 10)
        else:
            print("    skip stop because start failed")

        print("\n[done] all steps completed")
        return 0
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
