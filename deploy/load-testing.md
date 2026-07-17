# Нагрузочное профилирование All As Planned

Тесты запускаются только после резервной копии и отдельного разрешения на проверку
production. Сначала выполняются безопасные чтения, затем загрузки, последней —
реальная FFmpeg-очередь.

## Подготовка

На машине, с которой создаётся нагрузка:

```bash
cd /opt/yt-loader
export AAP_LOAD_BASE_URL='https://allasplanned.ru'
export AAP_LOAD_EMAIL='load-test@allasplanned.ru'
read -rsp 'Пароль тестового аккаунта: ' AAP_LOAD_PASSWORD
export AAP_LOAD_PASSWORD
echo
```

Используется отдельный подтверждённый аккаунт с тестовым проектом. Не передавайте
пароль аргументом командной строки и не сохраняйте JSON-отчёты в Git.

На production параллельно включается сбор метрик:

```bash
cd /opt/yt-loader
chmod +x deploy/server-load-watch.sh
deploy/server-load-watch.sh 600 5 > /tmp/aap-server-load.csv
```

## Последовательность

```bash
python3 deploy/load_profile.py --scenario health --users 1 --iterations 20
python3 deploy/load_profile.py --scenario health --users 5 --iterations 20

python3 deploy/load_profile.py --scenario api --users 1 --iterations 5
python3 deploy/load_profile.py --scenario api --users 3 --iterations 10
python3 deploy/load_profile.py --scenario api --users 5 --iterations 10

python3 deploy/load_profile.py --scenario upload --users 1 --iterations 1 --size-mb 100
python3 deploy/load_profile.py --scenario upload --users 1 --iterations 1 --size-mb 250
python3 deploy/load_profile.py --scenario upload --users 3 --iterations 1 --size-mb 100
```

Сценарий upload удаляет созданный файл после успешной загрузки. После теста всё
равно проверьте медиатеку тестового проекта и размер `server_data`.

## Реальная очередь видео

Создайте локальный файл `/tmp/aap-video-urls.txt` с 3, затем с 20 разрешёнными
тестовыми URL. Сценарий расходует кредиты и создаёт настоящие задания, поэтому без
явного подтверждения не запускается:

```bash
python3 deploy/load_profile.py \
  --scenario queue \
  --url-file /tmp/aap-video-urls.txt \
  --wait \
  --confirm-billable \
  --output /tmp/aap-queue-3.json
```

Проверяются `queue_position`, `queue_wait_seconds`, `processing_seconds`, итоговый
статус и отсутствие двойного списания. Во время теста контролируются readiness,
логи worker, CPU, RAM, swap, disk I/O и свободное место.

## Критерии остановки

Тест немедленно прекращается, если:

- readiness возвращает не `200`;
- свободно менее 15 ГБ или менее 20% диска;
- swap устойчиво растёт, OOM или container restart;
- p95 лёгкого API превышает 1 секунду;
- доля ошибок превышает 1%;
- очередь перестаёт продвигаться или одно задание обрабатывается параллельно дважды.

Итоговые подтверждённые значения переносятся в `SERVER_LIMITS.md`.
