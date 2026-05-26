# Data Lab Platform Overlay

Zero-intrusion extension for Label Studio: left sidebar「数据」menu + embedded [IO-AI LeRobot Studio](https://io-ai.tech/lerobot/) at `/lerobot/`.

## Quick start

From repository root:

```bash
export LABEL_STUDIO_HOST=http://10.10.10.34:8080

docker-compose \
  -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  build lerobot

docker-compose \
  -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  up -d
```

Or use the helper script:

```bash
./data-lab-platform/deploy-lerobot-studio.sh
```

| URL | Purpose |
|-----|---------|
| `http://10.10.10.34:8080` | Label Studio |
| `http://10.10.10.34:8080/lerobot-data` | LeRobot Studio in LS shell (iframe) |
| `http://10.10.10.34:8080/lerobot/?url=sample://sensexperience_ego` | Default sample (same as io-ai.tech) |

## Layout

```
data-lab-platform/
├── docker-compose.platform.yml
├── deploy-lerobot-studio.sh
├── gateway/nginx/
├── client/                  # LS shell + /lerobot-data embed page
├── scripts/
│   └── fetch-studio-assets.sh
└── lerobot-studio/
    ├── Dockerfile
    ├── download-assets.sh   # fixed asset manifest (no recursive crawl)
    ├── docker-entrypoint.sh
    ├── server.mjs           # /lerobot/* + /lerobot/datasets API
    └── config/datasets.json # sample:// registry
```

## LeRobot Studio service

Self-hosted mirror of IO-AI LeRobot Studio:

- Sample cards + `sample://` URLs
- Open Data (local folder / zip / remote URL) — handled in the browser
- `GET /lerobot/datasets` — sample manifest (`config/datasets.json`)

Customize samples: edit `lerobot-studio/config/datasets.json`, then `docker-compose … up -d --force-recreate lerobot`.

## Client-only changes (no image rebuild)

```bash
docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml restart nginx
```

Default iframe: `data-iframe-src="/lerobot/?url=sample%3A%2F%2Fsensexperience_ego"` in `datalab-inject.conf` and `lerobot-data.html`.

## Build notes

- `build lerobot` is fast (no asset download during image build).
- On first start, the container downloads static files from `io-ai.tech` into volume `lerobot-studio-data` (about 2–5 minutes). Watch progress:

  ```bash
  docker logs -f data-lab-lerobot-1
  ```

- Optional host prefetch:

  ```bash
  chmod +x data-lab-platform/scripts/fetch-studio-assets.sh
  ./data-lab-platform/scripts/fetch-studio-assets.sh
  ```

## Upgrade

- **Label Studio**: rebuild `app` image only.
- **LeRobot Studio**: `build lerobot` (or recreate container after config changes).
- **Shell / gateway**: edit `client/` or nginx snippets, then `restart nginx`.
