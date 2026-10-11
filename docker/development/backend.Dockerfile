FROM ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc AS uv

FROM python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS application
ENV PATH=/workspace/backend/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /workspace
COPY --from=uv /uv /uvx /bin/
COPY --chmod=0555 docker/development/secret-entrypoint.sh /usr/local/bin/development-secret-entrypoint
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN uv sync --project backend --frozen --no-dev --no-install-project
COPY --chown=65532:65532 backend/src ./backend/src
COPY --chown=65532:65532 backend/alembic ./backend/alembic
COPY --chown=65532:65532 backend/alembic.ini ./backend/alembic.ini
RUN find backend/src -type d \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf -- {} + \
    && uv sync --project backend --frozen --no-dev
WORKDIR /workspace/backend
ENTRYPOINT ["/usr/local/bin/development-secret-entrypoint", "65532", "65532"]
CMD ["python", "-m", "ci_coordinator.runtime"]

FROM application AS debug
RUN uv sync --project /workspace/backend --frozen --no-dev --group debug

# The default target must never inherit the optional debugger layer.
FROM application AS development
