#!/usr/bin/env python3
"""Full Unity TCP (:9100) acceptance test against a live edge host."""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ego_tcp_bridge import read_message, send_message

DEFAULT_HOST = "10.10.10.130"
DEFAULT_PORT = 9100
PREVIEW_HOST = "10.10.10.130"


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Session:
    sock: socket.socket
    results: list[Result] = field(default_factory=list)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def recv(self, timeout: float = 10.0) -> dict:
        self.sock.settimeout(timeout)
        msg = read_message(self.sock)
        if msg is None:
            raise ConnectionError("connection closed")
        return msg

    def call(self, payload: dict, *, timeout: float = 10.0, expect_followup: bool = False) -> list[dict]:
        send_message(self.sock, payload)
        replies = [self.recv(timeout=timeout)]
        if expect_followup:
            try:
                replies.append(self.recv(timeout=min(10.0, timeout)))
            except (TimeoutError, ConnectionError):
                pass
        return replies

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(Result(name, ok, detail))


def http_status(path: str) -> tuple[int, int]:
    url = f"http://{PREVIEW_HOST}:8765{path}"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = resp.read()
            return resp.status, len(data)
    except Exception as exc:
        return 0, int(getattr(exc, "code", 0) or 0)


def wait_status(host: str, want: str, timeout_s: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        with urllib.request.urlopen(f"http://{host}:8080/api/status", timeout=5) as resp:
            last = json.load(resp)
        if last.get("state") == want:
            if want != "recording" or last.get("frames_writing"):
                return last
        time.sleep(1.0)
    return last


def run(host: str, port: int) -> int:
    print(f"Connecting {host}:{port} …")
    sock = socket.create_connection((host, port), timeout=10)
    sess = Session(sock)

    try:
        init = sess.recv(5)
        d = init.get("data") or {}
        sess.record(
            "connect_push_device_info",
            init.get("type") == "device_info_result"
            and "state" in d
            and "preview_jpg_url" in d
            and "view_num" in d,
            json.dumps(init, ensure_ascii=False)[:200],
        )

        hb = sess.call({"type": "heartbeat"}, timeout=5)
        sess.record(
            "heartbeat",
            len(hb) == 1 and hb[0].get("type") == "heartbeat_ack",
            json.dumps(hb, ensure_ascii=False),
        )

        info = sess.call({"type": "device_info"}, timeout=5)
        idata = (info[0].get("data") or {}) if info else {}
        sess.record(
            "device_info",
            info[0].get("type") == "device_info_result"
            and {"state", "duration", "segment_count", "storage_free_gb", "frames_writing"}
            <= set(idata),
            f"state={idata.get('state')}",
        )

        legacy = sess.call({"type": "get_device_status"}, timeout=5)
        sess.record(
            "get_device_status_legacy",
            legacy[0].get("type") == "device_info_result",
            json.dumps(legacy, ensure_ascii=False),
        )

        for vn, vid in [(1, "front_left"), (4, "rear_right")]:
            ch = sess.call({"type": "change_view", "data": {"view_num": vn}}, timeout=5)
            cdata = (ch[0].get("data") or {}) if ch else {}
            sess.record(
                f"change_view_{vn}",
                ch[0].get("type") == "view_changed_result"
                and cdata.get("view_id") == vid
                and ":8765/preview/" in (cdata.get("preview_jpg_url") or ""),
                json.dumps(ch[0], ensure_ascii=False)[:180],
            )

        bad_views = [
            ({"type": "change_view", "data": {"view_num": 0}}, "invalid_view_num"),
            ({"type": "change_view", "data": {"view_num": 9}}, "invalid_view_num"),
            ({"type": "change_view", "data": "bad"}, "invalid_payload"),
        ]
        for payload, code in bad_views:
            err = sess.call(payload, timeout=5)
            edata = (err[0].get("data") or {}) if err else {}
            sess.record(
                f"change_view_error_{code}",
                err[0].get("type") == "error" and edata.get("code") == code,
                json.dumps(err[0], ensure_ascii=False),
            )

        prep = sess.call({"type": "prepare_multi_preview"}, timeout=10)
        pdata = (prep[0].get("data") or {}) if prep else {}
        sess.record(
            "prepare_multi_preview_idle",
            prep[0].get("type") == "prepare_multi_preview_result"
            and pdata.get("phase") in ("ready", "ending_warm_idle")
            and pdata.get("success") is True,
            json.dumps(prep[0], ensure_ascii=False),
        )

        time.sleep(8)
        cam_ok = 0
        cam_detail: list[str] = []
        for cam in ("front_left", "front_right", "rear_left", "rear_right"):
            code, size = http_status(f"/preview/{cam}/jpg")
            cam_detail.append(f"{cam}:{code}/{size}")
            if code == 200 and size > 100:
                cam_ok += 1
        sess.record("preview_8765_quad", cam_ok == 4, ", ".join(cam_detail))

        start = sess.call({"type": "start_recording"}, timeout=120, expect_followup=True)
        sdata = (start[0].get("data") or {}) if start else {}
        start_ok = bool(sdata.get("success"))
        sess.record(
            "start_recording",
            start_ok,
            json.dumps(start, ensure_ascii=False)[:220],
        )
        if start_ok and len(start) > 1:
            sess.record(
                "start_recording_push_device_info",
                start[1].get("type") == "device_info_result",
                json.dumps(start[1], ensure_ascii=False)[:180],
            )

        if start_ok:
            st = wait_status(host, "recording", timeout_s=30)
            sess.record(
                "status_while_recording",
                st.get("state") == "recording" and st.get("frames_writing"),
                f"state={st.get('state')} frames_writing={st.get('frames_writing')}",
            )

            prep_rec = sess.call({"type": "prepare_multi_preview"}, timeout=5)
            pr = (prep_rec[0].get("data") or {}) if prep_rec else {}
            sess.record(
                "prepare_multi_preview_blocked_recording",
                pr.get("phase") == "blocked_capture" and pr.get("success") is False,
                json.dumps(prep_rec[0], ensure_ascii=False),
            )

            code, size = http_status("/preview/front_left/jpg")
            sess.record(
                "preview_main_cam_while_recording",
                code == 200 and size > 100,
                f"front_left {code}/{size}",
            )

            time.sleep(3)
            stop = sess.call(
                {"type": "stop_recording", "data": {"is_save": True}},
                timeout=180,
                expect_followup=True,
            )
            tdata = (stop[0].get("data") or {}) if stop else {}
            stop_ok = bool(tdata.get("success")) and tdata.get("is_save") is True
            sess.record(
                "stop_recording_save",
                stop_ok,
                json.dumps(stop, ensure_ascii=False)[:220],
            )
            if stop_ok and len(stop) > 1:
                sess.record(
                    "stop_recording_push_device_info",
                    stop[1].get("type") == "device_info_result",
                    json.dumps(stop[1], ensure_ascii=False)[:180],
                )

            time.sleep(4)
            st2 = wait_status(host, "idle", timeout_s=120)
            sess.record(
                "status_after_stop",
                st2.get("state") == "idle",
                f"state={st2.get('state')} warm={st2.get('oak_warm_active')}",
            )

        bad_stop = sess.call({"type": "stop_recording", "data": "bad"}, timeout=5)
        bdata = (bad_stop[0].get("data") or {}) if bad_stop else {}
        sess.record(
            "stop_recording_invalid_payload",
            bad_stop[0].get("type") == "error" and bdata.get("code") == "invalid_payload",
            json.dumps(bad_stop[0], ensure_ascii=False),
        )

        unknown = sess.call({"type": "not_a_real_command"}, timeout=5)
        udata = (unknown[0].get("data") or {}) if unknown else {}
        sess.record(
            "unknown_type_error",
            unknown[0].get("type") == "error" and udata.get("code") == "unknown_type",
            json.dumps(unknown[0], ensure_ascii=False),
        )

        # Second short cycle: legacy stop_and_save + abandon path
        start2 = sess.call({"type": "start_recording"}, timeout=120, expect_followup=True)
        if (start2[0].get("data") or {}).get("success"):
            wait_status(host, "recording", timeout_s=30)
            time.sleep(3)
            ab = sess.call(
                {"type": "stop_recording", "data": {"is_save": False}},
                timeout=180,
                expect_followup=True,
            )
            adata = (ab[0].get("data") or {}) if ab else {}
            sess.record(
                "stop_recording_abandon",
                bool(adata.get("success")) and adata.get("is_save") is False,
                json.dumps(ab[0], ensure_ascii=False)[:220],
            )

            legacy_stop = sess.call({"type": "stop_and_save_recording"}, timeout=5)
            # may fail if already idle — only check response shape
            ltype = legacy_stop[0].get("type")
            sess.record(
                "stop_and_save_recording_legacy_shape",
                ltype in ("stop_recording_result", "error"),
                json.dumps(legacy_stop[0], ensure_ascii=False)[:180],
            )
        else:
            sess.record("stop_recording_abandon", False, "skipped: start2 failed")
            sess.record("stop_and_save_recording_legacy_shape", False, "skipped")

    finally:
        sess.close()

    passed = sum(1 for r in sess.results if r.ok)
    total = len(sess.results)
    print("\n" + "=" * 72)
    print(f"UNITY TCP ACCEPTANCE  {host}:{port}  —  {passed}/{total} passed")
    print("=" * 72)
    for r in sess.results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"[{mark}] {r.name}")
        if r.detail:
            print(f"       {r.detail}")
    print("=" * 72)
    return 0 if passed == total else 1


if __name__ == "__main__":
    h = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    p = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    raise SystemExit(run(h, p))
