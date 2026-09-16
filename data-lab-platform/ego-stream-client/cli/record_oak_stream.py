"""CLI entry for systemd: python -m ego_capture_studio.cli.record_oak_stream

Delegates to capture.record_oak_stream.main() so install_h264_production_backend
patches the real module (not sys.modules['__main__']).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable


def _load_capture_main() -> Callable[[], None]:
    try:
        from ego_capture_studio.capture.record_oak_stream import main

        return main
    except ModuleNotFoundError:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from record_oak_stream import main  # type: ignore[import-not-found]

        return main


if __name__ == "__main__":
    _load_capture_main()()
