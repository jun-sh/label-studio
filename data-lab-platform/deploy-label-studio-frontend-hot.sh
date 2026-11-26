#!/usr/bin/env bash
# Retired 2026-11-26. This script used `docker cp` into running containers; every
# container recreate silently reverted the frontend (nginx served a June 2026 bundle
# for months). The frontend is now built inside the Label Studio image:
#
#   docker build --target production --build-arg USE_PREBUILT_FRONTEND=no \
#     -t data-lab-label-studio:<date>-<sha> .
#   # set the tag in docker-compose.yml (LABEL_STUDIO_IMAGE default), then:
#   docker compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml up -d app nginx
#
# See data-lab-platform/docs/architecture-review-2026-11.md (P0-1).
echo "deploy-label-studio-frontend-hot.sh is retired; build the image instead (see header)." >&2
exit 1
