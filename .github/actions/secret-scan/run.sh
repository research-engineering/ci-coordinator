#!/usr/bin/env bash
set -euo pipefail

scratch=$(mktemp -d "$RUNNER_TEMP/secret-scan.XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
binary=$(bash "$SECRET_SCAN_ACTION_PATH/install.sh" "$scratch/tool")
python3 -I "$SECRET_SCAN_ACTION_PATH/entrypoint.py" \
  --gitleaks "$binary" --root "$SECRET_SCAN_SOURCE" \
  --history "$SECRET_SCAN_HISTORY" --base "$SECRET_SCAN_BASE" --head "$SECRET_SCAN_HEAD"
