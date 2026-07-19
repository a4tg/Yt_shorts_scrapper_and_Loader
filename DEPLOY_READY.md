# Deploy-ready передача All As Planned

Эта версия подготовлена для следующего обновления сервера, но production в
рамках разработки не изменялся.

## Что входит в кандидат

- проверенные рабочие модули Workspace Depth;
- приватная медиатека готовых видео и быстрый импорт метаданных;
- последовательная пакетная видеоочередь;
- onboarding, демо-проект, поддержка и продуктовые события;
- админские задания, обращения, начисления, возвраты и аудит;
- публичные юридические документы и сохранение согласия при регистрации;
- fail-safe блокировка ЮKassa до заполнения и проверки реквизитов.

Головная миграция Alembic: `s4h5i6j7k8l9`.

## Локальная проверка кандидата

Windows:

```powershell
.\deploy\release-gate.ps1
```

Linux:

```bash
chmod +x deploy/release-gate.sh
./deploy/release-gate.sh
```

Перед закрытой бетой проверить production `.env` без вывода секретов:

```bash
python3 deploy/production_preflight.py --env-file .env
```

Перед включением реальных платежей использовать строгий режим:

```bash
python3 deploy/production_preflight.py --env-file .env --commercial
```

После финального коммита можно добавить `--require-clean`. Локальный каталог
`reports/` намеренно игнорируется проверкой и не входит в релиз.

## Будущее обновление production

Выполнять только после отдельного решения владельца:

```bash
cd /opt/yt-loader
git status --short

umask 077
YT_LOADER_BACKUP_DIR=/var/backups/yt-loader \
  ./deploy/backup-data.sh
/usr/local/sbin/aap-restic-backup

git pull --ff-only origin main
POSTGRES_PASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' .env | tail -n 1)" \
  docker compose config --quiet
docker compose up -d --build
docker compose ps

docker compose exec -T yt-loader alembic current
curl -fsS https://allasplanned.ru/api/health
curl -fsS https://allasplanned.ru/api/health/ready
curl -fsS https://allasplanned.ru/api/legal/config
```

Ожидается миграция `s4h5i6j7k8l9`, healthy-контейнеры и `status=ok`.

## Что не включать автоматически

Оставить `YT_LOADER_ENABLE_PAYMENTS=false`, пока не пройдены тестовый магазин
ЮKassa и проверка документов по `deploy/commercial-readiness.md`.

После выкладки вручную проверить desktop/mobile:

1. регистрацию, подтверждение email и восстановление пароля;
2. создание демо-проекта и onboarding;
3. загрузку, просмотр, версию и согласование файла;
4. чат и вложения двумя аккаунтами;
5. импорт и пакет из трёх видео;
6. админскую поддержку и журнал действий.

Production-профилирование по `deploy/load-testing.md` остаётся отдельным
послерелизным gate: без него временные лимиты нельзя рекламировать как
гарантированные.
