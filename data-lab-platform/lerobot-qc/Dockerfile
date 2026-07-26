# LeRobot QC — reuse embodied-annotate base (ffmpeg/curl/python deps already installed).
# Avoids slow apt-get from deb.debian.org during build on CN networks.
ARG BASE_IMAGE=data-lab-embodied-annotate:local
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# QC requirements are a subset of embodied-annotate; this layer is usually a no-op.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" \
    -r /app/backend/requirements.txt

COPY backend/ /app/backend/

ENV PYTHONPATH=/app/backend
ENV LEROBOT_QC_CACHE=/data/cache
ENV LEROBOT_QC_SIDECAR=/data/qc-sidecar
ENV LEROBOT_QC_DELIVERY=/data/qc-delivery
ENV LEROBOT_QC_DATASETS=/data/datasets

EXPOSE 7863

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=10 \
  CMD curl -fsS http://127.0.0.1:7863/healthz >/dev/null || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7863"]
