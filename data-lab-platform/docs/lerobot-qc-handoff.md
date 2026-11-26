# LeRobot QC — handoff (2026-11-26)

Stable ops details (paths, deploy, env, rebuild, backup) live in
[lerobot-qc-runbook.md](lerobot-qc-runbook.md). This file is the current state and next steps.

## State

- QC upgrade phase is **closed**. Merged to `main`:
  - PR #2: instruction fixes reach `task_index` / `tasks.parquet`; lerobot-free stats.
    Also: failed-rebuild cleanup, delivery freshness, rebuild pre-check, overview page,
    collection cache, resume after restart, shared `QcStore` (fixes lost reviews).
  - PR #5: 16-bit depth moved to `meta/depth_features.json`.
  - PR #6: automatic acceptance at the end of every rebuild.
  - PRs #14–#29: Datalab shell/menu, locale follow, home -> `/overview`, overview UI, trends API, overview redesign.
- Deliveries (UniFranka `unifranka_converted_v35`) at host `data-storage/lerobot-qc/delivery/`:
  `681447_v20261123`, `681448_v20261124`, `681449_v20261124`, `681451_v20261124`,
  `681453_v20261125`, `681454_v20261123`, `681455_v20261123`. One per package.
  - All 7 load with stock LeRobot 0.4.4 (torchcodec) and passed a full acceptance check.
  - Only 681447 and 681448 contain depth.
  - 681531 is all-removed, so it has no delivery.
  - The old `681447_v20260806` and `681453_v20261123` were deleted.
- Overview stage counts: delivered 7, in_review 19, not_started 58, all_removed 1, needs_rebuild 0.
  The remaining work is human review, not tooling.

## Deployment

The deployed checkout is `main` at `60c7cfb24`, which equals `origin/main`, so PRs #26–#29 are live.
`/` redirects to `/overview`, and `GET /qc/api/qc/trends` answers in about 0.06 s.
The QC and app containers were restarted after those merges.

To deploy later merges:

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab
git pull --ff-only origin main            # checkout has unrelated local edits; stop on conflict
docker restart data-lab-lerobot-qc-1      # Python changed (check no .qc_rebuild_job.json in delivery dirs first)
docker restart data-lab-app-1             # overview.html template
```

## Open items / next steps

1. `/overview` (PR #29 redesign) was verified logged in after deploy. If anything looks wrong, re-check at phone width.
2. Depth-aware reader for training: decode `.mkv` FFV1 gray16le using `meta/depth_features.json`
   `video_paths` and the per-episode `videos/<key>/*` columns. Build it only when a consumer needs depth.
3. Opening one package from the USB NTFS disk (BookDuo_2) takes about 8 s. A metadata cache would help.
4. Batch rebuild from the overview page (select needs_rebuild rows).
5. The ego data path has not gone through QC yet; only UniFranka (teleop) has.
6. Converter-side data issues (not QC bugs):
   - Source image stats count is lower than the frame count (e.g. 10432 vs 11540).
   - `finalized` is always False in minimal mode.

## Verify

```bash
cd data-lab-platform/lerobot-qc/backend && python3 -m pytest -q tests   # run from backend/, not repo root
curl -s "http://10.10.10.34:8080/qc/api/qc/overview?collection=unifranka_converted_v35" | python3 -m json.tool | head
```

Gotchas:
- The working tree carries about 38 unrelated uncommitted edits (nginx, mux-exec, deploy scripts).
  Never commit them with QC work.
- Delivery and sidecar files are root-owned. Delete them via
  `docker exec data-lab-lerobot-qc-1 rm -rf /data/qc-delivery/<name>`.
