# Parallel USB Drain POC (`feat/ego-parallel-drain-poc`)

Isolated test line for multi-core OAK capture. **Does not modify** `oak_4p_capture.py` or `ecs-record-oak-mcap.service` (production).

## Architecture

```text
CAM_A drain thread ──→ ring_A ──┐
CAM_B drain thread ──→ ring_B ──┼──→ main thread: device-tick commit (+33ms stamps) + IMU + persist
CAM_C drain thread ──→ ring_C ──┤
CAM_D drain thread ──→ ring_D ──┘
IMU drain thread   ──→ imu buffer ─┘
preview drain thread (optional, when EGO_STRICT_SYNC_PREVIEW_DRAIN=0)
```

`EGO_CAPTURE_SYNC_MODE=fsync_quad` (default in POC drop-in): primary `CAM_A` tick +
timestamp-aligned quad pick within `EGO_FSYNC_QUAD_ALIGN_MAX_NS` (1ms). Waits up to
`EGO_FSYNC_QUAD_WAIT_MS` per tick; on miss drops primary head (no misaligned commit).
Set `OAK_H264_SEQUENTIAL=0`. Revert with `EGO_CAPTURE_SYNC_MODE=device_tick` or
`strict_grid`.

Deep ingest + async writer (default in POC drop-in):

- `EGO_POC_RING_LEN=512` — parallel + main rings (overflow counted)
- `EGO_CAPTURE_WRITER_ASYNC=1` — commit enqueues frames; writer thread calls `append_frame`
- `EGO_CAPTURE_FRAME_QUEUE_MAX=256`

```bash
journalctl --user -u ecs-record-oak-mcap-parallel-drain -f | \
  grep -E 'fsync_quad_|ingest_overflow_|async_frame_writer_|capture_fps='
```

### FSYNC-quad gates (130)

- `capture_fps` / `wall_commit_hz`: **29.8–30.2**
- `avg_max_cam_offset_us`: **< 1000**
- `quad_miss_pct`: **< 2%**
- `per_cam_ingest_hz`: all cams **≥ 29.8**
- `dropped=0`, 4-way MCAP frame parity, derive remux OK

```bash
journalctl --user -u ecs-record-oak-mcap-parallel-drain -f | \
  grep -E 'fsync_quad_|capture_fps=|straggler_'
```

## Switch to POC (130)

```bash
git checkout feat/ego-parallel-drain-poc
# deploy ego-studio to 130 (existing provision script)

systemctl --user disable --now ecs-record-oak-mcap.service
systemctl --user enable --now ecs-record-oak-mcap-parallel-drain.service
```

## Revert to production

```bash
systemctl --user disable --now ecs-record-oak-mcap-parallel-drain.service
systemctl --user enable --now ecs-record-oak-mcap.service
git checkout deploy-release   # or main
```

## Validate

```bash
# During 12s clip:
journalctl --user -u ecs-record-oak-mcap-parallel-drain -f | grep capture_fps

# CPU per thread:
pidstat -t -p $(pgrep -f record_oak_stream_parallel) 1
```

**Pass:** `capture_fps >= 29.5`, `dropped=0`, effective Hz 29.8–30.2, ≥2 cores with sustained load on cam-drain threads.

## Tuning env (drop-in only)

| Env | Default | Purpose |
|-----|---------|---------|
| `EGO_PARALLEL_DRAIN_BATCH` | 128 | Samples moved from parallel ring per main-loop tick |
| `EGO_PARALLEL_SPIN_ROUNDS` | 128 | tryGet bursts before idle sleep |
| `EGO_PARALLEL_IDLE_SLEEP_US` | 50 | Sleep when queue empty (µs) |
