"""Unit tests for ego_tcp_bridge framing (no hardware required)."""

from __future__ import annotations

import io
import socket
import struct
import threading
import time
import unittest
from unittest import mock

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ego_tcp_bridge import encode_message, read_message, send_message


class FrameCodecTest(unittest.TestCase):
    def test_encode_big_endian_length(self) -> None:
        frame = encode_message({"type": "heartbeat"})
        length = struct.unpack(">I", frame[:4])[0]
        self.assertEqual(length, len(frame) - 4)
        self.assertIn(b'"type":"heartbeat"', frame[4:])

    def test_round_trip_over_socketpair(self) -> None:
        left, right = socket.socketpair()
        try:
            payload = {"type": "device_info", "data": {"state": "idle"}}
            send_message(left, payload)
            parsed = read_message(right)
            self.assertEqual(parsed, payload)
        finally:
            left.close()
            right.close()


class HandlerSmokeTest(unittest.TestCase):
    def test_heartbeat_on_mocked_status(self) -> None:
        from ego_tcp_bridge import handle_client

        server_sock, client_sock = socket.socketpair()
        try:
            with mock.patch("ego_tcp_bridge.ego_web._build_status", return_value={"state": "idle", "duration": 0, "segment_count": 0, "storage_free": "1 GB", "storage_warn": False, "frames_writing": False, "msg": ""}):
                thread = threading.Thread(target=handle_client, args=(server_sock, ("127.0.0.1", 1)), daemon=True)
                thread.start()
                initial = read_message(client_sock)
                self.assertEqual(initial.get("type"), "device_info")
                send_message(client_sock, {"type": "heartbeat"})
                ack = read_message(client_sock)
                self.assertEqual(ack, {"type": "heartbeat_ack"})
                client_sock.close()
                thread.join(timeout=2)
        finally:
            server_sock.close()


if __name__ == "__main__":
    unittest.main()
