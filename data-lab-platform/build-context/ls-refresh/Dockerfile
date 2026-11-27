# Phase 0 interim image: current production image + freshly built frontend and
# Data Lab Django overrides baked in (no docker cp, no static_build bind mount).
# Phase 1 replaces this with a full from-source CI build.
FROM data-lab-label-studio:base-20260701
USER root
RUN rm -rf /label-studio/web/dist
COPY --chown=1001:0 web-dist/ /label-studio/web/dist/
COPY --chown=1001:0 label_studio/ /label-studio/label_studio/
COPY --chown=1001:0 version_.py /label-studio/label_studio/core/version_.py
RUN cd /label-studio && rm -rf label_studio/core/static_build && \
    .venv/bin/python label_studio/manage.py collectstatic --no-input -v 0 && \
    chown -R 1001:0 label_studio/core/static_build && \
    test -f web/dist/apps/labelstudio/main.js && test -f label_studio/core/static_build/images/data_lab_logo.svg
