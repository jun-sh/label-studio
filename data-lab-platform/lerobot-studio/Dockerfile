# Embodied dataset visualizer (static UI + bundled sample + API).
# Bookworm base: Alpine lacks wheels for torch/datasets required by official LeRobot derive.
# Build context: data-lab-platform/ (see deploy-stream-ingest-v0.1.4-mcap.sh).

FROM node:20-bookworm-slim

WORKDIR /app
COPY lerobot-studio/requirements-derive.txt /app/requirements-derive.txt
COPY lerobot-studio/requirements-parquet-lerobot.txt /app/requirements-parquet-lerobot.txt
COPY lerobot-studio/package.json /app/package.json
# Optional offline wheel cache — run scripts/download-derive-python-wheels.sh once on host.
COPY lerobot-studio/vendor/python-wheels/ /wheels/

# Use CN mirrors — deb.debian.org is too slow on this host (~20min+ for apt alone).
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' \
      /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
    wget ca-certificates unzip ffmpeg zstd \
    python3 python3-pip python3-dev python3-zstandard build-essential \
  && rm -rf /var/lib/apt/lists/* \
  && npm install --omit=dev --registry=https://registry.npmmirror.com \
  && if [ -f /wheels/.wheels-ready ]; then \
       echo "pip: offline wheels (/wheels)"; \
       pip3 install --break-system-packages --no-index --find-links=/wheels \
         /wheels/torch-*+cpu*cp311*.whl \
         /wheels/torchvision-*+cpu*cp311*.whl; \
       find /wheels -maxdepth 1 -name '*.whl' \
         ! -name 'lerobot-*' ! -name 'torch-*' ! -name 'torchvision-*' ! -name '*-cp310-cp310-*' \
         -exec pip3 install --break-system-packages --no-index --find-links=/wheels --no-deps {} +; \
       pip3 install --break-system-packages --no-index --find-links=/wheels \
         "lerobot==0.4.4" --no-deps; \
     else \
       echo "pip: online (run download-derive-python-wheels.sh to cache offline)"; \
       pip3 install --break-system-packages --no-cache-dir --default-timeout=600 \
         "torch==2.7.1+cpu" \
         --index-url https://download.pytorch.org/whl/cpu \
         --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple; \
       pip3 install --break-system-packages --no-cache-dir --default-timeout=600 \
         -i https://pypi.tuna.tsinghua.edu.cn/simple \
         -r /app/requirements-derive.txt \
         datasets "huggingface_hub>=0.34.0" "numpy>=1.26.0" "Pillow>=10.0.0" \
         cmake einops "opencv-python-headless<4.13.0,>=4.9.0" "av<16.0.0,>=15.0.0" \
         "jsonlines<5.0.0,>=4.0.0" "packaging<26.0,>=24.2" "pynput<1.9.0,>=1.7.7" \
         "pyserial<4.0,>=3.5" "wandb<0.25.0,>=0.24.0"; \
       pip3 install --break-system-packages --no-cache-dir --default-timeout=600 \
         -i https://pypi.tuna.tsinghua.edu.cn/simple "lerobot==0.4.4" --no-deps; \
     fi \
  && python3 -c "from lerobot.datasets.lerobot_dataset import LeRobotDataset; from lerobot.datasets.video_utils import encode_video_frames"

COPY lerobot-studio/ingest/ /app/ingest/
COPY lerobot-studio/derive/ /app/derive/
COPY ego-stream-client/h264_segment_boundary.py /app/ego-stream-client/h264_segment_boundary.py
COPY lerobot-studio/download-assets.sh lerobot-studio/docker-entrypoint.sh lerobot-studio/prepare-bundled-dataset.sh lerobot-studio/ingest-bundled-datasets.sh lerobot-studio/server.mjs lerobot-studio/stream-ingest.mjs lerobot-studio/mux-exec.mjs lerobot-studio/lerobot-converter.mjs lerobot-studio/task-naming.mjs lerobot-studio/ingest-server.mjs lerobot-studio/delivery-status.mjs lerobot-studio/frame_bin_codec.mjs lerobot-studio/import-handlers.mjs lerobot-studio/derive-async.mjs lerobot-studio/derive-pipeline.mjs /app/
COPY lerobot-studio/vendor/ /app/vendor/
COPY lerobot-studio/scripts/sync-stream-parquet.py /app/scripts/sync-stream-parquet.py
COPY lerobot-studio/scripts/append-segment-parquet.py /app/scripts/append-segment-parquet.py
COPY lerobot-studio/session-markers.mjs lerobot-studio/derive-lock.mjs lerobot-studio/ego-derive-run.mjs lerobot-studio/ego-derive-watch.mjs /app/
COPY lerobot-studio/scripts/extract-tar-zst.py /app/scripts/extract-tar-zst.py
COPY lerobot-studio/scripts/validate-mcap-archive.py /app/scripts/validate-mcap-archive.py
COPY lerobot-studio/scripts/export-videos-from-parquet.py /app/scripts/export-videos-from-parquet.py
COPY lerobot-studio/scripts/sync-vendor-annotations.py /app/scripts/sync-vendor-annotations.py
COPY lerobot-studio/scripts/ego-derive-worker-entry.sh /app/scripts/ego-derive-worker-entry.sh
COPY lerobot-studio/patches/apply-branding.sh /app/patches/apply-branding.sh
COPY lerobot-studio/patches/patch-dockview-scalar-chart.sh /app/patches/patch-dockview-scalar-chart.sh
COPY lerobot-studio/patches/patch-playback-mode.sh /app/patches/patch-playback-mode.sh
COPY lerobot-studio/branding/ /app/branding/
COPY lerobot-studio/config/datasets.json /app/config/datasets.json
COPY lerobot-studio/config/sample-datasets.manifest.json /app/config/sample-datasets.manifest.json
COPY lerobot-studio/config/collection-stations.json /app/config/collection-stations.json
COPY lerobot-studio/config/collection-station-tokens.json /app/config/collection-station-tokens.json
COPY lerobot-studio/config/station-topology.json /app/config/station-topology.json

RUN chmod +x /app/download-assets.sh /app/docker-entrypoint.sh /app/prepare-bundled-dataset.sh \
  /app/ingest-bundled-datasets.sh /app/patches/apply-branding.sh /app/patches/patch-dockview-scalar-chart.sh \
  /app/patches/patch-playback-mode.sh /app/scripts/ego-derive-worker-entry.sh

ENV PORT=7860
ENV LEROBOT_STUDIO_ROOT=/srv/lerobot
ENV BUNDLED_DATA_ROOT=/srv/bundled
ENV LEROBOT_DATASETS_JSON=/app/config/datasets.json
ENV NODE_ENV=production
ENV DERIVE_PARQUET_BACKEND=lerobot
ENV DERIVE_VIDEO_EXPORT_BACKEND=auto

EXPOSE 7860

HEALTHCHECK --interval=15s --timeout=10s --start-period=600s --retries=20 \
  CMD wget -qO- http://127.0.0.1:7860/lerobot/bundled/sensexperience_ego.zip >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
