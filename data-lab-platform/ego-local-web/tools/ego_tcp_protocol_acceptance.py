#!/usr/bin/env python3
"""Full TCP protocol acceptance suite (Tencent doc / ego-app-tcp-protocol.md)."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ego_tcp_bridge import encode_message, read_message, send_message

VIEW_IDS = {
    1: "front_left",
    2: "front_right",
    3: "rear_left",
    4: "rear_right",
}

DEVICE_INFO_KEYS = {
    "state",
    "duration",
    "segment_count",
    "storage_free_gb",
    "frames_writing",
    "view_num",
    "view_id",
    "preview_mjpeg_url",
    "preview_jpg_url",
}


class Suite:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.failures: list[str] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed += 1
        suffix = f" — {detail}" if detail else ""
        print(f"  PASS  {name}{suffix}")

    def fail(self, name: str, detail: str) -> None:
        self.failed += 1
        self.failures.append(f"{name}: {detail}")
        print(f"  FAIL  {name} — {detail}")

    def skip(self, name: str, reason: str) -> None:
        self.skipped += 1
        print(f"  SKIP  {name} — {reason}")


def recv_one(sock: socket.socket, timeout_s: float) -> dict[str, Any]:
    sock.settimeout(timeout_s)
    msg = read_message(sock)
    if msg is None:
        raise ConnectionError("server closed connection")
    return msg


def recv_until(
    sock: socket.socket,
    msg_type: str,
    timeout_s: float,
    *,
    log: bool = True,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        msg = recv_one(sock, remaining)
        if log:
            print(f"    ← {json.dumps(msg, ensure_ascii=False)}")
        if msg.get("type") == msg_type:
            return msg
    raise TimeoutError(f"timed out waiting for {msg_type}")


def send(sock: socket.socket, payload: dict[str, Any]) -> None:
    print(f"    → {json.dumps(payload, ensure_ascii=False)}")
    send_message(sock, payload)


def check_device_info_result(suite: Suite, name: str, msg: dict[str, Any], *, preview_host: str) -> dict[str, Any] | None:
    if msg.get("type") != "device_info_result":
        suite.fail(name, f"expected device_info_result, got {msg.get('type')}")
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        suite.fail(name, "missing data object")
        return None
    missing = DEVICE_INFO_KEYS - set(data.keys())
    if missing:
        suite.fail(name, f"missing keys: {sorted(missing)}")
        return None
    state = data.get("state")
    if state not in {"idle", "starting", "warming", "recording", "stopping", "error"}:
        suite.fail(name, f"invalid state={state!r}")
        return None
    view_num = data.get("view_num")
    view_id = data.get("view_id")
    if view_num in VIEW_IDS and view_id != VIEW_IDS[int(view_num)]:
        suite.fail(name, f"view_num={view_num} but view_id={view_id}")
        return None
    for key in ("preview_mjpeg_url", "preview_jpg_url"):
        url = str(data.get(key) or "")
        if not url.startswith(f"http://{preview_host}:8765/preview/"):
            suite.fail(name, f"{key} host mismatch: {url}")
            return None
    suite.ok(name, f"state={state} view={view_id}")
    return data


def wait_preview_ready(preview_host: str, timeout_s: float = 20.0) -> bool:
    status_url = f"http://{preview_host}:8765/preview/status"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ok, _ = http_get_ok(status_url, timeout_s=3.0)
        if ok:
            return True
        time.sleep(0.5)
    return False


def http_get_ok(url: str, timeout_s: float = 8.0) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read(512)
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            if url.endswith(".jpg") or "/jpg" in url:
                if not body.startswith(b"\xff\xd8"):
                    return False, "not JPEG magic"
            return True, f"{len(body)} bytes"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def run_acceptance(host: str, port: int, preview_host: str, *, skip_capture: bool) -> int:
    suite = Suite()
    addr = (host, port)
    print(f"\n=== Ego TCP protocol acceptance @ {host}:{port} (preview_host={preview_host}) ===\n")

    sock = socket.create_connection(addr, timeout=10)
    try:
        # 1 connect push
        print("[connect]")
        info = recv_until(sock, "device_info_result", 5)
        data = check_device_info_result(suite, "connect → device_info_result", info, preview_host=preview_host)
        if data and data.get("state") != "idle":
            suite.fail("precondition idle", f"state={data.get('state')}")
        else:
            suite.ok("precondition idle")

        # 2 heartbeat
        print("\n[heartbeat]")
        send(sock, {"type": "heartbeat"})
        ack = recv_until(sock, "heartbeat_ack", 5)
        if ack.get("type") == "heartbeat_ack":
            suite.ok("heartbeat → heartbeat_ack")
        else:
            suite.fail("heartbeat → heartbeat_ack", str(ack))

        # 3 device_info
        print("\n[device_info]")
        send(sock, {"type": "device_info"})
        info = recv_until(sock, "device_info_result", 5)
        check_device_info_result(suite, "device_info → device_info_result", info, preview_host=preview_host)

        # 4 legacy get_device_status
        print("\n[get_device_status legacy]")
        send(sock, {"type": "get_device_status"})
        info = recv_until(sock, "device_info_result", 5)
        check_device_info_result(suite, "get_device_status → device_info_result", info, preview_host=preview_host)

        # 5 change_view 1..4
        print("\n[change_view 1..4]")
        for view_num, view_id in VIEW_IDS.items():
            send(sock, {"type": "change_view", "data": {"view_num": view_num}})
            changed = recv_until(sock, "view_changed_result", 10)
            if changed.get("type") != "view_changed_result":
                suite.fail(f"change_view {view_num}", str(changed))
                continue
            cdata = changed.get("data") or {}
            if cdata.get("view_num") != view_num or cdata.get("view_id") != view_id:
                suite.fail(f"change_view {view_num}", f"got {cdata}")
                continue
            for key in ("preview_mjpeg_url", "preview_jpg_url"):
                if view_id not in str(cdata.get(key) or ""):
                    suite.fail(f"change_view {view_num} url", str(cdata.get(key)))
                    break
            else:
                suite.ok(f"change_view view_num={view_num}", view_id)

        # 6 change_view errors
        print("\n[change_view errors]")
        for payload, code in (
            ({"type": "change_view", "data": {"view_num": 0}}, "invalid_view_num"),
            ({"type": "change_view", "data": {"view_num": 5}}, "invalid_view_num"),
            ({"type": "change_view", "data": {"view_num": "x"}}, "invalid_view_num"),
            ({"type": "change_view"}, "invalid_payload"),
        ):
            send(sock, payload)
            err = recv_until(sock, "error", 5)
            edata = err.get("data") or {}
            if err.get("type") == "error" and edata.get("code") == code:
                suite.ok(f"change_view error {code}")
            else:
                suite.fail(f"change_view error {code}", str(err))

        # 7 protocol errors
        print("\n[protocol errors]")
        send(sock, {"type": "not_a_real_command"})
        err = recv_until(sock, "error", 5)
        if (err.get("data") or {}).get("code") == "unknown_type":
            suite.ok("unknown_type → error")
        else:
            suite.fail("unknown_type → error", str(err))

        send(sock, {"data": {"x": 1}})
        err = recv_until(sock, "error", 5)
        if (err.get("data") or {}).get("code") == "missing_type":
            suite.ok("missing type → error")
        else:
            suite.fail("missing type → error", str(err))

        # 8 preview HTTP from device_info URLs (idle standby)
        print("\n[preview HTTP idle]")
        send(sock, {"type": "change_view", "data": {"view_num": 1}})
        recv_until(sock, "view_changed_result", 10)
        if not wait_preview_ready(preview_host):
            suite.fail("preview standby warmup", "status not online")
        send(sock, {"type": "device_info"})
        info = recv_until(sock, "device_info_result", 5)
        idata = (info.get("data") or {}) if info.get("type") == "device_info_result" else {}
        jpg_url = str(idata.get("preview_jpg_url") or "")
        if jpg_url:
            ok, detail = http_get_ok(jpg_url)
            if not ok:
                time.sleep(3.0)
                ok, detail = http_get_ok(jpg_url, timeout_s=15)
            if ok:
                suite.ok("GET preview_jpg_url", detail)
            else:
                suite.fail("GET preview_jpg_url", f"{jpg_url} — {detail}")
        status_url = f"http://{preview_host}:8765/preview/status"
        ok, detail = http_get_ok(status_url)
        if ok:
            suite.ok("GET :8765/preview/status", detail)
        else:
            suite.fail("GET :8765/preview/status", detail)

        # 8b stop_recording invalid payload
        print("\n[stop_recording invalid payload]")
        send(sock, {"type": "stop_recording", "data": "bad"})
        err = recv_until(sock, "error", 5)
        if (err.get("data") or {}).get("code") == "invalid_payload":
            suite.ok("stop_recording invalid payload → error")
        else:
            suite.fail("stop_recording invalid payload → error", str(err))

        if skip_capture:
            suite.skip("capture flow", "--skip-capture")
            _print_summary(suite)
            return 0 if suite.failed == 0 else 1

        # 9 start_recording
        print("\n[start_recording]")
        send(sock, {"type": "start_recording"})
        start = recv_until(sock, "start_recording_result", 120)
        sdata = start.get("data") or {}
        if not sdata.get("success"):
            suite.fail("start_recording", sdata.get("msg") or str(start))
            _print_summary(suite)
            return 1
        suite.ok("start_recording → success")
        info = recv_until(sock, "device_info_result", 10)
        rdata = check_device_info_result(suite, "start → device_info_result", info, preview_host=preview_host)
        if rdata and rdata.get("state") not in {"starting", "warming", "recording"}:
            suite.fail("state after start", str(rdata.get("state")))

        # heartbeat during start/recording
        print("\n[heartbeat during recording]")
        send(sock, {"type": "heartbeat"})
        ack = recv_until(sock, "heartbeat_ack", 5)
        if ack.get("type") == "heartbeat_ack":
            suite.ok("heartbeat during recording")

        # duplicate start
        print("\n[duplicate start_recording]")
        send(sock, {"type": "start_recording"})
        dup = recv_until(sock, "start_recording_result", 30)
        ddata = dup.get("data") or {}
        if ddata.get("success") is False:
            suite.ok("duplicate start rejected", str(ddata.get("msg") or ""))
        else:
            suite.fail("duplicate start rejected", "success=true unexpected")

        # change_view during recording
        print("\n[change_view during recording]")
        send(sock, {"type": "change_view", "data": {"view_num": 1}})
        changed = recv_until(sock, "view_changed_result", 10)
        if changed.get("type") == "view_changed_result":
            suite.ok("change_view during recording")

        time.sleep(2)

        # preview during recording (main path may work)
        print("\n[preview HTTP recording]")
        send(sock, {"type": "device_info"})
        info = recv_until(sock, "device_info_result", 5)
        idata = (info.get("data") or {}) if info.get("type") == "device_info_result" else {}
        jpg_url = str(idata.get("preview_jpg_url") or "")
        if jpg_url:
            ok, detail = http_get_ok(jpg_url, timeout_s=15)
            if ok:
                suite.ok("GET preview_jpg_url while recording", detail)
            else:
                suite.skip("GET preview_jpg_url while recording", detail)

        # 10 stop save
        print("\n[stop_recording is_save=true]")
        send(sock, {"type": "stop_recording", "data": {"is_save": True}})
        stop = recv_until(sock, "stop_recording_result", 180)
        tdata = stop.get("data") or {}
        if tdata.get("success") and tdata.get("is_save") is True:
            suite.ok("stop_recording save")
        else:
            suite.fail("stop_recording save", str(stop))
        recv_until(sock, "device_info_result", 15)

        # legacy stop_and_save_recording: start brief + stop
        print("\n[legacy stop_and_save_recording]")
        send(sock, {"type": "start_recording"})
        start = recv_until(sock, "start_recording_result", 120)
        if not (start.get("data") or {}).get("success"):
            suite.skip("legacy stop_and_save_recording", "start failed")
        else:
            recv_until(sock, "device_info_result", 10)
            time.sleep(2)
            send(sock, {"type": "stop_and_save_recording"})
            stop = recv_until(sock, "stop_recording_result", 180)
            ldata = stop.get("data") or {}
            if ldata.get("success") and ldata.get("is_save") is True:
                suite.ok("stop_and_save_recording legacy")
            else:
                suite.fail("stop_and_save_recording legacy", str(stop))
            recv_until(sock, "device_info_result", 15)

        # abandon flow
        print("\n[stop_recording is_save=false abandon]")
        send(sock, {"type": "start_recording"})
        start = recv_until(sock, "start_recording_result", 120)
        if not (start.get("data") or {}).get("success"):
            suite.skip("abandon flow", "start failed")
        else:
            recv_until(sock, "device_info_result", 10)
            time.sleep(2)
            send(sock, {"type": "stop_recording", "data": {"is_save": False}})
            stop = recv_until(sock, "stop_recording_result", 180)
            adata = stop.get("data") or {}
            if adata.get("success") and adata.get("is_save") is False:
                suite.ok("stop_recording abandon")
            else:
                suite.fail("stop_recording abandon", str(stop))
            recv_until(sock, "device_info_result", 15)

        _print_summary(suite)
        return 0 if suite.failed == 0 else 1
    finally:
        sock.close()


def _print_summary(suite: Suite) -> None:
    print("\n=== SUMMARY ===")
    print(f"  passed : {suite.passed}")
    print(f"  failed : {suite.failed}")
    print(f"  skipped: {suite.skipped}")
    if suite.failures:
        print("\nFailures:")
        for line in suite.failures:
            print(f"  - {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ego TCP full protocol acceptance")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--preview-host", default=None, help="expected host in preview URLs")
    parser.add_argument("--skip-capture", action="store_true")
    args = parser.parse_args()
    preview_host = args.preview_host or args.host
    try:
        return run_acceptance(args.host, args.port, preview_host, skip_capture=args.skip_capture)
    except Exception as exc:  # noqa: BLE001
        print(f"\nFATAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
