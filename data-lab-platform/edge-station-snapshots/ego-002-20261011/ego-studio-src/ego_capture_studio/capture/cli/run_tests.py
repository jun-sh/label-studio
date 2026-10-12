"""Run pytest with ROS global plugins disabled (common on Ubuntu + ROS2)."""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    import pytest

    raise SystemExit(pytest.main(sys.argv[1:]))


if __name__ == "__main__":
    main()
