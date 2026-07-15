#!/usr/bin/env sh
set -eu

# First transition from the legacy Basic Auth/JSON deployment to PostgreSQL.
# Run from the repository after a clean `git pull --ff-only`.

umask 077
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

if [ ! -f .env ]; then
  echo ".env is missing. Copy .env.example and configure it first." >&2
  exit 2
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Tracked files have local changes. Abort before replacing the running service." >&2
  exit 2
fi

env_value() {
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r'
}

postgres_password=$(env_value POSTGRES_PASSWORD)
case "$postgres_password" in
  ""|replace-*|*[!A-Za-z0-9_.~-]*)
    echo "POSTGRES_PASSWORD must be a URL-safe random value (A-Z, a-z, 0-9, _ . ~ -)." >&2
    exit 2
    ;;
esac
if [ "${#postgres_password}" -lt 24 ]; then
  echo "POSTGRES_PASSWORD must contain at least 24 characters." >&2
  exit 2
fi

public_url=$(env_value YT_LOADER_PUBLIC_BASE_URL)
case "$public_url" in
  https://*) ;;
  *) echo "YT_LOADER_PUBLIC_BASE_URL must be the public HTTPS URL." >&2; exit 2 ;;
esac

secure_cookies=$(env_value YT_LOADER_SECURE_COOKIES)
if [ "$secure_cookies" != "true" ]; then
  echo "YT_LOADER_SECURE_COOKIES=true is required for production." >&2
  exit 2
fi
if [ -z "$(env_value YT_LOADER_ALLOWED_HOSTS)" ]; then
  echo "YT_LOADER_ALLOWED_HOSTS must contain the public hostname, localhost and 127.0.0.1." >&2
  exit 2
fi

verification=$(env_value YT_LOADER_REQUIRE_EMAIL_VERIFICATION)
if [ "$verification" = "true" ]; then
  if [ -z "$(env_value SMTP_HOST)" ] || [ -z "$(env_value SMTP_FROM_EMAIL)" ]; then
    echo "Email verification is enabled, but SMTP_HOST/SMTP_FROM_EMAIL are missing." >&2
    exit 2
  fi
elif [ "$verification" != "false" ]; then
  echo "Set YT_LOADER_REQUIRE_EMAIL_VERIFICATION explicitly to true or false." >&2
  exit 2
fi

docker compose config --quiet

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_root=${PREUPGRADE_BACKUP_DIR:-/var/backups/yt-loader-pre-saas}
backup_dir="$backup_root/$timestamp"
mkdir -p "$backup_dir"
git rev-parse HEAD > "$backup_dir/git-commit.txt"
cp .env "$backup_dir/env.backup"

paths=""
for path in server_data cookies; do
  if [ -e "$path" ]; then
    paths="$paths $path"
  fi
done
if [ -n "$paths" ]; then
  # shellcheck disable=SC2086 -- paths are fixed repository directory names.
  tar -czf "$backup_dir/legacy-data.tar.gz" $paths
  test -s "$backup_dir/legacy-data.tar.gz"
fi

echo "Legacy backup created in $backup_dir"
echo "Building the new image while the old container is still serving requests..."
docker compose build

echo "Starting PostgreSQL and applying migrations before switching the application..."
docker compose up -d postgres
docker compose run --rm migrate

echo "Switching the application container..."
docker compose up -d --no-deps yt-loader

deadline=$(( $(date +%s) + 90 ))
until health=$(curl -fsS http://127.0.0.1:8000/api/health 2>/dev/null); do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "The new application did not become healthy within 90 seconds." >&2
    docker compose logs --tail=100 yt-loader >&2
    echo "Legacy backup: $backup_dir" >&2
    exit 1
  fi
  sleep 2
done

echo "$health"
docker compose exec -T yt-loader alembic current
docker compose exec -T yt-loader python manage_users.py audit-payments
docker compose exec -T yt-loader python manage_users.py audit-credits
docker compose ps
echo "First SaaS upgrade completed. Legacy backup: $backup_dir"
