#!/usr/bin/env bash
set -euo pipefail

duration="${1:-300}"
interval="${2:-5}"

if ! [[ "$duration" =~ ^[0-9]+$ && "$interval" =~ ^[0-9]+$ ]] || (( duration < 1 || interval < 1 )); then
  echo "Usage: $0 [duration_seconds] [interval_seconds]" >&2
  exit 2
fi

deadline=$(( $(date +%s) + duration ))
echo "timestamp,load1,load5,load15,mem_available_kb,swap_used_kb,disk_free_kb,yt_cpu,yt_mem"

while (( $(date +%s) < deadline )); do
  read -r load1 load5 load15 _ < /proc/loadavg
  mem_available="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
  swap_total="$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)"
  swap_free="$(awk '/SwapFree:/ {print $2}' /proc/meminfo)"
  disk_free="$(df -Pk / | awk 'NR==2 {print $4}')"
  docker_values="$(docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' yt-loader 2>/dev/null || true)"
  yt_cpu="$(printf '%s' "$docker_values" | cut -d, -f2)"
  yt_mem="$(printf '%s' "$docker_values" | cut -d, -f3 | tr ',' ';')"
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$load1" "$load5" "$load15" \
    "$mem_available" "$((swap_total - swap_free))" "$disk_free" "$yt_cpu" "$yt_mem"
  sleep "$interval"
done
