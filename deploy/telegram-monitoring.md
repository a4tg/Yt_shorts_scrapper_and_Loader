# Telegram monitoring

The monitor is intended to run on a server independent from production. It
checks the public readiness endpoint every minute. The endpoint already covers
the database, background workers and the minimum free space required by the
application.

The monitor sends:

- one alert after three consecutive failed checks;
- a reminder after 60 failed checks;
- one recovery notification;
- no messages while the service remains healthy.

The Telegram token and chat ID must exist only in
`/etc/allasplanned-monitor.env` with mode `600`.

## 1. Get the chat ID

Open the bot in Telegram and send `/start`. On the monitoring server, create
the protected configuration from the example:

```bash
install -m 600 \
  /opt/yt-loader/deploy/allasplanned-monitor.env.example \
  /etc/allasplanned-monitor.env

nano /etc/allasplanned-monitor.env
```

Set `TELEGRAM_BOT_TOKEN`, save the file, and run:

```bash
TOKEN="$(
  sed -n 's/^TELEGRAM_BOT_TOKEN=//p' \
    /etc/allasplanned-monitor.env |
  tail -n 1
)"

curl -fsS \
  "https://api.telegram.org/bot${TOKEN}/getUpdates" |
python3 -c '
import json
import sys

payload = json.load(sys.stdin)
chats = {}
for update in payload.get("result", []):
    message = update.get("message") or update.get("channel_post") or {}
    chat = message.get("chat") or {}
    if "id" in chat:
        chats[chat["id"]] = (
            chat.get("title")
            or chat.get("username")
            or chat.get("first_name")
            or "private chat"
        )
if not chats:
    raise SystemExit("Chat not found. Send /start to the bot and retry.")
for chat_id, name in chats.items():
    print(f"{chat_id}: {name}")
'

unset TOKEN
```

Copy the required numeric ID into `TELEGRAM_CHAT_ID` in the protected
configuration.

## 2. Install on the independent server

If the repository is checked out at `/opt/yt-loader`, install the script and
systemd units:

```bash
install -m 700 \
  /opt/yt-loader/deploy/telegram-monitor.sh \
  /usr/local/sbin/allasplanned-monitor

install -m 644 \
  /opt/yt-loader/deploy/allasplanned-monitor.service \
  /etc/systemd/system/allasplanned-monitor.service

install -m 644 \
  /opt/yt-loader/deploy/allasplanned-monitor.timer \
  /etc/systemd/system/allasplanned-monitor.timer

systemctl daemon-reload
```

For an independent server, keep this value:

```dotenv
AAP_MONITOR_LOCAL_CHECKS=false
```

Test Telegram delivery before enabling the timer:

```bash
/usr/local/sbin/allasplanned-monitor --test
```

Then start scheduled monitoring:

```bash
systemctl enable --now allasplanned-monitor.timer
systemctl start allasplanned-monitor.service

systemctl status allasplanned-monitor.timer --no-pager
journalctl -u allasplanned-monitor.service -n 50 --no-pager
```

## 3. Optional production-local checks

The same monitor can run on production with a separate Telegram configuration
and state directory. Set:

```dotenv
AAP_MONITOR_NAME="All As Planned production internal"
AAP_MONITOR_LOCAL_CHECKS=true
AAP_MONITOR_PROJECT_DIR="/opt/yt-loader"
AAP_MONITOR_BACKUP_DIR="/var/backups/yt-loader"
AAP_MONITOR_BACKUP_MAX_AGE_HOURS=36
```

This additionally checks the application and PostgreSQL containers and the
freshness of local PostgreSQL/file backups. The independent external check
should remain enabled because a monitor located only on production cannot
report a complete server outage.

## Diagnostics

Run a check manually:

```bash
systemctl start allasplanned-monitor.service
journalctl -u allasplanned-monitor.service -n 50 --no-pager
```

Inspect timer scheduling:

```bash
systemctl list-timers allasplanned-monitor.timer --all
```

The state is stored in `/var/lib/allasplanned-monitor`. Do not edit it during
normal operation.
