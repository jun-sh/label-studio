"""Entry point — see README.md for calibration workflow."""

from __future__ import annotations

import sys


def main() -> int:
    print("oak-4p-calib — OAK 四目鱼眼标定工具")
    print()
    print("常用命令（在仓库本目录下执行）：")
    print("  uv run python scripts/verify_device.py")
    print("  uv run python scripts/read_calib.py -o data/calib")
    print("  uv run python scripts/undistort_4cam.py")
    print()
    print("完整说明见 README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
