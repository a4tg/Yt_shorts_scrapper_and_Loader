#!/usr/bin/env bash
set -u

config_file="${AAP_MONITOR_ENV_FILE:-/etc/allasplanned-monitor.env}"
if [ ! -r "$config_file" ]; then
  echo "Monitor config is not readable: $config_file" >&2
  exit 2
fi

# shellcheck disable=SC1090
source "$config_file"

: "${TELEGRAM_BOT_TOKEN:?Set TELEGRAM_BOT_TOKEN in $config_file}"
: "${TELEGRAM_CHAT_ID:?Set TELEGRAM_CHAT_ID in $config_file}"

monitor_name="${AAP_MONITOR_NAME:-All As Planned}"
health_url="${AAP_MONITOR_HEALTH_URL:-https://allasplanned.ru/api/health/ready}"
timeout_seconds="${AAP_MONITOR_TIMEOUT_SECONDS:-15}"
failure_threshold="${AAP_MONITOR_FAILURE_THRESHOLD:-3}"
reminder_failures="${AAP_MONITOR_REMINDER_FAILURES:-60}"
state_dir="${AAP_MONITOR_STATE_DIR:-/var/lib/allasplanned-monitor}"
local_checks="${AAP_MONITOR_LOCAL_CHECKS:-false}"
project_dir="${AAP_MONITOR_PROJECT_DIR:-/opt/yt-loader}"
backup_dir="${AAP_MONITOR_BACKUP_DIR:-/var/backups/yt-loader}"
backup_max_age_hours="${AAP_MONITOR_BACKUP_MAX_AGE_HOURS:-36}"

case "$failure_threshold:$reminder_failures:$timeout_seconds:$backup_max_age_hours" in
  *[!0-9:]*)
    echo "Monitor numeric settings must contain positive integers" >&2
    exit 2
    ;;
esac

if [ "$failure_threshold" -lt 1 ] || [ "$timeout_seconds" -lt 1 ]; then
  echo "Failure threshold and timeout must be at least 1" >&2
  exit 2
fi

mkdir -p "$state_dir"
chmod 700 "$state_dir"
status_file="$state_dir/status"
failures_file="$state_dir/failures"
last_status="$(cat "$status_file" 2>/dev/null || printf 'unknown')"
failures="$(cat "$failures_file" 2>/dev/null || printf '0')"
case "$failures" in
  ''|*[!0-9]*) failures=0 ;;
esac

telegram_send() {
  message="$1"
  curl --fail --silent --show-error \
    --max-time "$timeout_seconds" \
    --request POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${message}" \
    --data "disable_web_page_preview=true" \
    >/dev/null
}

timestamp="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
host_name="$(hostname -f 2>/dev/null || hostname)"

if [ "${1:-}" = "--test" ]; then
  telegram_send "✅ Тест мониторинга
${monitor_name}
Сервер: ${host_name}
Время: ${timestamp}"
  echo "Telegram test notification sent"
  exit 0
fi

errors=""
add_error() {
  if [ -n "$errors" ]; then
    errors="${errors}; $1"
  else
    errors="$1"
  fi
}

health_response="$(mktemp)"
health_error="$(mktemp)"
trap 'rm -f "$health_response" "$health_error"' EXIT

if ! curl --fail --silent --show-error \
  --max-time "$timeout_seconds" \
  "$health_url" >"$health_response" 2>"$health_error"; then
  detail="$(tr '\n' ' ' <"$health_error" | cut -c1-240)"
  add_error "health endpoint недоступен${detail:+: $detail}"
elif ! grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$health_response"; then
  detail="$(tr '\n' ' ' <"$health_response" | cut -c1-240)"
  add_error "readiness вернул непредвиденный ответ: $detail"
fi

if [ "$local_checks" = "true" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    add_error "Docker не найден"
  else
    app_state="$(docker inspect \
      --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
      yt-loader 2>/dev/null || true)"
    case "$app_state" in
      "running healthy"|"running ") ;;
      *) add_error "контейнер yt-loader: ${app_state:-не найден}" ;;
    esac

    postgres_id=""
    if [ -f "$project_dir/docker-compose.yml" ]; then
      postgres_id="$(docker compose \
        --project-directory "$project_dir" \
        ps -q postgres 2>/dev/null || true)"
    fi
    if [ -z "$postgres_id" ]; then
      add_error "контейнер PostgreSQL не найден"
    else
      postgres_state="$(docker inspect \
        --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
        "$postgres_id" 2>/dev/null || true)"
      case "$postgres_state" in
        "running healthy"|"running ") ;;
        *) add_error "контейнер PostgreSQL: ${postgres_state:-не найден}" ;;
      esac
    fi
  fi

  newest_backup="$(find "$backup_dir" -maxdepth 1 -type f \
    \( -name 'postgres-*.dump' -o -name 'files-*.tar.gz' \) \
    -printf '%T@\n' 2>/dev/null | sort -nr | head -n 1)"
  if [ -z "$newest_backup" ]; then
    add_error "резервная копия не найдена"
  else
    newest_epoch="${newest_backup%%.*}"
    backup_age_seconds="$(( $(date +%s) - newest_epoch ))"
    if [ "$backup_age_seconds" -gt "$((backup_max_age_hours * 3600))" ]; then
      backup_age_hours="$((backup_age_seconds / 3600))"
      add_error "резервная копия устарела: ${backup_age_hours} ч."
    fi
  fi
fi

if [ -z "$errors" ]; then
  printf '0\n' >"$failures_file"
  if [ "$last_status" = "down" ]; then
    if telegram_send "✅ Сервис восстановлен
${monitor_name}
Проверка: ${health_url}
Сервер: ${host_name}
Время: ${timestamp}"; then
      printf 'up\n' >"$status_file"
    else
      echo "Service recovered, but Telegram delivery failed" >&2
      exit 1
    fi
  else
    printf 'up\n' >"$status_file"
  fi
  echo "Monitor check passed"
  exit 0
fi

failures="$((failures + 1))"
printf '%s\n' "$failures" >"$failures_file"
should_notify=false
if [ "$failures" -eq "$failure_threshold" ]; then
  should_notify=true
elif [ "$reminder_failures" -gt 0 ] \
  && [ "$failures" -gt "$failure_threshold" ] \
  && [ "$((failures % reminder_failures))" -eq 0 ]; then
  should_notify=true
fi

if [ "$should_notify" = "true" ]; then
  if telegram_send "🚨 Сбой сервиса
${monitor_name}
Причина: ${errors}
Неудачных проверок подряд: ${failures}
Сервер: ${host_name}
Время: ${timestamp}"; then
    printf 'down\n' >"$status_file"
  else
    # Retry the first alert on the next timer run.
    if [ "$failures" -eq "$failure_threshold" ]; then
      printf '%s\n' "$((failure_threshold - 1))" >"$failures_file"
    fi
    echo "Monitor failed and Telegram delivery failed: $errors" >&2
    exit 1
  fi
fi

echo "Monitor check failed ($failures/$failure_threshold): $errors" >&2
exit 1
