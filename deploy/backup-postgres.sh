#!/usr/bin/env sh
set -eu

umask 077
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_dir/backups"}
retention_days=${BACKUP_RETENTION_DAYS:-14}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$backup_dir/yt_loader_$timestamp.dump"
temporary="$target.part"

mkdir -p "$backup_dir"
cd "$project_dir"
trap 'rm -f "$temporary"' EXIT HUP INT TERM

docker compose exec -T postgres pg_dump \
  --username=yt_loader --dbname=yt_loader --format=custom --no-owner --no-acl \
  > "$temporary"

test -s "$temporary"
docker compose exec -T postgres pg_restore --list < "$temporary" > /dev/null
mv "$temporary" "$target"
trap - EXIT HUP INT TERM

find "$backup_dir" -type f -name 'yt_loader_*.dump' -mtime "+$retention_days" -delete
printf '%s\n' "$target"
