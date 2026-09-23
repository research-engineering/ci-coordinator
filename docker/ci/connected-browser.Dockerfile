FROM node:24.21.0-trixie-slim@sha256:8ec5d7557396cfe32d21c3f9c13072355ceab22b584578ca4bb28af31120cffe
ENV COREPACK_HOME=/opt/corepack \
    NODE_USE_ENV_PROXY=1 \
    PNPM_HOME=/opt/pnpm \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PATH=/opt/pnpm:$PATH
WORKDIR /workspace
RUN mkdir -p "$COREPACK_HOME" "$PNPM_HOME" \
    && npm install --global --ignore-scripts corepack@0.36.0 \
    && corepack enable \
    && corepack prepare pnpm@12.5.1 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json ./frontend/package.json
COPY patches ./patches
RUN pnpm install --frozen-lockfile
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libnss3-tools=2:3.110-1+deb13u4 \
    && pnpm --dir frontend exec playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY frontend ./frontend
WORKDIR /workspace/frontend
ENV HOME=/tmp/browser-home \
    XDG_CACHE_HOME=/tmp/browser-home/.cache \
    XDG_CONFIG_HOME=/tmp/browser-home/.config \
    TMPDIR=/tmp
CMD ["/bin/sh", "-euc", "umask 077; mkdir -p \"$HOME/.pki/nssdb\" \"$XDG_CACHE_HOME\" \"$XDG_CONFIG_HOME\"; certutil -N --empty-password -d \"sql:$HOME/.pki/nssdb\"; certutil -A -d \"sql:$HOME/.pki/nssdb\" -n connected-witness -t C,, -i /fixture/ca.pem; exec pnpm exec playwright test --config connected.playwright.config.ts"]
