"""Guardrails: heartbeat must not be tied to capture-stack PartOf."""

from __future__ import annotations

import unittest
from pathlib import Path

SYSTEMD_DIR = Path(__file__).resolve().parents[1] / "systemd"


class SystemdHeartbeatPolicyTests(unittest.TestCase):
    def test_heartbeat_not_part_of_capture_stack(self) -> None:
        dropin = SYSTEMD_DIR / "ecs-station-heartbeat.service.d"
        if dropin.is_dir():
            for path in dropin.glob("*.conf"):
                for line in path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    self.assertNotEqual(
                        stripped,
                        "PartOf=ecs-oak-capture-stack.target",
                        msg=f"{path.name} must not bind heartbeat to capture stack",
                    )

    def test_standby_stack_wants_heartbeat(self) -> None:
        text = (SYSTEMD_DIR / "ecs-oak-standby-stack.target").read_text(encoding="utf-8")
        self.assertIn("Wants=ecs-station-heartbeat.service", text)

    def test_capture_stack_does_not_want_heartbeat(self) -> None:
        text = (SYSTEMD_DIR / "ecs-oak-capture-stack.target").read_text(encoding="utf-8")
        self.assertNotIn("ecs-station-heartbeat.service", text)


if __name__ == "__main__":
    unittest.main()
