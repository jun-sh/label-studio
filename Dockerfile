ARG NODE_VERSION=22
ARG PYTHON_VERSION=3.13
ARG POETRY_VERSION=2.3.2
ARG VERSION_OVERRIDE
ARG BRANCH_OVERRIDE

################################ Overview

# Requires Docker BuildKit: RUN --mount (cache/bind). Example:
#   DOCKER_BUILDKIT=1 docker build -t data-lab:local .

# This Dockerfile builds a Label Studio environment.
# It consists of five main stages:
# 1. "frontend-builder" - Compiles the frontend assets using Node.
# 2. "frontend-version-generator" - Generates version files for frontend sources.
# 3. "venv-builder" - Virtualenv via Poetry on Debian slim (same libc as prod).
# 4. "py-version-generator" - Generates version files for python sources.
# 5. "prod" - Final image: python-slim + nginx (glibc, same as venv); Nginx from nginx.org.

################################ Stage: frontend-builder (build frontend assets)
# Default BUILDPLATFORM for legacy `docker build` (BuildKit/Buildx set this automatically).
ARG BUILDPLATFORM=linux/amd64
# Use Debian slim instead of Alpine: `apk add` can exit non-zero after glib/busybox triggers even when cairo/pango deps installed.
FROM --platform=${BUILDPLATFORM} node:${NODE_VERSION}-bookworm-slim AS frontend-builder
ARG DEBIAN_USE_MIRROR=""
# Override when Aliyun returns bad payloads (e.g. "unexpected size"): Tsinghua HTTP mirrors work well in CN.
ARG DEBIAN_APT_MAIN="http://mirrors.aliyun.com/debian"
ARG DEBIAN_APT_SECURITY="http://mirrors.aliyun.com/debian-security"
# Optional: set DEBIAN_USE_MIRROR=yes (via docker-compose) to use Debian mirrors for unstable networks in CN.
RUN set -eux; \
    if [ "$DEBIAN_USE_MIRROR" = "yes" ] || [ "$DEBIAN_USE_MIRROR" = "true" ] || [ "$DEBIAN_USE_MIRROR" = "1" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/debian.list; do \
        [ -f "$f" ] || continue; \
        sed -i \
          -e "s|http://deb.debian.org/debian|${DEBIAN_APT_MAIN}|g" \
          -e "s|https://deb.debian.org/debian|${DEBIAN_APT_MAIN}|g" \
          -e "s|http://security.debian.org/debian-security|${DEBIAN_APT_SECURITY}|g" \
          -e "s|https://security.debian.org/debian-security|${DEBIAN_APT_SECURITY}|g" \
          "$f"; \
      done; \
    fi

# apt retries + long fetch timeout; Aliyun occasionally serves stub responses during mirror sync ("unexpected size").
RUN printf '%s\n' \
      'Acquire::Retries "10";' \
      'Acquire::http::Timeout "120";' \
      'Acquire::ftp::Timeout "120";' \
    > /etc/apt/apt.conf.d/99timeout-retries

RUN set +e; \
    attempt=1; max=12; \
    while [ "$attempt" -le "$max" ]; do \
      apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        build-essential \
        pkg-config \
        libcairo2-dev \
        libgif-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
        libpango1.0-dev \
        git \
        python3 \
      && rm -rf /var/lib/apt/lists/* && exit 0; \
      echo "apt-get install failed (attempt $attempt/$max), cleaning cache and retrying..."; \
      apt-get clean || true; \
      rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/partial/* 2>/dev/null || true; \
      sleep 25; \
      attempt=$((attempt+1)); \
    done; \
    exit 1

ENV BUILD_NO_SERVER=true \
    BUILD_NO_HASH=true \
    BUILD_NO_CHUNKS=true \
    BUILD_MODULE=true \
    YARN_CACHE_FOLDER=/root/web/.yarn \
    NX_CACHE_DIRECTORY=/root/web/.nx \
    NODE_ENV=production \
    NODE_OPTIONS="--max-old-space-size=4096"

WORKDIR /label-studio/web

# Same pattern as label-studio 1.15: CN-friendly npm registry + long HTTP timeout (yarn install is heavy).
RUN yarn config set network-timeout 1200000

COPY web/package.json .
COPY web/yarn.lock .
COPY web/tools tools
RUN --mount=type=cache,target=/root/web/.yarn,id=yarn-cache,sharing=locked \
    --mount=type=cache,target=/root/web/.nx,id=nx-cache,sharing=locked \
    yarn config set registry https://registry.npmmirror.com && \
    yarn install --prefer-offline --no-progress --pure-lockfile --frozen-lockfile --ignore-engines --non-interactive --production=false

COPY web/ .
COPY pyproject.toml ../pyproject.toml
# Optional baked frontend: data-lab-platform/build-context/web-dist/ (see deploy-label-studio-image.sh)
ARG USE_PREBUILT_FRONTEND=auto
COPY data-lab-platform/build-context/web-dist /label-studio/build-context/web-dist
RUN --mount=type=cache,target=/root/web/.yarn,id=yarn-cache,sharing=locked \
    --mount=type=cache,target=/root/web/.nx,id=nx-cache,sharing=locked \
    set -eux; \
    use_prebuilt=0; \
    if [ "${USE_PREBUILT_FRONTEND}" = "yes" ]; then \
      use_prebuilt=1; \
    elif [ "${USE_PREBUILT_FRONTEND}" = "auto" ]; then \
      if [ -f ../build-context/web-dist/apps/labelstudio/main.js ] && \
         grep -q 'embodied-annotate' ../build-context/web-dist/apps/labelstudio/main.js; then \
        echo "frontend-builder: using baked web-dist from build-context"; \
        use_prebuilt=1; \
      elif [ -f dist/apps/labelstudio/main.js ] && \
           grep -q 'embodied-annotate' dist/apps/labelstudio/main.js; then \
        echo "frontend-builder: using prebuilt web/dist from build context"; \
        use_prebuilt=1; \
      fi; \
    fi; \
    if [ "$use_prebuilt" = "1" ]; then \
      if [ -f ../build-context/web-dist/apps/labelstudio/main.js ]; then \
        rm -rf dist && cp -a ../build-context/web-dist dist; \
      fi; \
      test -f dist/apps/labelstudio/main.js; \
      grep -q 'embodied-annotate' dist/apps/labelstudio/main.js; \
    else \
      yarn run build; \
    fi

################################ Stage: frontend-version-generator
FROM frontend-builder AS frontend-version-generator
RUN --mount=type=cache,target=/root/web/.yarn,id=yarn-cache,sharing=locked \
    --mount=type=cache,target=/root/web/.nx,id=nx-cache,sharing=locked \
    --mount=type=bind,source=.git,target=../.git \
    yarn version:libs

################################ Stage: venv-builder (prepare the virtualenv)
# Debian slim (not Alpine): matches upstream 1.15+ — same libc as production, no flaky apk/gcc; apt cache mounts speed rebuilds.
FROM python:${PYTHON_VERSION}-slim AS venv-builder
ARG POETRY_VERSION=2.3.2
ARG PYTHON_VERSION
ARG PYPI_INDEX_URL="https://mirrors.aliyun.com/pypi/simple/"
ARG PIP_TRUSTED_HOST="mirrors.aliyun.com"
ARG DEBIAN_USE_MIRROR=""
ARG DEBIAN_APT_MAIN="http://mirrors.aliyun.com/debian"
ARG DEBIAN_APT_SECURITY="http://mirrors.aliyun.com/debian-security"
RUN set -eux; \
    if [ "$DEBIAN_USE_MIRROR" = "yes" ] || [ "$DEBIAN_USE_MIRROR" = "true" ] || [ "$DEBIAN_USE_MIRROR" = "1" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/debian.list; do \
        [ -f "$f" ] || continue; \
        sed -i \
          -e "s|http://deb.debian.org/debian|${DEBIAN_APT_MAIN}|g" \
          -e "s|https://deb.debian.org/debian|${DEBIAN_APT_MAIN}|g" \
          -e "s|http://security.debian.org/debian-security|${DEBIAN_APT_SECURITY}|g" \
          -e "s|https://security.debian.org/debian-security|${DEBIAN_APT_SECURITY}|g" \
          "$f"; \
      done; \
    fi

RUN printf '%s\n' \
      'Acquire::Retries "10";' \
      'Acquire::http::Timeout "120";' \
    > /etc/apt/apt.conf.d/99timeout-retries

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    apt-get update; \
    apt-get install --no-install-recommends -y \
      build-essential \
      git \
      curl \
      python3-dev \
      libpq-dev \
      libpcre2-dev \
    ; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=600 \
    POETRY_HTTP_TIMEOUT=1200 \
    PIP_CACHE_DIR="/.cache" \
    POETRY_CACHE_DIR="/.poetry-cache" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_VIRTUALENVS_PREFER_ACTIVE_PYTHON=true

# Official install.python-poetry.org can hang or fail in CI/Docker; pip install is reliable and honors PYPI_INDEX_URL.
RUN --mount=type=cache,target=/.cache/pip \
    set -eux; \
    pip_install_args=""; \
    if [ -n "${PYPI_INDEX_URL}" ]; then pip_install_args="-i ${PYPI_INDEX_URL}"; fi; \
    if [ -n "${PIP_TRUSTED_HOST}" ]; then export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}"; fi; \
    python -m pip install -U pip setuptools wheel ${pip_install_args}; \
    python -m pip install "poetry==${POETRY_VERSION}" ${pip_install_args}; \
    poetry --version

WORKDIR /label-studio

ENV VENV_PATH="/label-studio/.venv"
ENV PATH="$VENV_PATH/bin:$PATH"

## Starting from this line all packages will be installed in $VENV_PATH

# Copy dependency files
COPY pyproject.toml poetry.lock README.md ./

# Set a default build argument for including dev dependencies
ARG INCLUDE_DEV=false
# Optional: rewrite label-studio-sdk GitHub zip URL (IncompleteRead / timeouts to github.com in CN).
# Build with: --build-arg USE_GITHUB_DOWNLOAD_MIRROR=ghproxy   (or leave empty for direct GitHub).
ARG USE_GITHUB_DOWNLOAD_MIRROR=""
RUN set -eux; \
    case "${USE_GITHUB_DOWNLOAD_MIRROR}" in \
      ""|off|none|false) ;; \
      ghproxy) \
        sed -i 's|https://github.com/HumanSignal/label-studio-sdk/archive/|https://mirror.ghproxy.com/https://github.com/HumanSignal/label-studio-sdk/archive/|' poetry.lock \
        ;; \
      *) echo "Unsupported USE_GITHUB_DOWNLOAD_MIRROR=${USE_GITHUB_DOWNLOAD_MIRROR} (use ghproxy, off, or empty)" >&2; exit 1 ;; \
    esac

# CN PyPI mirror: pip.conf + poetry primary source in pyproject (build-time lock regen).
RUN set -eux; \
    mkdir -p /root/.pip; \
    printf '[global]\nindex-url = %s\ntrusted-host = %s\ntimeout = 600\nretries = 10\n' \
      "${PYPI_INDEX_URL}" "${PIP_TRUSTED_HOST}" > /root/.pip/pip.conf; \
    cp /root/.pip/pip.conf /etc/pip.conf; \
    export PIP_INDEX_URL="${PYPI_INDEX_URL}"; \
    export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}"; \
    poetry config virtualenvs.in-project true --local; \
    poetry config installer.max-workers 4 --local; \
    if ! grep -q 'name = "aliyun"' pyproject.toml; then \
      printf '\n[[tool.poetry.source]]\nname = "aliyun"\nurl = "%s"\npriority = "primary"\n' \
        "${PYPI_INDEX_URL}" >> pyproject.toml; \
    fi; \
    poetry lock; \
    poetry check --lock

# Install dependencies (retry for flaky PyPI / GitHub SDK zip). Mirrors via PYPI_INDEX_URL / USE_GITHUB_DOWNLOAD_MIRROR.
RUN --mount=type=cache,target=/.poetry-cache,id=poetry-cache-slim,sharing=locked \
    export PIP_INDEX_URL="${PYPI_INDEX_URL}"; \
    export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}"; \
    attempt=1 && max=10 && \
    while [ "$attempt" -le "$max" ]; do \
      if [ "$INCLUDE_DEV" = "true" ]; then \
        poetry install --no-root --extras uwsgi --with test -vv && break; \
      else \
        poetry install --no-root --without test --extras uwsgi -vv && break; \
      fi; \
      echo "poetry install failed (attempt $attempt/$max), retrying in 60s..."; \
      sleep 60; \
      attempt=$((attempt+1)); \
    done && \
    [ "$attempt" -le "$max" ]

# Install LS
COPY label_studio label_studio
RUN --mount=type=cache,target=/.poetry-cache,id=poetry-cache-slim,sharing=locked \
    if [ -n "${PYPI_INDEX_URL}" ]; then export PIP_INDEX_URL="${PYPI_INDEX_URL}"; fi; \
    if [ -n "${PIP_TRUSTED_HOST}" ]; then export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}"; fi; \
    attempt=1 && max=10 && \
    while [ "$attempt" -le "$max" ]; do \
      poetry install --only-root --extras uwsgi -vv && break; \
      echo "poetry install --only-root failed (attempt $attempt/$max), retrying in 60s..."; \
      sleep 60; \
      attempt=$((attempt+1)); \
    done && \
    [ "$attempt" -le "$max" ] && \
    python3 label_studio/manage.py collectstatic --no-input

################################ Stage: py-version-generator
FROM venv-builder AS py-version-generator
ARG VERSION_OVERRIDE
ARG BRANCH_OVERRIDE

# Create version_.py and ls-version_.py
RUN --mount=type=bind,source=.git,target=./.git \
    VERSION_OVERRIDE=${VERSION_OVERRIDE} BRANCH_OVERRIDE=${BRANCH_OVERRIDE} poetry run python label_studio/core/version.py

################################### Stage: prod
# Debian slim + nginx.org packages (same stack as LS 1.15+). Venv is glibc; must not copy into Alpine.
FROM python:${PYTHON_VERSION}-slim AS production
ARG DEBIAN_USE_MIRROR=""
ARG DEBIAN_APT_MAIN="http://mirrors.aliyun.com/debian"
ARG DEBIAN_APT_SECURITY="http://mirrors.aliyun.com/debian-security"
RUN set -eux; \
    if [ "$DEBIAN_USE_MIRROR" = "yes" ] || [ "$DEBIAN_USE_MIRROR" = "true" ] || [ "$DEBIAN_USE_MIRROR" = "1" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/debian.list; do \
        [ -f "$f" ] || continue; \
        sed -i \
          -e "s|http://deb.debian.org/debian|${DEBIAN_APT_MAIN}|g" \
          -e "s|https://deb.debian.org/debian|${DEBIAN_APT_MAIN}|g" \
          -e "s|http://security.debian.org/debian-security|${DEBIAN_APT_SECURITY}|g" \
          -e "s|https://security.debian.org/debian-security|${DEBIAN_APT_SECURITY}|g" \
          "$f"; \
      done; \
    fi

RUN printf '%s\n' \
      'Acquire::Retries "10";' \
      'Acquire::http::Timeout "120";' \
    > /etc/apt/apt.conf.d/99timeout-retries

ENV LS_DIR=/label-studio \
    HOME=/label-studio \
    LABEL_STUDIO_BASE_DATA_DIR=/label-studio/data \
    OPT_DIR=/opt/heartex/instance-data/etc \
    PATH="/label-studio/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=core.settings.label_studio \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR $LS_DIR

# Runtime libs (expat, curl, bash, procps, common numpy/opencv helpers), then nginx from nginx.org
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    apt-get update; \
    apt-get upgrade -y; \
    apt-get install --no-install-recommends -y \
      ca-certificates \
      curl \
      gnupg2 \
      gosu \
      libexpat1 \
      bash \
      procps \
      libglib2.0-0 \
      libgomp1 \
    ; \
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL https://nginx.org/keys/nginx_signing.key | gpg --dearmor -o /etc/apt/keyrings/nginx-archive-keyring.gpg; \
    DEBIAN_VERSION=$(awk -F '=' '/^VERSION_CODENAME=/ {print $2}' /etc/os-release); \
    printf "deb [signed-by=/etc/apt/keyrings/nginx-archive-keyring.gpg] http://nginx.org/packages/debian ${DEBIAN_VERSION} nginx\n" > /etc/apt/sources.list.d/nginx.list; \
    printf "Package: *\nPin: origin nginx.org\nPin: release o=nginx\nPin-Priority: 900\n" > /etc/apt/preferences.d/99nginx; \
    apt-get update; \
    apt-get install --no-install-recommends -y nginx; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p $LS_DIR $LABEL_STUDIO_BASE_DATA_DIR $OPT_DIR && \
    chown -R 1001:0 $LS_DIR $LABEL_STUDIO_BASE_DATA_DIR $OPT_DIR /var/log/nginx /etc/nginx

COPY --chown=1001:0 deploy/default.conf /etc/nginx/nginx.conf

# Copy essential files for installing Label Studio and its dependencies
COPY --chown=1001:0 pyproject.toml .
COPY --chown=1001:0 poetry.lock .
COPY --chown=1001:0 README.md .
COPY --chown=1001:0 LICENSE LICENSE
COPY --chown=1001:0 licenses licenses

# Copy files from build stages (venv stage supplies most of /label-studio)
COPY --chown=1001:0 --from=venv-builder               $LS_DIR                                           $LS_DIR
COPY --chown=1001:0 --from=py-version-generator       $LS_DIR/label_studio/core/version_.py             $LS_DIR/label_studio/core/version_.py
COPY --chown=1001:0 --from=frontend-builder           $LS_DIR/web/dist                                  $LS_DIR/web/dist
COPY --chown=1001:0 --from=frontend-version-generator $LS_DIR/web/dist/apps/labelstudio/version.json    $LS_DIR/web/dist/apps/labelstudio/version.json
COPY --chown=1001:0 --from=frontend-version-generator $LS_DIR/web/dist/libs/editor/version.json         $LS_DIR/web/dist/libs/editor/version.json
COPY --chown=1001:0 --from=frontend-version-generator $LS_DIR/web/dist/libs/datamanager/version.json    $LS_DIR/web/dist/libs/datamanager/version.json

# Overlay deploy/ after venv copy so entrypoint scripts are not overwritten by the builder tree.
COPY --chown=1001:0 deploy deploy

RUN chmod +x \
    /label-studio/deploy/docker-entrypoint.sh \
    /label-studio/deploy/docker-entrypoint-root.sh

# Root at start: docker-entrypoint-root.sh chowns bind-mounted data then gosu 1001 for app (nginx stays root).
USER root

EXPOSE 8080

ENTRYPOINT ["/label-studio/deploy/docker-entrypoint-root.sh"]
CMD ["label-studio"]
