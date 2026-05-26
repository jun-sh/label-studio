# IO-AI LeRobot Studio — static mirror + datasets API.
# Assets download on first container start (see docker-entrypoint.sh), not at image build.

FROM node:20-alpine

RUN apk add --no-cache wget ca-certificates

WORKDIR /app
COPY download-assets.sh docker-entrypoint.sh server.mjs /app/
COPY config/datasets.json /app/config/datasets.json
RUN chmod +x /app/download-assets.sh /app/docker-entrypoint.sh

ENV PORT=7860
ENV LEROBOT_STUDIO_ROOT=/srv/lerobot
ENV LEROBOT_DATASETS_JSON=/app/config/datasets.json
ENV NODE_ENV=production

EXPOSE 7860

HEALTHCHECK --interval=15s --timeout=10s --start-period=300s --retries=10 \
  CMD wget -qO- http://127.0.0.1:7860/lerobot/ >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
