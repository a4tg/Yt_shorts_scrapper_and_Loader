#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ] || [ ! -s "$1" ]; then
  echo "Usage: RESTORE_CONFIRM=yt_loader ./deploy/restore-postgres.sh BACKUP.dump" >&2
  exit 2
fi
if [ "${RESTORE_CONFIRM:-}" != "yt_loader" ]; then
  echo "Restore replaces the current database. Set RESTORE_CONFIRM=yt_loader." >&2
  exit 2
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_file=$(CDPATH= cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")
cd "$project_dir"

docker compose exec -T postgres pg_restore --list < "$backup_file" > /dev/null
docker compose stop yt-loader
docker compose exec -T postgres dropdb --username=yt_loader --force --if-exists yt_loader
docker compose exec -T postgres createdb --username=yt_loader --owner=yt_loader yt_loader
docker compose exec -T postgres pg_restore \
  --username=yt_loader --dbname=yt_loader --no-owner --no-acl --exit-on-error \
  < "$backup_file"
docker compose run --rm migrate
docker compose up -d yt-loader
docker compose exec -T yt-loader python manage_users.py audit-payments
docker compose exec -T yt-loader python manage_users.py audit-credits
