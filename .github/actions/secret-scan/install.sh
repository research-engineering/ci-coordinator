#!/usr/bin/env bash
set -euo pipefail

action_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
destination=${1:?An empty installation directory is required}
mkdir -m 700 -- "$destination"
destination=$(cd -- "$destination" && pwd)
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) platform=linux_x64 ;;
  Darwin-arm64) platform=darwin_arm64 ;;
  *) printf '%s\n' 'Unsupported secret scanner platform' >&2; exit 2 ;;
esac
archive="gitleaks_8.30.1_${platform}.tar.gz"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  --connect-timeout 15 --max-time 90 --max-filesize 33554432 \
  "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/$archive" \
  --output "$destination/$archive"
(
  cd -- "$destination"
  awk -v archive="$archive" '$2 == archive { print; found++ } END { if (found != 1) exit 1 }' \
    "$action_root/checksums.txt" > archive.sha256
  shasum -a 256 --check --status archive.sha256
  tar -xzf "$archive" gitleaks
  test -f gitleaks && test ! -L gitleaks
  chmod 700 gitleaks
  rm -- "$archive" archive.sha256
)
printf '%s\n' "$destination/gitleaks"
