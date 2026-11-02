from __future__ import annotations

import json
from pathlib import Path

from ego_capture_studio.schema.episode import PolicyReadyBundle


def write_policy_bundle(path: Path, bundle: PolicyReadyBundle) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bundle.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
