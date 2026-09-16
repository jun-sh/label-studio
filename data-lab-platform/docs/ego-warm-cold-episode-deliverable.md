# Warm USB + Cold Episode — Deliverable Sign-off Checklist

Production feature: **OAK stays connected between clips** (Warm USB), **each Start gets a fresh sealed session** (Cold Episode).

## Architecture (sign-off scope)

| Layer | Contract |
|-------|----------|
| **Capture** | Writes `capture_stop_state.json` phases: `recording → stopping → flushing → sealed → warm_idle` |
| **ego_web** | Async stop (POST returns immediately); polls stop phase for UI; hard-stop fallback on timeout/error |
| **CLI** | Single implementation via `capture.record_oak_stream.main()` — no duplicate module |
| **Async writer** | Consumer thread restarts after flush; stall timeout (`EGO_ASYNC_WRITER_STALL_S`, default 45s) |

Shared files (under `EGO_CAPTURE_CHECKPOINT` parent):

- `checkpoint.json` — active session pointer
- `capture_live_stats.json` — timer / beep anchor
- `oak_warm_state.json` — warm idle window
- `capture_stop_state.json` — **stop phase state machine**

## CI gate (merge blocker)

```bash
bash data-lab-platform/scripts/rc-ego-capture-smoke.sh
```

Runs warm/stop contract tests + full `ego-local-web/tests/`.

## Field deploy (130)

```bash
RC_CAPTURE_PASS=1 bash data-lab-platform/scripts/ego-130-deploy-warm-cold-episode.sh
RC_CAPTURE_PASS=1 bash data-lab-platform/scripts/ego-130-post-deploy-smoke.sh
```

Deploy script: preflight compile/import, sync all capture modules, **restart ego-web**, remote import smoke.

## Qualification test (operator, before sign-off)

On ego-001 edge UI:

1. **Cold start** → beep within 20s → record 30s → **Stop** → idle within **15s**
2. **Warm start** (within 5 min) → beep within **5s** → record 30s → **Stop** → idle within **15s**
3. Repeat × **10** cycles without UI stuck on「正在保存数据」
4. Verify each stopped session has `segments/sessions/sess_*/session_seal.json`
5. Long clip: record **>75s** (segment rollover) → Stop → seal OK

Pass criteria:

- Stop P99 < 15s (UI returns idle; `capture_stop_phase` → `warm_idle` or file cleared)
- Zero `async_frame_writer_flush` lines with unchanged `written=` for >60s in journal
- No `capture_fatal` in journal during qualification run

## Rollback

Set on **both** web + capture systemd drop-ins:

```
Environment=EGO_OAK_WARM_IDLE=0
```

Redeploy; behavior reverts to full cold start/stop (backup tag `backup/pre-warm-usb-cold-episode-20260916`).

## Known limits (documented, not blocking sign-off)

- First cold start still ~15–20s (OAK connect + H264 ready)
- Warm window default 300s AC / 120s battery (`EGO_OAK_WARM_S`)
- Unity/TCP `:9100` protocol unchanged

## Sign-off record

| Item | Owner | Date | Pass |
|------|-------|------|------|
| CI `rc-ego-capture-smoke.sh` | | | |
| 130 deploy + post-deploy smoke | | | |
| Operator 10× cycle qualification | | | |
| Long clip stop/seal | | | |
