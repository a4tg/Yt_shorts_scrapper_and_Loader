#!/usr/bin/env bash
set -euo pipefail

project_dir="${YT_LOADER_PROJECT_DIR:-/opt/yt-loader}"
backup_dir="${YT_LOADER_BACKUP_DIR:-/var/backups/yt-loader}"
retention_days="${YT_LOADER_BACKUP_RETENTION_DAYS:-14}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$backup_dir"
cd "$project_dir"

docker compose exec -T postgres pg_dump -U yt_loader -d yt_loader -Fc > "$backup_dir/postgres-$timestamp.dump"
tar --exclude='*.part' -C "$project_dir" -czf "$backup_dir/files-$timestamp.tar.gz" server_data
find "$backup_dir" -type f \( -name 'postgres-*.dump' -o -name 'files-*.tar.gz' \) -mtime "+$retention_days" -delete

echo "Backup completed: $timestamp"
