#!/bin/sh
set -eu

if [ "$#" -lt 3 ]; then
  echo "development secret entrypoint requires uid, gid, and command" >&2
  exit 64
fi

runtime_uid="$1"
runtime_gid="$2"
shift 2

case "$runtime_uid:$runtime_gid" in
  *[!0-9:]* | :* | *:)
    echo "development runtime identity is invalid" >&2
    exit 64
    ;;
esac

source_directory=/run/secrets
target_directory=/run/ci-coordinator-secrets
runtime_home=/run/ci-coordinator-home
if [ ! -d "$source_directory" ]; then
  echo "development secret source is unavailable" >&2
  exit 66
fi

mkdir -p "$target_directory"
chown "$runtime_uid:$runtime_gid" "$target_directory"
chmod 0700 "$target_directory"
mkdir -p "$runtime_home"
chown "$runtime_uid:$runtime_gid" "$runtime_home"
chmod 0700 "$runtime_home"
export HOME="$runtime_home"

copied=0
for source_path in "$source_directory"/*; do
  [ -f "$source_path" ] || continue
  [ ! -L "$source_path" ] || {
    echo "development secret source is invalid" >&2
    exit 66
  }
  filename=${source_path##*/}
  case "$filename" in
    *[!A-Za-z0-9._-]* | "")
      echo "development secret filename is invalid" >&2
      exit 66
      ;;
  esac
  install -m 0400 -o "$runtime_uid" -g "$runtime_gid" \
    "$source_path" "$target_directory/$filename"
  copied=$((copied + 1))
done

if [ "$copied" -eq 0 ]; then
  echo "development secret set is empty" >&2
  exit 66
fi

exec setpriv \
  --reuid="$runtime_uid" \
  --regid="$runtime_gid" \
  --clear-groups \
  --bounding-set=-all \
  --no-new-privs \
  "$@"
