#!/usr/bin/env node
/** Verify DERIVE_MUX_INJECT_FAIL_CAMERA is visible inside stream-ingest container. */
const target = String(process.env.DERIVE_MUX_INJECT_FAIL_CAMERA || "").trim();
if (!target) {
  console.error("DERIVE_MUX_INJECT_FAIL_CAMERA unset");
  process.exit(1);
}
console.log(JSON.stringify({ ok: true, injectCamera: target }));
