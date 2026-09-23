#!/usr/bin/env bash
set -euo pipefail

sudo chown vscode:vscode \
  backend/.venv \
  "${HOME}/.local" \
  "${HOME}/.local/share" \
  "${HOME}/.local/share/mise" \
  .pnpm-store \
  node_modules \
  frontend/node_modules

mise trust
CI=true mise run install
