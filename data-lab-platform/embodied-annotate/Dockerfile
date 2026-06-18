# Data Lab embodied-annotate (LeRobot Annotate fork) — FastAPI :7861
FROM python:3.10-slim-bookworm

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖层缓存
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" \
    -r /app/backend/requirements.txt

# 应用代码（业务逻辑原样 COPY，不做 patch）
COPY backend/ /app/backend/

ENV PYTHONPATH=/app
ENV LEROBOT_ANNOTATE_CACHE=/data/cache
ENV LEROBOT_ANNOTATE_EXPORT=/data/exports

EXPOSE 7861

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=10 \
  CMD curl -fsS http://127.0.0.1:7861/ >/dev/null || exit 1

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "7861"]
