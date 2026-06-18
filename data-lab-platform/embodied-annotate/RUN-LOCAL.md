# embodied-annotate — Run via Docker Compose (Data Lab)

## Quick start

From the data-lab repo root:

```bash
COMPOSE="docker-compose \
  -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml"

$COMPOSE build embodied-annotate
$COMPOSE up -d embodied-annotate nginx
```

Open via platform proxy: **http://10.10.10.34:8080/lerobot-annotate/**

Embedded in Data Lab: **http://10.10.10.34:8080/projects/:id/embodied** (`datalab_embed=1`)

## Demo dataset

| Field | Value |
|-------|-------|
| Container path | `/data/datasets/pusht` |
| Host path | `data-storage/embodied-annotate/datasets/pusht` |
| Source | HuggingFace `lerobot/pusht` (subset) |
| FPS | 10 |

In the UI: **Connect dataset** → source **local** → path:

```
/data/datasets/pusht
```

## Workflow

1. **Connect dataset** → source **local** (or HF repo) → **Load**
2. Pick an episode from the left list
3. Select a subtask label chip
4. **Drag** on the timeline to create a region
5. **Submit** to save episode annotations
6. **Export** → writes to `/data/exports/<name>/` (host: `data-storage/embodied-annotate/exports/`)

## Environment

| Variable | Container default | Purpose |
|----------|-------------------|---------|
| `LEROBOT_ANNOTATE_CACHE` | `/data/cache` | HF download cache, ffmpeg clip cache |
| `LEROBOT_ANNOTATE_EXPORT` | `/data/exports` | Export output root |

## Logs & health

```bash
docker logs -f data-lab-embodied-annotate-1
curl -fsS http://10.10.10.34:8080/lerobot-annotate/ >/dev/null && echo OK
```

## Source

Upstream: https://github.com/huggingface/lerobot-annotate — see `UPSTREAM.md` for sync policy.
