#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="${YT_LOADER_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
env_file="${YT_LOADER_ENV_FILE:-$project_dir/.env}"
backup_dir="${YT_LOADER_BACKUP_DIR:-/var/backups/yt-loader}"
expected_commit="${AAP_RELEASE_COMMIT:-}"
public_url="${YT_LOADER_PUBLIC_BASE_URL:-}"

cd "$project_dir"

fail() {
  echo "Production rollout: FAIL — $*" >&2
  docker compose ps >&2 || true
  docker compose logs --tail=120 yt-loader migrate >&2 || true
  exit 1
}
trap 'fail "command failed at line $LINENO"' ERR

command -v docker >/dev/null || fail "docker is unavailable"
command -v python3 >/dev/null || fail "python3 is unavailable"
command -v curl >/dev/null || fail "curl is unavailable"
test -f "$env_file" || fail "environment file not found: $env_file"

dirty="$(git status --porcelain --untracked-files=no)"
test -z "$dirty" || fail "tracked server files contain local changes"

current_commit="$(git rev-parse HEAD)"
if [[ -n "$expected_commit" && "$current_commit" != "$expected_commit" ]]; then
  fail "HEAD $current_commit does not match AAP_RELEASE_COMMIT $expected_commit"
fi

echo "1/6 Validating production environment"
python3 deploy/production_preflight.py \
  --env-file "$env_file" \
  --commercial

echo "2/6 Validating and building containers"
docker compose config --quiet
docker compose build

echo "3/6 Creating pre-deployment backup"
YT_LOADER_PROJECT_DIR="$project_dir" \
YT_LOADER_BACKUP_DIR="$backup_dir" \
  bash deploy/backup-data.sh

echo "4/6 Starting the release"
docker compose up -d --remove-orphans

echo "5/6 Waiting for application readiness"
ready=0
for _attempt in $(seq 1 60); do
  if curl -fsS --max-time 5 \
    http://127.0.0.1:8000/api/health/ready \
    >/tmp/allasplanned-rollout-ready.json 2>/dev/null; then
    if python3 -c '
import json
from pathlib import Path
payload = json.loads(Path("/tmp/allasplanned-rollout-ready.json").read_text())
required = ("status", "database", "workers", "disk")
raise SystemExit(0 if all(payload.get(key) == "ok" for key in required) else 1)
'; then
      ready=1
      break
    fi
  fi
  sleep 2
done
rm -f /tmp/allasplanned-rollout-ready.json
test "$ready" -eq 1 || fail "readiness did not become healthy within 120 seconds"

docker compose exec -T yt-loader \
  alembic current |
  grep -q 't5i6j7k8l9m0' ||
  fail "database is not at the expected Alembic revision"

echo "6/6 Verifying the deployed domain"
if [[ -z "$public_url" ]]; then
  public_url="$(
    sed -n 's/^YT_LOADER_PUBLIC_BASE_URL=//p' "$env_file" |
      tail -n 1
  )"
  public_url="${public_url#\"}"
  public_url="${public_url%\"}"
  public_url="${public_url#\'}"
  public_url="${public_url%\'}"
fi
test -n "$public_url" || fail "YT_LOADER_PUBLIC_BASE_URL is empty"
curl -fsS --max-time 15 "$public_url/api/health/ready" >/dev/null ||
  fail "public readiness endpoint is unavailable"

if [[ -n "${AAP_SMOKE_EMAIL:-}" && -n "${AAP_SMOKE_PASSWORD:-}" ]]; then
  python3 deploy/production_smoke.py \
    --base-url "$public_url" \
    --require-ai \
    --commercial
else
  echo "Authenticated smoke skipped: export AAP_SMOKE_EMAIL and AAP_SMOKE_PASSWORD to run it."
fi

docker compose ps
echo "Production rollout: PASS — $current_commit"
