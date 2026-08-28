# MCAP golden fixtures (ego-mcap-pilot P0)

## Files

| File | Purpose |
|------|---------|
| `golden-seg.mcap` | Regression baseline — 3 frames, 4 cameras, IMU, observation topics |
| `golden-seg.meta.json` | Expected topic message counts (generated with fixture) |

## Regenerate

```bash
python3 data-lab-platform/fixtures/mcap/generate_golden_fixture.py
```

## Verify (Foxglove)

Open `golden-seg.mcap` in Foxglove Studio; expect topics under `/ego/camera/*`, `/ego/imu/raw`, `/ego/observation/*`.

## Compare in CI / tests

```bash
pytest data-lab-platform/ego-stream-client/tests/test_mcap_segment_writer.py -q
```
