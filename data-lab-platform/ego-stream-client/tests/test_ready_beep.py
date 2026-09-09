"""Tests for capture-ready host beep."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ready_beep


def test_play_capture_ready_beep_dry_run(capsys) -> None:
    with mock.patch.dict(os.environ, {"EGO_CAPTURE_READY_BEEP": "1", "EGO_CAPTURE_READY_BEEP_DRY_RUN": "1"}, clear=False):
        assert ready_beep.play_capture_ready_beep() is True
    assert "capture_ready_beep=dry_run" in capsys.readouterr().out


def test_play_capture_ready_beep_disabled(capsys) -> None:
    with mock.patch.dict(os.environ, {"EGO_CAPTURE_READY_BEEP": "0"}, clear=False):
        assert ready_beep.play_capture_ready_beep() is False
    assert "capture_ready_beep=disabled" in capsys.readouterr().out


def test_play_capture_ready_beep_custom_cmd(capsys) -> None:
    with mock.patch.dict(
        os.environ,
        {"EGO_CAPTURE_READY_BEEP": "1", "EGO_CAPTURE_BEEP_CMD": "echo beep-test"},
        clear=False,
    ):
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(args=[], returncode=0)) as run:
            assert ready_beep.play_capture_ready_beep() is True
            run.assert_called_once()
    assert "capture_ready_beep=played backend=custom_cmd" in capsys.readouterr().out


def test_synthesize_beep_wav(tmp_path: Path) -> None:
    wav_path = tmp_path / "beep.wav"
    ready_beep._synthesize_beep_wav(wav_path, frequency_hz=440.0, duration_s=0.05)
    assert wav_path.is_file()
    assert wav_path.stat().st_size > 1000
