# Legacy ego scripts (archived)

These scripts target **deprecated** paths:

- `ego-lan-214` station fleet
- H264 / `segment_mp4` capture and derive
- `v0.0.9.2` ingest overlay

**Current production (ego-001):**

- Capture: HW JPEG frame bins (`z-production-egoverse.conf`)
- Upload: `tarzst` → staging JPEG mux
- Reset: `ego-001-reset-for-rerun.sh` or `ego-reset-34-only.sh`
- E2E: `ego-001-plan-b-e2e.sh`
- Verify 130: `ego-130-verify-production.sh`

Do not run legacy scripts on ego-001 without reading them first.

**Archived compose overlays:** `data-lab-platform/deploy/archive/compose/`  
**Archived deploy scripts:** `data-lab-platform/scripts/legacy/deploy-stream-ingest-v0.0.*.sh`
