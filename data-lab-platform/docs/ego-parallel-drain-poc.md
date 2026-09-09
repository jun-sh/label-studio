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

`EGO_CAPTURE_SYNC_MODE=device_tick` (default in POC drop-in): commit when rings are
ready — no wall-clock grid wait. Revert with `EGO_CAPTURE_SYNC_MODE=strict_grid`.

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
