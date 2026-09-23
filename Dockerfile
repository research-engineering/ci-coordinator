FROM ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc AS uv

FROM node:24.21.0-trixie-slim@sha256:8ec5d7557396cfe32d21c3f9c13072355ceab22b584578ca4bb28af31120cffe AS operator-ui
ENV COREPACK_HOME=/opt/corepack \
    NODE_USE_ENV_PROXY=1 \
    PNPM_HOME=/opt/pnpm \
    PATH=/opt/pnpm:$PATH
WORKDIR /workspace
RUN mkdir -p "$COREPACK_HOME" "$PNPM_HOME" \
    && npm install --global --ignore-scripts corepack@0.36.0 \
    && corepack enable \
    && corepack prepare pnpm@12.5.1 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json ./frontend/package.json
COPY patches ./patches
RUN --mount=type=cache,id=runtime-pnpm-amd64,target=/pnpm/store,sharing=locked \
    pnpm install --frozen-lockfile --store-dir /pnpm/store
COPY frontend ./frontend
RUN pnpm --filter @ci-coordinator/operator-ui build:assets

FROM python:3.13.15-slim-trixie@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0 AS upstream-python

FROM ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78 AS interpreter
COPY --from=upstream-python /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY docker/runtime/ubuntu-snapshot.conf /etc/apt/apt.conf.d/50snapshot
COPY docker/runtime/install.sh /tmp/install-runtime.sh
RUN /bin/sh /tmp/install-runtime.sh runtime
COPY --from=upstream-python /usr/local/ /usr/local/
RUN ldconfig

FROM interpreter AS security-repairs
ENV SOURCE_DATE_EPOCH=1789604463
RUN /bin/sh /tmp/install-runtime.sh security
COPY docker/runtime/security /security
RUN /bin/bash /security/build.sh

FROM scratch AS repair-witnesses
COPY --from=security-repairs /out/check_zlib /check_zlib
COPY --from=security-repairs /out/python-before.json /out/python-after.json /out/zlib-before.json /out/zlib-after.json /
COPY --from=security-repairs /out/stdlib-before.json /out/stdlib-after.json /

FROM interpreter AS dependencies
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
COPY --from=uv /uv /uvx /bin/
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,id=runtime-uv-amd64-313,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-install-project --no-build

FROM dependencies AS build
COPY --from=dependencies /app/backend/.venv /dependency-venv
COPY backend/src ./src
COPY --from=operator-ui /workspace/frontend/dist ./src/ci_coordinator/api/http/static
COPY scripts/frontend_bundle.py /tmp/frontend_bundle.py
RUN python /tmp/frontend_bundle.py --write-manifest ./src/ci_coordinator/api/http/static \
    && python /tmp/frontend_bundle.py ./src/ci_coordinator/api/http/static
ARG CI_COORDINATOR_RELEASE_ID=0000000000000000000000000000000000000000000000000000000000000000
ARG CI_COORDINATOR_SOURCE_COMMIT=0000000000000000000000000000000000000000
ARG CI_COORDINATOR_PRODUCTION_BUILD=false
RUN set --; \
    if test "$CI_COORDINATOR_PRODUCTION_BUILD" = true; then set -- --production-eligible; fi; \
    PYTHONPATH=src .venv/bin/python -c \
    'from ci_coordinator.runtime_settings.build_identity import main; main()' \
    --output src/ci_coordinator/runtime_settings/resources/build-identity.v1.json \
    --release-identity "$CI_COORDINATOR_RELEASE_ID" \
    --source-commit "$CI_COORDINATOR_SOURCE_COMMIT" "$@"
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
RUN --mount=type=cache,id=runtime-uv-amd64-313,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev
COPY docker/runtime/application_layer.py /tmp/application_layer.py
RUN .venv/bin/python -I -B /tmp/application_layer.py
COPY LICENSE NOTICE /application-layer/usr/share/licenses/ci-coordinator/

FROM dependencies AS runtime-files
RUN /bin/sh /tmp/install-runtime.sh assembly
COPY docker/runtime/assemble.sh /tmp/assemble.sh
COPY docker/runtime/package_metadata.py /tmp/package_metadata.py
COPY --from=security-repairs /out/stdlib /repairs/stdlib
COPY --from=security-repairs /out/zlib1g.deb /repairs/zlib1g.deb
COPY docker/runtime/security /security
RUN dpkg --install /repairs/zlib1g.deb \
    && /bin/bash /tmp/assemble.sh \
    && python -I -B /security/manifest.py

FROM scratch
ENV PATH=/app/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    LANG=C.UTF-8 \
    HOME=/home/ci-coordinator
COPY --from=runtime-files /runtime/ /
COPY --from=runtime-files /dependency-layer/ /
COPY --from=build /application-layer/ /
WORKDIR /app/backend
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-m", "ci_coordinator.runtime.healthcheck"]
CMD ["python", "-m", "ci_coordinator.runtime"]
