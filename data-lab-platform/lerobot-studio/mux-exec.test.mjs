import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  buildSetptsExprFromPtsSec,
  relativePtsSecFromTimestampsNs,
  splitAnnexBAccessUnits,
} from "./mux-exec.mjs";

describe("mux-exec device PTS helpers", () => {
  it("splitAnnexBAccessUnits groups VCL NALs into access units", () => {
    const idr = Buffer.from([0, 0, 0, 1, 0x65, 0x01]);
    const p1 = Buffer.from([0, 0, 0, 1, 0x41, 0x02]);
    const p2 = Buffer.from([0, 0, 0, 1, 0x41, 0x03]);
    const units = splitAnnexBAccessUnits(Buffer.concat([idr, p1, p2]));
    assert.equal(units.length, 3);
  });

  it("buildSetptsExprFromPtsSec maps frame numbers to seconds", () => {
    const expr = buildSetptsExprFromPtsSec([0, 0.033333333, 0.066666667]);
    assert.match(expr, /if\(eq\(n\\,0\)\\,0\.000000000/);
    assert.match(expr, /if\(eq\(n\\,1\)\\,0\.033333333/);
  });

  it("relativePtsSecFromTimestampsNs normalizes to episode start", () => {
    const base = 10_000_000_000_000;
    const pts = relativePtsSecFromTimestampsNs([base, base + 33_333_333, base + 66_666_666]);
    assert.deepEqual(pts?.map((v) => Math.round(v * 1e6)), [0, 33333, 66667]);
  });
});
