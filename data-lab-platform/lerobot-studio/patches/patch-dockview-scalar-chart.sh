#!/bin/sh
# Default scalar chart: hide zero-variance placeholder joints; prefer MANO/state/kp2d.
set -eu

ROOT="${1:-/srv/lerobot}"

python3 - "$ROOT" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

root = Path(sys.argv[1])
assets = root / "assets"
if not assets.is_dir():
    sys.exit(0)

HELPER = (
    "function __datalabJointSeriesHasVariance(e,t){var n=t&&t[e];"
    "if(!n)return!1;for(var r of[n.state,n.action]){if(!r||r.length<2)continue;"
    "var i=r[0],a=r[r.length-1];if(Math.abs(a-i)>1e-8)return!0;"
    "for(var o=1;o<Math.min(r.length,24);o++)if(Math.abs(r[o]-i)>1e-8)return!0}"
    "return!1}"
    "function __datalabDefaultJointIds(e,t){"
    "var n=/^(mano_pose|state|mano_kp2d_uv|pose)\\[/;"
    "return e.filter(function(e){return n.test(e)&&(!t||__datalabJointSeriesHasVariance(e,t))})}"
)

OLD_EFFECT = (
    "(0,q.useEffect)(()=>{V.size===0&&Y.length>0&&!pe.current&&H(new Set(Y))},[Y,V.size])"
)
NEW_EFFECT = (
    "(0,q.useEffect)(()=>{if(V.size>0||!Y.length||pe.current)return;"
    "if(X){var __z=__datalabDefaultJointIds(Y,X);H(new Set(__z.length>0?__z:Y));return}"
    "var __c=Y.filter(function(__id){return /^(mano_pose|state|mano_kp2d_uv|pose)\\[/.test(__id)});"
    "H(new Set(__c.length>0?__c:Y))},[Y,V.size,X])"
)

ANCHOR = "function Ye(e,t){"

patched = 0
for path in sorted(assets.glob("DockviewLayout-*.js")):
    text = path.read_text(encoding="utf-8")
    if "__datalabDefaultJointIds" in text:
        patched += 1
        continue
    if ANCHOR not in text or OLD_EFFECT not in text:
        print(f"skip (pattern missing): {path.name}")
        continue
    text = text.replace(ANCHOR, HELPER + ANCHOR, 1)
    text = text.replace(OLD_EFFECT, NEW_EFFECT, 1)
    path.write_text(text, encoding="utf-8")
    print(f"patched scalar chart defaults: {path.name}")
    patched += 1

if patched:
    print(f"scalar chart patch applied ({patched} file(s))")
PY
