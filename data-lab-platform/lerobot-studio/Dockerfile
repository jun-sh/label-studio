# Embodied dataset visualizer (static UI + bundled sample + API).

FROM node:20-alpine

# Default Alpine CDN is slow/unreachable on some LANs; mirror keeps apk (incl. zstd) reliable.
ARG ALPINE_MIRROR=mirrors.aliyun.com
RUN sed -i "s|dl-cdn.alpinelinux.org|${ALPINE_MIRROR}|g" /etc/apk/repositories \
  && apk add --no-cache zstd \
  && apk add --no-cache wget ca-certificates unzip ffmpeg python3 py3-pyarrow

WORKDIR /app
COPY download-assets.sh docker-entrypoint.sh prepare-bundled-dataset.sh ingest-bundled-datasets.sh server.mjs stream-ingest.mjs ingest-server.mjs frame_bin_codec.mjs /app/
COPY vendor/ /app/vendor/
COPY scripts/sync-stream-parquet.py /app/scripts/sync-stream-parquet.py
COPY patches/apply-branding.sh /app/patches/apply-branding.sh
COPY branding/ /app/branding/
COPY config/datasets.json /app/config/datasets.json
COPY config/sample-datasets.manifest.json /app/config/sample-datasets.manifest.json
COPY config/collection-stations.json /app/config/collection-stations.json

RUN chmod +x /app/download-assets.sh /app/docker-entrypoint.sh /app/prepare-bundled-dataset.sh \
  /app/ingest-bundled-datasets.sh /app/patches/apply-branding.sh

ENV PORT=7860
ENV LEROBOT_STUDIO_ROOT=/srv/lerobot
ENV BUNDLED_DATA_ROOT=/srv/bundled
ENV LEROBOT_DATASETS_JSON=/app/config/datasets.json
ENV NODE_ENV=production

EXPOSE 7860

HEALTHCHECK --interval=15s --timeout=10s --start-period=600s --retries=20 \
  CMD wget -qO- http://127.0.0.1:7860/lerobot/bundled/sensexperience_ego.zip >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
