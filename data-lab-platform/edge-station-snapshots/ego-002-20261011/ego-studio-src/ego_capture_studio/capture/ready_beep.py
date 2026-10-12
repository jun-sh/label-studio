"""Short host speaker cue when OAK warmup probe finishes and real capture begins."""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _synthesize_beep_wav(
    path: Path,
    *,
    frequency_hz: float = 880.0,
    duration_s: float = 0.22,
    volume: float = 0.32,
    sample_rate: int = 44100,
) -> None:
    frame_count = max(1, int(sample_rate * duration_s))
    fade_in = max(1, int(sample_rate * 0.015))
    fade_out = max(1, int(sample_rate * 0.05))
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            t = index / sample_rate
            envelope = min(
                1.0,
                index / fade_in,
                (frame_count - index) / fade_out,
            )
            sample = int(32767 * volume * envelope * math.sin(2.0 * math.pi * frequency_hz * t))
            frames.extend(struct.pack("<h", sample))
        handle.writeframes(frames)


def _run_player(player: str, wav_path: Path) -> bool:
    try:
        proc = subprocess.run(
            [player, str(wav_path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def play_capture_ready_beep() -> bool:
    """Play one short beep after warmup probe. Returns True if a player ran successfully."""
    if not _env_flag("EGO_CAPTURE_READY_BEEP", "1"):
        _log("capture_ready_beep=disabled")
        return False
    if _env_flag("EGO_CAPTURE_READY_BEEP_DRY_RUN", "0"):
        _log("capture_ready_beep=dry_run")
        return True

    custom_cmd = os.environ.get("EGO_CAPTURE_BEEP_CMD", "").strip()
    if custom_cmd:
        try:
            proc = subprocess.run(
                custom_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log(f"capture_ready_beep=failed reason=custom_cmd error={exc}")
            return False
        if proc.returncode == 0:
            _log("capture_ready_beep=played backend=custom_cmd")
            return True
        _log(f"capture_ready_beep=failed reason=custom_cmd rc={proc.returncode}")
        return False

    wav_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        _synthesize_beep_wav(wav_path)
        for player in ("paplay", "aplay"):
            if shutil.which(player):
                if _run_player(player, wav_path):
                    _log(f"capture_ready_beep=played backend={player}")
                    return True
        _log("capture_ready_beep=skipped reason=no_audio_player")
        return False
    finally:
        if wav_path is not None:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass
