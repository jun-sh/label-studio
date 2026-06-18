# embodied-annotate — Upstream sync

## Baseline

| Item | Value |
|------|-------|
| Upstream repo | https://github.com/huggingface/lerobot-annotate |
| Upstream branch | main |
| Import baseline commit | N/A (source imported from sibling workspace, not a git clone) |
| Data Lab customization start | First commit in data-lab monorepo under `data-lab-platform/embodied-annotate/` |

## Protected customization (verify after every merge)

| File | Protected content |
|------|-------------------|
| `backend/static/i18n.js` | i18n, `datalab_embed` mode, DL / Embodied Annotate branding, hide HF Hub |
| `backend/static/index.html` | `i18n.js` include, `pushHubPanel` ids |
| `backend/static/app.js` | `LA_I18N` integration (if present) |
| `backend/app.py` | Follow upstream by default; on conflict, keep upstream logic and re-apply static customizations |

Do not change: `/api/episodes/{i}/annotations` contract, `subtasks.parquet` export format.

## Sync workflow

```bash
cd data-lab-platform/embodied-annotate
git remote add upstream https://github.com/huggingface/lerobot-annotate.git  # once
git fetch upstream
git checkout -b sync/upstream-$(date +%Y%m%d)
git merge upstream/main   # or cherry-pick

# Verify i18n.js / index.html / app.js
cd $REPO
docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  build embodied-annotate
docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  up -d embodied-annotate
```

## Release tagging

Data Lab `v0.0.x` release notes should record the `embodied-annotate` image build commit (short SHA).
